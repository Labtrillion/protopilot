"""에이전트 내부 상태와 갱신 규칙.

- 실제 관측(`observations`)과 가상 예측(`runs`/`impacts`)은 다른 필드·다른 타입으로만 보관한다.
- 도구 결과는 성공 + 새 정보일 때만 상태에 반영한다. 조회 성공 ≠ 정보 갱신 성공.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .cases import ActiveSetpoint
from .common import CheckStatus, DataQuality, Model
from .inputs import Alarm, EventInput, MissingInfo, Observation, SafetyCheck
from .policy import FreshnessPolicy
from .rag import NormalizedRagResult
from .tool_io import (
    ExperimentConditions,
    ExternalComparison,
    ImpactResult,
    RelationEdge,
    SensorQueryPayload,
    SimulationPayload,
    SimulationRun,
    ToolError,
    ToolRequest,
    ToolResult,
    ToolStatus,
)


class Usage(Model):
    decisions: int = 0
    tool_calls: int = 0
    simulations: int = 0


class ToolExecutionRecord(Model):
    tool_request_id: str
    decision_id: str
    action_id: str
    tool_name: str
    snapshot_id: str
    status: ToolStatus
    error: ToolError | None = None
    info_updated: bool = Field(description="상태에 새 정보가 실제로 반영되었는가")
    updated_sensor_ids: list[str] = []
    run_id: str | None = None
    notes: list[str] = []
    state_version_before: int
    state_version_after: int


class DecisionRecord(Model):
    decision_id: str
    state_version: int
    decided_by: Literal["zoro", "policy"]
    decision_kind: str
    selected_option_id: str | None
    action_id: str | None
    valid: bool


class ComparisonView(Model):
    """에이전트 자체 비교 가능성 판정 (+ 외부 비교 결과와 AND)."""

    snapshot_id: str
    baseline_run_id: str | None
    comparable_run_ids: list[str]
    not_comparable: dict[str, str] = Field(description="run_id → 비교 불가 이유")
    metric_deltas: dict[str, dict[str, float]] = Field(
        description="run_id → metric → (후보 - 기준). 단위는 각 metric 의 unit"
    )
    comparable: bool


class AgentState(Model):
    event_id: str
    state_version: int
    sim_time_s: float
    snapshot_id: str
    alarms: list[Alarm]
    observations: list[Observation]  # 실제 관측만
    safety_checks: list[SafetyCheck]
    missing_info: list[MissingInfo]
    active_setpoints: list[ActiveSetpoint] = []
    rag: NormalizedRagResult
    decisions: list[DecisionRecord] = []
    tool_history: list[ToolExecutionRecord] = []
    runs: list[SimulationRun] = []  # 가상 예측
    impacts: list[ImpactResult] = []  # 가상 예측
    relations: list[RelationEdge] = []
    external_comparisons: list[ExternalComparison] = []
    comparison: ComparisonView | None = None
    usage: Usage = Usage()

    @classmethod
    def from_inputs(cls, event: EventInput, rag: NormalizedRagResult) -> "AgentState":
        return cls(
            event_id=event.event_id,
            state_version=event.state_version,
            sim_time_s=event.sim_time_s,
            snapshot_id=event.snapshot_id,
            alarms=list(event.alarms),
            observations=list(event.observations),
            safety_checks=list(event.safety_checks),
            missing_info=list(event.missing_info),
            active_setpoints=list(event.active_setpoints),
            rag=rag,
        )

    # ---- 조회 헬퍼 ----
    def latest_observation(self, sensor_id: str) -> Observation | None:
        obs = [o for o in self.observations if o.sensor_id == sensor_id]
        if not obs:
            return None
        return max(obs, key=lambda o: float("-inf") if o.measured_at_s is None else o.measured_at_s)

    def valve_positions(self) -> dict[str, float]:
        """XMV 최신 관측값(값 있고 품질 bad 아님). 신선도는 따로 판단한다."""
        out = {}
        for sid in {o.sensor_id for o in self.observations if o.sensor_id.startswith("XMV(")}:
            o = self.latest_observation(sid)
            if o and o.value is not None and str(o.quality) != "bad":
                out[sid] = o.value
        return out

    def safety(self, check_id: str) -> SafetyCheck | None:
        return next((s for s in self.safety_checks if s.check_id == check_id), None)

    def runs_for(self, action_id: str, snapshot_id: str | None = None) -> list[SimulationRun]:
        snap = snapshot_id or self.snapshot_id
        return [r for r in self.runs if r.action_id == action_id and r.snapshot_id == snap]

    def succeeded_run(self, action_id: str) -> SimulationRun | None:
        ok = [r for r in self.runs_for(action_id) if r.status == "succeeded"]
        return ok[-1] if ok else None

    def impact_for(self, run_id: str) -> ImpactResult | None:
        return next((i for i in self.impacts if i.run_id == run_id), None)


# ---------------------------------------------------------------- freshness
def sensor_freshness(state: AgentState, sensor_id: str, policy: FreshnessPolicy) -> tuple[CheckStatus, str]:
    o = state.latest_observation(sensor_id)
    if o is None:
        return CheckStatus.FAIL, f"{sensor_id}: 관측 없음"
    if o.value is None or o.quality == DataQuality.BAD:
        return CheckStatus.FAIL, f"{sensor_id}: 유효한 값 없음(quality={o.quality})"
    if o.measured_at_s is None:
        return CheckStatus.UNKNOWN, f"{sensor_id}: 측정 시각 없음 → 신선도 판단 불가"
    age = state.sim_time_s - o.measured_at_s
    limit = policy.max_age(sensor_id)
    if age > limit:
        return CheckStatus.FAIL, f"{sensor_id}: 오래된 값 (age {age:.0f}s > {limit:.0f}s)"
    if o.quality == DataQuality.UNCERTAIN:
        return CheckStatus.UNKNOWN, f"{sensor_id}: 품질 uncertain"
    return CheckStatus.PASS, f"{sensor_id}: 최신 (age {age:.0f}s ≤ {limit:.0f}s)"


# ---------------------------------------------------------------- args
class ArgsError(ValueError):
    pass


def resolve_args(template: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """등록된 틀의 정해진 치환자만 해석한다. 모델 출력은 여기에 들어오지 않는다."""

    def res(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("$"):
            if v == "$snapshot_id":
                return state.snapshot_id
            if v == "$event_id":
                return state.event_id
            for prefix in ("$latest_sample_id:", "$latest_value:"):
                if v.startswith(prefix):
                    sid = v[len(prefix):]
                    o = state.latest_observation(sid)
                    val = None if o is None else (o.sample_id if prefix == "$latest_sample_id:" else o.value)
                    if val is None:
                        raise ArgsError(f"{v}: 값 없음")
                    return val
            raise ArgsError(f"허용되지 않은 치환자 {v}")
        if isinstance(v, list):
            return [res(x) for x in v]
        if isinstance(v, dict):
            return {k: res(x) for k, x in v.items()}
        return v

    return {k: res(v) for k, v in template.items()}


# ---------------------------------------------------------------- apply results
def apply_tool_result(
    state: AgentState, request: ToolRequest, result: ToolResult
) -> ToolExecutionRecord:
    if result.tool_request_id != request.tool_request_id:
        raise ValueError("tool_request_id mismatch")
    before = state.state_version
    notes: list[str] = []
    updated: list[str] = []
    info_updated = False
    run_id: str | None = None

    if result.finished_sim_time_s is not None and result.finished_sim_time_s > state.sim_time_s:
        state.sim_time_s = result.finished_sim_time_s

    p = result.payload
    if isinstance(p, SimulationPayload):
        run_id = p.run.run_id
        info_updated = _apply_simulation(state, request, result.status, p, notes)
    elif result.status != "succeeded":
        notes.append(f"도구 상태 {result.status}: 결과를 관측/정보로 반영하지 않음")
    elif p is None:
        notes.append("성공 응답이나 payload 없음: 반영할 정보 없음")
    elif isinstance(p, SensorQueryPayload):
        updated = _apply_observations(state, request, p, notes)
        info_updated = bool(updated)
        if not updated:
            notes.append("조회는 성공했으나 새 측정값 없음 → 정보 갱신 실패")
    else:  # RelationQueryPayload
        known = {(e.source, e.relation, e.target) for e in state.relations}
        new = [e for e in p.edges if (e.source, e.relation, e.target) not in known]
        state.relations.extend(new)
        info_updated = bool(new)
        notes.append(f"설비 관계 {len(new)}건 추가")

    state.state_version += 1
    rec = ToolExecutionRecord(
        tool_request_id=request.tool_request_id,
        decision_id=request.decision_id,
        action_id=request.action_id,
        tool_name=request.tool_name,
        snapshot_id=request.snapshot_id,
        status=result.status,
        error=result.error,
        info_updated=info_updated,
        updated_sensor_ids=updated,
        run_id=run_id,
        notes=notes,
        state_version_before=before,
        state_version_after=state.state_version,
    )
    state.tool_history.append(rec)
    return rec


def _apply_observations(
    state: AgentState, request: ToolRequest, p: SensorQueryPayload, notes: list[str]
) -> list[str]:
    requested = set(request.args.get("sensor_ids", []))
    updated: list[str] = []
    for o in p.observations:
        sid = o.sensor_id
        if sid not in requested:
            notes.append(f"{sid}: 요청하지 않은 센서 → 무시")
            continue
        if o.value is None or o.quality == DataQuality.BAD:
            notes.append(f"{sid}: 유효값 없음 → 반영 안 함")
            continue
        if o.measured_at_s is None:
            notes.append(f"{sid}: 측정 시각 없음 → 최신값으로 볼 수 없어 반영 안 함")
            continue
        if o.measured_at_s > state.sim_time_s:
            notes.append(f"{sid}: 측정 시각이 현재보다 미래 → 거부")
            continue
        prev = state.latest_observation(sid)
        if prev is not None and prev.measured_at_s is not None and o.measured_at_s <= prev.measured_at_s:
            notes.append(f"{sid}: 기존과 같은/더 오래된 측정(measured_at {o.measured_at_s:.0f}s) → 갱신 아님")
            continue
        if prev is not None and prev.sample_id and o.sample_id == prev.sample_id:
            notes.append(f"{sid}: 같은 시료({o.sample_id}) 재전달 → 갱신 아님")
            continue
        state.observations.append(o)
        updated.append(sid)
        notes.append(f"{sid}: 새 측정 반영 value={o.value} {o.unit} @ {o.measured_at_s:.0f}s")
    if updated:
        state.missing_info = [m for m in state.missing_info if m.sensor_id not in updated]
    return updated


def _apply_simulation(
    state: AgentState, request: ToolRequest, status: ToolStatus, p: SimulationPayload, notes: list[str]
) -> bool:
    run = p.run
    if run.action_id != request.action_id or run.snapshot_id != request.snapshot_id:
        notes.append("run 의 action/snapshot 이 요청과 불일치 → 반영 거부")
        return False
    eff = run.status if status == "succeeded" else status
    run = run.model_copy(update={"status": eff})
    state.runs = [r for r in state.runs if r.run_id != run.run_id] + [run]
    if eff != "succeeded":
        notes.append(f"실험 {run.run_id} 상태 {eff}: 영향 결과를 만들거나 반영하지 않음")
        return False
    if p.impact is None or p.impact.run_id != run.run_id:
        notes.append(f"실험 {run.run_id} 성공했으나 영향 분석 결과 없음/불일치")
        return True
    state.impacts = [i for i in state.impacts if i.run_id != run.run_id] + [p.impact]
    notes.append(f"[가상 예측] 실험 {run.run_id} 결과·후속 영향 반영")
    return True


# ---------------------------------------------------------------- comparison
def _condition_diffs(a: ExperimentConditions, b: ExperimentConditions) -> list[str]:
    da, db = a.model_dump(), b.model_dump()
    return [f"{k}: {da[k]} ≠ {db[k]}" for k in da if da[k] != db[k]]


def assess_comparison(state: AgentState, baseline_action_id: str) -> ComparisonView | None:
    runs = [r for r in state.runs if r.snapshot_id == state.snapshot_id]
    if not runs:
        return None
    base = state.succeeded_run(baseline_action_id)
    base_impact = state.impact_for(base.run_id) if base else None
    reasons: dict[str, str] = {}
    comparable: list[str] = []
    for r in runs:
        if r.action_id == baseline_action_id:
            continue
        if r.status != "succeeded":
            reasons[r.run_id] = f"실험 상태 {r.status}: 결과 없음"
        elif base is None:
            reasons[r.run_id] = "같은 snapshot 의 성공한 기준 실험 없음"
        elif diffs := _condition_diffs(base.conditions, r.conditions):
            reasons[r.run_id] = "실험 조건 불일치 → 동일 조건 비교 아님: " + "; ".join(diffs)
        elif state.impact_for(r.run_id) is None or base_impact is None:
            reasons[r.run_id] = "영향 분석 결과 없음"
        else:
            ext = [
                c for c in state.external_comparisons
                if r.run_id in c.run_ids and c.baseline_run_id == base.run_id and not c.comparable
            ]
            if ext:
                reasons[r.run_id] = "외부 비교 모듈 판정 비교 불가: " + "; ".join(
                    x for c in ext for x in c.reasons_not_comparable
                )
            else:
                comparable.append(r.run_id)
    deltas: dict[str, dict[str, float]] = {}
    for rid in comparable:
        imp = state.impact_for(rid)
        assert imp is not None and base_impact is not None
        deltas[rid] = {
            m: round(v.value - base_impact.metrics[m].value, 6)
            for m, v in imp.metrics.items()
            if m in base_impact.metrics and base_impact.metrics[m].unit == v.unit
        }
    return ComparisonView(
        snapshot_id=state.snapshot_id,
        baseline_run_id=base.run_id if base else None,
        comparable_run_ids=comparable,
        not_comparable=reasons,
        metric_deltas=deltas,
        comparable=base is not None and bool(comparable),
    )
