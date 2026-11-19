"""실제 시뮬레이터 연결 어댑터: ToolRequest → 분기 실행 → ToolResult.

FakeToolkit 자리에 들어간다. 시뮬레이터별 코드는 두 개의 작은 인터페이스로만 받는다.
- BranchRunner.run(snapshot_id, case, horizon_s) → BranchOutcome
  알람 시점 스냅샷에서 상태를 복제하고 case 의 설정값 변경을 적용해 horizon 동안 진행한 시계열.
- LiveObserver.observe(sensor_ids) → (관측 목록, 조회 완료 시뮬레이션 시각)
  라이브 공정의 현재 계측.

채점(비용·정확도·게이트)은 요청에 붙은 EvaluationSpec 으로 benchmark.score_timeseries 가 한다.
그래서 모든 case 가 같은 기준으로 채점되고, 시뮬레이터 쪽은 시계열만 돌려주면 된다.
모델 입력에 들어가면 안 되는 외란·고장 정보는 BranchOutcome 에 담지 않는다.
"""
from __future__ import annotations

from typing import Callable, Protocol

from pydantic import Field

from .benchmark import EvaluationSpec, inside, score_timeseries
from .cases import SimulatorCase
from .common import Model
from .inputs import Observation
from .tool_io import (
    EvidenceSpan,
    ExperimentConditions,
    ImpactResult,
    LimitExceedance,
    MetricValue,
    SensorQueryPayload,
    SimulationPayload,
    SimulationRun,
    ToolError,
    ToolRequest,
    ToolResult,
)
from .benchmark import TrackedVariable


class BranchOutcome(Model):
    """분기 실행 결과. 시각은 분기 시작 기준 초, 표본 키는 'XMEAS(n)' 형식."""

    times_s: list[float]
    samples: list[dict[str, float]]
    tripped: bool
    trip_message: str | None = None
    applied_changes: list[dict] = Field(default=[], description="실제 적용한 설정값 변경과 시각")
    simulator_version: str
    disturbance_profile_id: str = Field(description="분기들이 공유하는 외란 조건의 불투명 식별자. 외란 번호를 넣지 않는다")
    seed: int | None = None
    timeseries_ref: str | None = None
    # 제어 스텝 단위로 적분한 지표 (server/alarm_response.branch_summary, feat/setpoint-decision 정의).
    # 비용은 TEP 내부 운전비, 알람 시간은 AlarmMonitor 의 실제값(true) 채널. 없으면 null.
    operating_cost_usd: float | None = Field(default=None, description="TEP 내부 운전비 적분 [USD]")
    produced_m3: float | None = Field(default=None, description="제어 스텝 적분 생산량 [m3]")
    production_loss_m3: float | None = Field(default=None, description="기준 생산 목표 x horizon - 생산량. 트립 후 구간도 손실")
    alarm_minutes: float | None = Field(default=None, description="AlarmMonitor 실제값 채널의 알람 활성 시간 [min]")
    observed_alarm_minutes: float | None = Field(default=None, description="운전원 화면(센서 오프셋 포함) 기준 [min]")
    alarm_minutes_by_rule: dict[str, float] = {}


class BranchRunner(Protocol):
    def run(self, snapshot_id: str, case: SimulatorCase, horizon_s: float) -> BranchOutcome: ...


class LiveObserver(Protocol):
    def observe(self, sensor_ids: list[str]) -> tuple[list[Observation], float | None]: ...


class SimulationError(RuntimeError):
    """시뮬레이터가 분기를 계산하지 못함. recoverable=False 면 반복 종료."""

    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable


def limit_exceedances(times_s: list[float], samples: list[dict[str, float]],
                      limits: dict[str, tuple[float | None, float | None]], units: dict[str, str]) -> list[LimitExceedance]:
    """경보 한계를 넘은 구간(첫 시각·총 지속·최대 초과량)."""
    out = []
    for tag, (lo, hi) in limits.items():
        if tag not in samples[0]:
            continue
        for side, bound in (("high", hi), ("low", lo)):
            if bound is None:
                continue
            over = [(t, (x[tag] - bound) if side == "high" else (bound - x[tag])) for t, x in zip(times_s, samples)]
            hits = [(t, e) for t, e in over if e > 0]
            if not hits:
                continue
            dt = (times_s[-1] - times_s[0]) / max(len(times_s) - 1, 1)
            out.append(LimitExceedance(sensor_id=tag, limit_name=f"{tag} {side} {bound:g}", limit_value=bound,
                                       unit=units.get(tag, ""), first_exceeded_at_s=hits[0][0],
                                       duration_s=len(hits) * dt, max_excess=max(e for _, e in hits)))
    return out


class SimulationToolkit:
    """ToolExecutor 에 등록할 실제 도구 묶음."""

    def __init__(self, runner: BranchRunner, *, alarm_tags: list[str],
                 alarm_limits: dict[str, tuple[float | None, float | None]], units: dict[str, str],
                 observer: LiveObserver | None = None, run_id_prefix: str = "RUN"):
        self.runner = runner
        self.observer = observer
        self.alarm_tags = alarm_tags
        self.alarm_limits = alarm_limits
        self.units = units
        self.run_id_prefix = run_id_prefix
        self._count = 0

    def tools(self) -> dict[str, Callable[[ToolRequest], ToolResult]]:
        tools = {"run_simulation": self.run_simulation}
        if self.observer is not None:
            tools["sensor_query"] = self.sensor_query
        return tools

    # ------------------------------------------------------------ sensor_query
    def sensor_query(self, req: ToolRequest) -> ToolResult:
        ids = list(req.args.get("sensor_ids", []))
        obs, finished = self.observer.observe(ids)
        got = {o.sensor_id for o in obs}
        return ToolResult(tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="succeeded",
                          payload=SensorQueryPayload(observations=obs, unavailable_sensor_ids=[s for s in ids if s not in got]),
                          finished_sim_time_s=finished, is_virtual=False)

    # ------------------------------------------------------------ run_simulation
    def _failed(self, req: ToolRequest, code: str, message: str, recoverable: bool) -> ToolResult:
        return ToolResult(tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="failed",
                          error=ToolError(code=code, message=message, recoverable=recoverable), is_virtual=True)

    def run_simulation(self, req: ToolRequest) -> ToolResult:
        if req.case is None:
            return self._failed(req, "no_case", "등록 case 없는 시뮬레이션 요청", False)
        snapshot = req.args.get("snapshot_id") or req.snapshot_id
        horizon = float(req.args.get("horizon_s") or (req.evaluation.horizon_s if req.evaluation else 0))
        if horizon <= 0:
            return self._failed(req, "no_horizon", "horizon_s 없음", False)
        try:
            out = self.runner.run(snapshot, req.case, horizon)
        except SimulationError as e:
            return self._failed(req, "simulation_error", str(e), e.recoverable)
        self._count += 1
        run_id = f"{self.run_id_prefix}-{self._count:03d}-{req.action_id}"
        run = SimulationRun(
            run_id=run_id, snapshot_id=snapshot, action_id=req.action_id, case_id=req.case.case_id,
            conditions=ExperimentConditions(snapshot_id=snapshot, horizon_s=horizon,
                                            simulator_version=out.simulator_version,
                                            disturbance_profile_id=out.disturbance_profile_id, seed=out.seed,
                                            initial_composition_sample_id=req.args.get("composition_sample_id")),
            status="succeeded", timeseries_ref=out.timeseries_ref,
            error=out.trip_message if out.tripped else None)
        impact = self._impact(run_id, out, req.evaluation) if len(out.times_s) >= 2 else None
        return ToolResult(tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="succeeded",
                          payload=SimulationPayload(run=run, impact=impact), is_virtual=True)

    def _impact(self, run_id: str, out: BranchOutcome, spec: EvaluationSpec | None) -> ImpactResult:
        t, x = out.times_s, out.samples
        alarm_vars = [TrackedVariable(tag=g, setpoint=0.0, unit="", hi=self.alarm_limits.get(g, (None, None))[1],
                                      lo=self.alarm_limits.get(g, (None, None))[0], source="alarm limit")
                      for g in self.alarm_tags if g in x[0]]
        flags = [all(inside(v, s[v.tag]) for v in alarm_vars) for s in x]
        stable = None
        for i in range(len(flags) - 1, -1, -1):
            if not flags[i]:
                break
            stable = t[i]
        window = spec.final_window_s if spec else 3600.0
        recovered = None if not alarm_vars else (not out.tripped and stable is not None and t[-1] - stable >= window - 1e-6)
        exceed = limit_exceedances(t, x, self.alarm_limits, self.units)
        metrics: dict[str, MetricValue] = {}
        score = None
        if spec is not None:
            try:
                score = score_timeseries(run_id, t, x, spec.tracked, spec, out.tripped,
                                         operating_cost=out.operating_cost_usd,
                                         produced_m3=out.produced_m3 if out.operating_cost_usd is not None else None)
            except (KeyError, ValueError):
                score = None  # 채점에 필요한 계측이 없으면 점수 없음 (만들지 않음)
        if score is not None:
            if score.usd_per_m3 is not None:
                metrics["usd_per_m3"] = MetricValue(value=round(score.usd_per_m3, 6), unit="USD/m3", better="lower")
            metrics["tracking_accuracy"] = MetricValue(value=score.tracking_accuracy, unit="score_0_1", better="higher")
            metrics["product_m3"] = MetricValue(value=round(score.product_m3, 6), unit="m3", better="higher")
        if out.operating_cost_usd is not None:
            metrics["operating_cost_usd"] = MetricValue(value=round(out.operating_cost_usd, 6), unit="USD", better="lower")
        if out.production_loss_m3 is not None:
            metrics["production_loss_m3"] = MetricValue(value=round(out.production_loss_m3, 6), unit="m3", better="lower")
        if out.alarm_minutes is not None:
            metrics["alarm_minutes"] = MetricValue(value=out.alarm_minutes, unit="min", better="lower")
        for tag in self.alarm_tags:
            if tag in x[0]:
                metrics[f"peak_{tag}"] = MetricValue(value=max(s[tag] for s in x), unit=self.units.get(tag, ""), better="lower")
        spans = [EvidenceSpan(run_id=run_id, sensor_id=e.sensor_id, t_start_s=e.first_exceeded_at_s,
                              t_end_s=min(t[-1], e.first_exceeded_at_s + e.duration_s)) for e in exceed]
        return ImpactResult(run_id=run_id, original_alarm_recovered=recovered,
                            recovery_time_s=stable if recovered else None,
                            affected_sensors=sorted({e.sensor_id for e in exceed}), exceedances=exceed,
                            metrics=metrics, evidence_spans=spans, score=score)
