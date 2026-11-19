"""행동·도구 등록 (정민 정의 + 장하/지연 도구 연결).

모델은 action_id(=선택지 ID)만 고른다. 실행 인자는 여기 등록된 args_template 에서만 만든다.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .cases import ResponseCase, SetpointSpec
from .common import Model


class ActionKind(StrEnum):
    SENSOR_QUERY = "sensor_query"
    RELATION_QUERY = "relation_query"
    SIMULATION = "simulation"
    HUMAN_REVIEW = "human_review"


class ConditionKind(StrEnum):
    SENSOR_FRESH = "sensor_fresh"  # sensor_id 최신값이 신선한가
    SAFETY_CHECK = "safety_check"  # 외부 규칙 check_id 결과
    RUN_SUCCEEDED = "run_succeeded"  # action_id 실험이 현재 snapshot 에서 성공했는가


class ConditionSpec(Model):
    condition_id: str
    description: str
    kind: ConditionKind
    sensor_id: str | None = None
    check_id: str | None = None
    action_id: str | None = None


class RerunPolicy(Model):
    scope: Literal["per_snapshot", "unlimited"] = Field(
        default="per_snapshot",
        description="per_snapshot: 같은 snapshot 에서 성공/진행 중이면 재실행 금지. unlimited: 성공해도 재실행 허용",
    )
    retry_after_failure: bool = True
    max_runs: int | None = Field(default=None, description="해당 범위 내 최대 시도 수. null = 무제한")


class ActionSpec(Model):
    action_id: str
    description: str
    kind: ActionKind
    tool_name: str | None = Field(description="실행 도구 이름. human_review 는 null")
    supported: bool = Field(description="실행 기능이 실제 구현·연결되었는가")
    case_id: str | None = Field(default=None, description="simulation 이 시험할 등록 대응 case. 지원되는 simulation 은 필수")
    args_template: dict[str, Any] = Field(
        default={},
        description="도구 인자 틀. '$snapshot_id', '$event_id', '$latest_sample_id:<sensor>', '$latest_value:<sensor>' 만 치환",
    )
    preconditions: list[str] = []
    required_fresh_sensors: list[str] = []
    target_sensors: list[str] = Field(default=[], description="sensor_query 대상. 모두 신선하면 옵션 닫힘")
    rerun: RerunPolicy = RerunPolicy()
    requires_rag_evidence: bool = False
    offer_without_rag: bool = Field(default=False, description="RAG 후보에 없어도 기본 제공")
    recommendable: bool = Field(default=False, description="이 실험 결과를 근거로 최종 권고 옵션을 만들 수 있는가")


class ActionCatalog(Model):
    schema_version: Literal["action_catalog.v1"] = "action_catalog.v1"
    label: str
    setpoints: list[SetpointSpec] = Field(default=[], description="감독기 허용 상위 목표 목록")
    cases: list[ResponseCase] = Field(default=[], description="시뮬레이터에 넘길 대응 case 목록")
    actions: list[ActionSpec]
    conditions: list[ConditionSpec]
    aliases: dict[str, str] = Field(default={}, description="RAG 원본 참조 → action_id (팀 합의 표). 그 외 매핑 금지")
    condition_aliases: dict[str, str] = {}

    @model_validator(mode="after")
    def _check_refs(self) -> "ActionCatalog":
        ids = [a.action_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action_id")
        cids = {c.condition_id for c in self.conditions}
        for a in self.actions:
            for p in a.preconditions:
                if p not in cids:
                    raise ValueError(f"{a.action_id}: undefined precondition {p}")
        sps = {sp.setpoint_id: sp for sp in self.setpoints}
        if len(sps) != len(self.setpoints):
            raise ValueError("duplicate setpoint_id")
        cases = {c.case_id: c for c in self.cases}
        if len(cases) != len(self.cases):
            raise ValueError("duplicate case_id")
        for c in self.cases:
            for ch in c.setpoint_changes:
                sp = sps.get(ch.setpoint_id)
                if sp is None:
                    raise ValueError(f"{c.case_id}: 허용 목록에 없는 설정값 {ch.setpoint_id}")
                if ch.unit != sp.unit:
                    raise ValueError(f"{c.case_id}: {ch.setpoint_id} 단위 {ch.unit} ≠ {sp.unit}")
                if not sp.allowed_min <= ch.target_value <= sp.allowed_max:
                    raise ValueError(f"{c.case_id}: {ch.setpoint_id}={ch.target_value} 허용 범위 밖")
                if ch.ramp_rate_per_h is not None and sp.max_rate_per_h is not None                         and ch.ramp_rate_per_h > sp.max_rate_per_h:
                    raise ValueError(f"{c.case_id}: {ch.setpoint_id} 램프 속도 초과")
        for a in self.actions:
            if a.case_id is not None and a.case_id not in cases:
                raise ValueError(f"{a.action_id}: undefined case {a.case_id}")
            if a.kind == ActionKind.SIMULATION and a.supported and a.case_id is None:
                raise ValueError(f"{a.action_id}: 지원되는 simulation 에 case_id 없음")
        for raw, target in self.aliases.items():
            if target not in ids:
                raise ValueError(f"alias {raw!r} -> unknown action {target}")
        for raw, target in self.condition_aliases.items():
            if target not in cids:
                raise ValueError(f"condition alias {raw!r} -> unknown condition {target}")
        return self

    def get(self, action_id: str | None) -> ActionSpec | None:
        return next((a for a in self.actions if a.action_id == action_id), None)

    def condition(self, condition_id: str) -> ConditionSpec | None:
        return next((c for c in self.conditions if c.condition_id == condition_id), None)

    def case(self, case_id: str | None) -> ResponseCase | None:
        return next((c for c in self.cases if c.case_id == case_id), None)

    def setpoint_map(self) -> dict[str, SetpointSpec]:
        return {sp.setpoint_id: sp for sp in self.setpoints}

    def human_review_action(self) -> ActionSpec | None:
        return next((a for a in self.actions if a.kind == ActionKind.HUMAN_REVIEW), None)

    def resolve_action_ref(self, raw_ref: str) -> tuple[str | None, Literal["exact", "alias", "unmapped"]]:
        """정확 일치 또는 합의된 별칭만 허용. 유사도/추측 매핑은 하지 않는다."""
        if self.get(raw_ref):
            return raw_ref, "exact"
        if raw_ref in self.aliases:
            return self.aliases[raw_ref], "alias"
        return None, "unmapped"

    def resolve_condition_ref(self, raw_ref: str) -> str | None:
        if self.condition(raw_ref):
            return raw_ref
        return self.condition_aliases.get(raw_ref)
