"""도구 실행 요청(경계 C)과 도구 결과(경계 A-3/A-4: 장하·동은 → 에이전트)."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from .benchmark import EvaluationSpec, RunScore
from .cases import SimulatorCase
from .common import Model
from .inputs import Observation

RunStatus = Literal["queued", "running", "succeeded", "failed", "aborted"]
ToolStatus = Literal["succeeded", "failed", "aborted", "running"]


class ToolRequest(Model):
    """에이전트 → 백엔드/도구. 인자는 등록된 args_template 으로만 구성된다."""

    tool_request_id: str
    decision_id: str
    event_id: str
    state_version: int = Field(description="요청을 만든 시점의 상태 버전")
    action_id: str
    tool_name: str
    case: SimulatorCase | None = Field(default=None, description="simulation 일 때 시뮬레이터에 넘길 대응 case")
    args: dict[str, Any]
    evaluation: EvaluationSpec | None = Field(default=None, description="simulation 채점 명세. 모든 case 에 동일")
    snapshot_id: str
    issued_sim_time_s: float


class ToolError(Model):
    code: str
    message: str
    recoverable: bool = Field(description="false 이면 반복을 종료하고 운영자 검토 요청")


# ---------- 결과 payload ----------
class SensorQueryPayload(Model):
    kind: Literal["sensor_query"] = "sensor_query"
    observations: list[Observation] = []
    unavailable_sensor_ids: list[str] = []
    note: str | None = None


class RelationEdge(Model):
    source: str
    relation: str
    target: str
    evidence_ids: list[str] = []


class RelationQueryPayload(Model):
    kind: Literal["relation_query"] = "relation_query"
    edges: list[RelationEdge] = []


class ExperimentConditions(Model):
    """비교 가능성 판단 키. 하나라도 다르면 동일 조건 A/B 비교가 아니다."""

    snapshot_id: str
    horizon_s: float
    simulator_version: str
    disturbance_profile_id: str
    seed: int | None = Field(default=None, description="시뮬레이터 시드. null = 미제공")
    initial_composition_sample_id: str | None = Field(description="초기 조건에 쓴 조성 시료. null = 미지정")


class SimulationRun(Model):
    kind: Literal["simulation_prediction"] = "simulation_prediction"
    run_id: str
    snapshot_id: str
    action_id: str
    case_id: str | None
    conditions: ExperimentConditions
    status: RunStatus
    timeseries_ref: str | None = Field(default=None, description="센서 시계열 저장 위치. null = 없음")
    error: str | None = None


class LimitExceedance(Model):
    sensor_id: str
    equipment_id: str | None = None
    limit_name: str
    limit_value: float
    unit: str
    first_exceeded_at_s: float = Field(description="run 시작 기준 [s]")
    duration_s: float
    max_excess: float = Field(description="한계 대비 최대 초과량 (unit 과 동일 단위)")


class MetricValue(Model):
    value: float
    unit: str
    better: Literal["lower", "higher"] | None = None


class EvidenceSpan(Model):
    run_id: str
    sensor_id: str
    t_start_s: float
    t_end_s: float


class ImpactResult(Model):
    """동은 모듈 출력: 한 run 의 후속 영향. 모두 가상 예측."""

    kind: Literal["simulation_prediction"] = "simulation_prediction"
    run_id: str
    original_alarm_recovered: bool | None = Field(description="null = 판단 불가")
    recovery_time_s: float | None = Field(description="run 시작 기준 [s]. null = 회복 안 됨/판단 불가")
    affected_equipment: list[str] = []
    affected_sensors: list[str] = []
    exceedances: list[LimitExceedance] = []
    metrics: dict[str, MetricValue] = {}
    evidence_spans: list[EvidenceSpan] = []
    score: RunScore | None = Field(default=None, description="벤치마크 점수(비용 USD/m³·정확도·게이트). 가상 예측")


class SimulationPayload(Model):
    kind: Literal["simulation"] = "simulation"
    run: SimulationRun
    impact: ImpactResult | None = None


ToolPayload = Annotated[
    SensorQueryPayload | RelationQueryPayload | SimulationPayload, Field(discriminator="kind")
]


class ToolResult(Model):
    tool_request_id: str
    tool_name: str
    status: ToolStatus
    payload: ToolPayload | None = None
    error: ToolError | None = None
    finished_sim_time_s: float | None = Field(
        default=None, description="실제 공정 조회 완료 시각. 가상 실험은 공정 시간을 진행시키지 않으므로 null"
    )
    is_virtual: bool = Field(description="가짜 도구/가상 결과이면 true")


class ExternalComparison(Model):
    """동은 모듈의 기준/A/B 비교 결과(선택 입력). 에이전트는 자체 조건 검사와 AND 로 결합한다."""

    comparison_id: str
    baseline_run_id: str
    run_ids: list[str]
    comparable: bool
    reasons_not_comparable: list[str] = []
