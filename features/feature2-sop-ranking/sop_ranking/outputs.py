"""경계 C: 에이전트 → 백엔드·화면 (영하)."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .benchmark import CaseSelection
from .cases import CostDirection, CostItem, ResolvedSetpointChange, RevertRule, SopRef, ValuesStatus
from .common import Model
from .options import OptionSet
from .rag import Evidence
from .state import AgentState, Usage
from .tool_io import ToolRequest, ToolResult
from zoro.schemas import ZoroRawResponse, ZoroRequest

from .decision import Decision


class LogStage(StrEnum):
    INPUT_RECEIVED = "input_received"
    OPTIONS_BUILT = "options_built"
    MODEL_REQUESTED = "model_requested"
    MODEL_SELECTED = "model_selected"  # 모델 선택 (검증 통과)
    DECISION_REJECTED = "decision_rejected"  # 모델 선택 거부/응답 무효
    POLICY_DECISION = "policy_decision"  # 모델 호출 없이 정책으로 결정
    TOOL_STARTED = "tool_started"
    TOOL_SUCCEEDED = "tool_succeeded"
    TOOL_FAILED = "tool_failed"
    TOOL_RUNNING = "tool_running"
    STATE_UPDATED = "state_updated"
    TERMINATED = "terminated"


class LogEntry(Model):
    seq: int
    event_id: str
    state_version: int
    stage: LogStage
    summary: str
    decision_id: str | None = None
    tool_request_id: str | None = None
    run_id: str | None = None
    data: dict[str, Any] = {}
    sim_time_s: float
    logged_at: datetime = Field(description="벽시계 UTC")


class TerminationReason(StrEnum):
    RECOMMENDATION_READY = "recommendation_ready"
    HUMAN_REVIEW_SELECTED = "human_review_selected"
    LIMIT_REACHED = "limit_reached"
    UNRECOVERABLE_TOOL_ERROR = "unrecoverable_tool_error"
    NO_VALID_OPTIONS = "no_valid_options"
    MODEL_RESPONSE_INVALID = "model_response_invalid"
    MODEL_CALL_FAILED = "model_call_failed"


class ProgressStatus(Model):
    event_id: str
    state_version: int
    iteration: int
    phase: Literal["options_built", "awaiting_model", "running_tool", "state_updated", "finished"]
    usage: Usage
    remaining: dict[str, int]
    open_option_ids: list[str] = []
    deferred_option_ids: list[str] = []
    last_decision_id: str | None = None
    finished: bool = False
    termination_reason: TerminationReason | None = None


class RationaleItem(Model):
    statement: str
    basis: Literal["observation", "policy", "experiment", "rag_evidence", "safety_check", "agent_state"]
    refs: list[str] = []


class SimulatedEffect(Model):
    """기준(hold) 대비 가상 예측 차이. 실제 절감액이 아님."""

    metric: str
    delta: float = Field(description="후보 - 기준")
    unit: str
    run_id: str
    baseline_run_id: str


class SetpointCommand(Model):
    """감독기·시뮬레이터 어댑터가 적용할 상위 목표 명령 (STUDIO.md 'Zoro 출력' 계약).

    내용은 모두 등록 case 에서 온다. 모델이 만든 값은 없다. 적용 여부는 감독기가 결정한다.
    """

    decision: Literal["hold", "propose_setpoint"]
    case_id: str | None = Field(description="null = 등록 hold case 없음(현재 목표 유지)")
    reason: str
    setpoint_changes: list[ResolvedSetpointChange] = Field(description="hold 이면 빈 목록")
    sop_refs: list[SopRef]
    expected_cost_direction: dict[CostItem, CostDirection] = Field(
        description="카탈로그 사전 가설(case 등록자 작성). 시뮬레이션·실측 결과 아님")
    expected_direction_basis: str | None
    simulated_effects: list[SimulatedEffect] = Field(description="기준 대비 가상 예측 차이")
    actual_effects: None = Field(default=None, description="실제 적용 후 평가기가 기록. 에이전트는 채우지 않음")
    watch_signals: list[str]
    issued_sim_time_s: float
    review_at_sim_time_s: float | None = Field(description="재검토 시각 [s]. null = 정해진 재검토 없음")
    revert_rules: list[RevertRule]
    values_status: ValuesStatus = Field(description="example = 예시 설정값(검증 기준 아님)")
    requires_supervisor_approval: Literal[True] = True


class FinalOutput(Model):
    event_id: str
    state_version: int
    outcome: Literal["recommendation", "human_review"]
    termination_reason: TerminationReason
    recommended_action_id: str | None = Field(description="null = 권고 없음(운영자 검토)")
    setpoint_command: SetpointCommand = Field(description="시뮬레이터/감독기로 넘길 hold 또는 propose_setpoint")
    summary: str
    sop_route: Literal["PROVIDE_SOP", "REQUEST_INFO", "RETRIEVE_AGAIN", "ESCALATE"] | None = None
    sop_sources: list[Evidence] = []
    requested_sensor_ids: list[str] = []
    rationale: list[RationaleItem]
    rationale_note: str = "근거는 에이전트가 상태·정책·실험 결과로 구성한 것이며 모델이 생성한 설명이 아님"
    uncertainties: list[str]
    compared_run_ids: list[str] = []
    case_selection: CaseSelection | None = Field(
        default=None, description="심슨(hold) 대비 case 탐색 기록과 종료 판정. 가상 예측 기반")
    selection_distribution: dict[str, float] | None = Field(
        default=None, description="마지막 결정의 행동 선택 분포. 안전/회복 확률 아님"
    )
    model_client_kind: Literal["real", "fake"] | None
    tools_virtual: bool
    simulation_results_are_predictions: Literal[True] = True
    disclaimer: str


class ZoroExchange(Model):
    request_id: str
    decision_id: str
    state_version: int
    options: OptionSet
    request: ZoroRequest
    raw_response: ZoroRawResponse | None


class RunReport(Model):
    final: FinalOutput
    decisions: list[Decision]
    exchanges: list[ZoroExchange]
    tool_requests: list[ToolRequest]
    tool_results: list[ToolResult]
    progress: list[ProgressStatus]
    log: list[LogEntry]
    final_state: AgentState
    sop_stage: RunReport | None = None
