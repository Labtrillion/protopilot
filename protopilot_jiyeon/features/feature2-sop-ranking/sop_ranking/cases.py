"""시뮬레이터에 넘길 대응 case(설정값 변경)와 감독기 허용 조작 목록.

TEP Studio 계약(zoro-rag/STUDIO.md 'Zoro 입력, 출력, 실행 경계')을 따른다.
- Zoro 출력은 hold 또는 propose_setpoint. 하위 P·PI 제어와 보호 기능은 그대로 두고 상위 목표만 바꾼다.
- Zoro-1 은 등록된 선택지 중 하나만 고른다. 목표값·대상 루프·원복 조건은 모델이 만들지 않고
  여기 등록된 case 에서만 나온다. 밸브(XMV) 직접 명령은 등록할 수 없다.
- 시뮬레이터 어댑터는 ResolvedSetpointChange 를 적용한다: tep-rust 는 Scheme::set_setpoint(scheme_index, value),
  ProtoPilot(tep-studio Ricker 제어기)는 setpoints 의 setpoint_id 필드(예: reactor_pressure)를 바꾼다.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .common import Model
from server.tep_control import cascade_response, top_level_setpoints

CostItem = Literal["purge", "product", "compressor", "steam"]
CostDirection = Literal["increase", "decrease", "no_change", "unknown"]
ValuesStatus = Literal["example", "agreed"]


class SetpointSpec(Model):
    """감독기가 바꿀 수 있도록 허용한 상위 목표 하나 (= STUDIO 의 allowed_actions 항목)."""

    setpoint_id: str = Field(description="예: 'SETPT(19)'")
    scheme_index: int | None = Field(default=None, description="tep-rust Scheme::set_setpoint index. 다른 시뮬레이터는 null")
    loop_id: str = Field(description="대상 상위 루프. 예: 'CONTRL19'")
    controlled_tag: str = Field(description="루프가 추종하는 계측. 예: 'XMEAS(30)'")
    description: str
    unit: str
    default_value: float = Field(description="Studio PRESET 기동값")
    allowed_min: float
    allowed_max: float
    max_step: float | None = Field(description="현재값 대비 1회 최대 변경폭 (unit). null = 제한 없음")
    max_rate_per_h: float | None = Field(default=None, description="램프 최대 속도 (unit/h). null = 계단 변경 허용")
    min_wait_s: float | None = Field(default=None, description="직전 변경 뒤 최소 대기 [s]. null = 제한 없음")
    values_status: ValuesStatus = Field(description="example = 예시 설정(검증된 운전 기준 아님)")
    source: str = Field(description="기본값·루프 매핑 출처")

    @model_validator(mode="after")
    def _range(self) -> "SetpointSpec":
        if not self.allowed_min <= self.default_value <= self.allowed_max:
            raise ValueError(f"{self.setpoint_id}: default_value 가 허용 범위 밖")
        return self


class SetpointChange(Model):
    """case 안의 변경 한 건 (카탈로그 작성자가 정한 값)."""

    setpoint_id: str
    target_value: float
    unit: str
    apply_at_s: float = Field(default=0.0, description="run 시작(=snapshot 시각) 기준 적용 시각 [s]")
    ramp_rate_per_h: float | None = Field(default=None, description="null = 계단 변경")


class SopRef(Model):
    sop_id: str
    version: str
    step_id: str


class RevertRule(Model):
    """원복(또는 hold 복귀) 조건. 감독기가 평가한다."""

    rule_id: str
    description: str
    signal: str | None = Field(default=None, description="판정 계측. null = 이벤트형(보호 작동 등)")
    op: Literal[">=", "<=", ">", "<", "event"]
    threshold: float | None = None
    unit: str | None = None


class ResponseCase(Model):
    """시나리오별 대응 case. Zoro 는 이 case 를 시험·권고할지 고를 뿐 내용을 바꾸지 않는다."""

    case_id: str
    policy: Literal["hold", "propose_setpoint"]
    description: str
    setpoint_changes: list[SetpointChange] = []
    sop_refs: list[SopRef] = Field(description="근거 SOP 단계")
    watch_signals: list[str] = Field(description="적용 후 확인할 신호")
    review_after_s: float = Field(description="적용 시각 기준 재검토까지 [s]")
    revert_rules: list[RevertRule] = []
    expected_cost_direction: dict[CostItem, CostDirection] = Field(
        description="카탈로그 작성자의 사전 가설. 시뮬레이션·실측 결과가 아님")
    expected_direction_basis: str | None = Field(default=None, description="가설 근거. null = 근거 없음")
    values_status: ValuesStatus

    @model_validator(mode="after")
    def _shape(self) -> "ResponseCase":
        if self.policy == "hold" and self.setpoint_changes:
            raise ValueError(f"{self.case_id}: hold case 에 설정값 변경이 있음")
        if self.policy == "propose_setpoint" and not self.setpoint_changes:
            raise ValueError(f"{self.case_id}: propose_setpoint case 에 변경이 없음")
        ids = [c.setpoint_id for c in self.setpoint_changes]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.case_id}: 같은 설정값 중복 변경")
        return self


class ActiveSetpoint(Model):
    """현재 적용 중인 상위 목표 (경계 A, 장하 제공)."""

    setpoint_id: str
    value: float
    unit: str
    since_s: float | None = Field(description="이 값이 적용된 시뮬레이션 시각 [s]. null = 모름")


class ControllerStep(Model):
    """Studio 제어 코드로 계산한 하위 루프 즉시 변화 (측정값이 아직 그대로라는 가정)."""

    loop: str
    control: Literal["P", "PI"]
    period_s: int
    target: str = Field(description="SETPT(n) 또는 XMV(n)")
    immediate_delta: float
    integral_per_run: float | None = Field(description="오차가 유지될 때 실행 1회당 추가 변화. P 전용이면 null")
    value_before: float | None = Field(default=None, description="XMV 의 현재 관측값. null = 미제공")
    value_after: float | None = None


class ResolvedSetpointChange(Model):
    """시뮬레이터/감독기로 그대로 전달되는 변경 한 건."""

    setpoint_id: str
    scheme_index: int | None
    loop_id: str
    controlled_tag: str
    from_value: float | None = Field(description="변경 전 값. null = 현재값 미제공")
    from_source: Literal["active_setpoint", "unknown"]
    target_value: float
    unit: str
    apply_at_s: float
    ramp_rate_per_h: float | None
    allowed_min: float
    allowed_max: float
    controller_response: list[ControllerStep] = Field(
        default=[], description="tep_control.cascade_response 계산. 현재값을 모르면 빈 목록")


class SimulatorCase(Model):
    """ToolRequest 로 시뮬레이터 어댑터에 넘기는 case."""

    case_id: str
    policy: Literal["hold", "propose_setpoint"]
    setpoint_changes: list[ResolvedSetpointChange]
    other_setpoints: Literal["unchanged"] = "unchanged"
    lower_loops_and_protection: Literal["unchanged"] = "unchanged"
    values_status: ValuesStatus


_SETPT = re.compile(r"^SETPT\((\d+)\)$")


def controller_steps(setpoint_id: str, delta: float, valves: dict[str, float] | None = None) -> list[ControllerStep]:
    """최상위 SETPT 변경의 하위 루프 즉시 변화. Studio 루프 목표가 아니면 빈 목록."""
    m = _SETPT.match(setpoint_id)
    if not m or int(m[1]) not in top_level_setpoints():
        return []
    out = []
    for k in cascade_response(int(m[1]), delta):
        before = (valves or {}).get(k.target) if k.target.startswith("XMV") else None
        out.append(ControllerStep(loop=f"CONTRL{k.loop}", control=k.control, period_s=k.period_s, target=k.target,
                                  immediate_delta=round(k.delta, 6),
                                  integral_per_run=None if k.integral_per_run is None else round(k.integral_per_run, 6),
                                  value_before=before, value_after=None if before is None else round(before + k.delta, 4)))
    return out


def resolve_case(case: ResponseCase, specs: dict[str, SetpointSpec],
                 active: list[ActiveSetpoint], valves: dict[str, float] | None = None) -> SimulatorCase:
    cur = {a.setpoint_id: a for a in active}
    out = []
    for ch in case.setpoint_changes:
        sp = specs[ch.setpoint_id]
        a = cur.get(ch.setpoint_id)
        resp = controller_steps(sp.setpoint_id, ch.target_value - a.value, valves) if a else []
        out.append(ResolvedSetpointChange(controller_response=resp, 
            setpoint_id=sp.setpoint_id, scheme_index=sp.scheme_index, loop_id=sp.loop_id,
            controlled_tag=sp.controlled_tag, from_value=a.value if a else None,
            from_source="active_setpoint" if a else "unknown", target_value=ch.target_value, unit=ch.unit,
            apply_at_s=ch.apply_at_s, ramp_rate_per_h=ch.ramp_rate_per_h,
            allowed_min=sp.allowed_min, allowed_max=sp.allowed_max))
    status = "agreed" if case.values_status == "agreed" and all(
        specs[c.setpoint_id].values_status == "agreed" for c in case.setpoint_changes) else "example"
    return SimulatorCase(case_id=case.case_id, policy=case.policy, setpoint_changes=out, values_status=status)


def envelope_problems(case: ResponseCase, specs: dict[str, SetpointSpec], active: list[ActiveSetpoint],
                      sim_time_s: float, valves: dict[str, float] | None = None) -> tuple[list[str], list[str]]:
    """현재 상태 기준 감독기 허용 검사. (hard 위반, 판단 불가) 를 돌려준다."""
    hard, unknown = [], []
    cur = {a.setpoint_id: a for a in active}
    for ch in case.setpoint_changes:
        sp = specs[ch.setpoint_id]
        a = cur.get(ch.setpoint_id)
        if a is None:
            unknown.append(f"{sp.setpoint_id} 현재값 미제공")
            continue
        if a.unit != sp.unit:
            hard.append(f"{sp.setpoint_id} 현재값 단위 {a.unit} ≠ {sp.unit}")
            continue
        step = abs(ch.target_value - a.value)
        if step == 0:
            hard.append(f"{sp.setpoint_id} 이미 {a.value} {sp.unit} — 변경 없음")
        elif sp.max_step is not None and step > sp.max_step + 1e-9:
            hard.append(f"{sp.setpoint_id} 변경폭 {step:g} > 최대 {sp.max_step:g} {sp.unit}")
        if sp.min_wait_s is not None:
            if a.since_s is None:
                unknown.append(f"{sp.setpoint_id} 직전 변경 시각 미제공")
            elif sim_time_s - a.since_s < sp.min_wait_s:
                unknown.append(f"{sp.setpoint_id} 직전 변경 후 {sim_time_s - a.since_s:g}s < 대기 {sp.min_wait_s:g}s")
        # Studio 코드: 최종 밸브가 0/100% 에 닿으면 CONSHAND 가 잘라 루프가 더 대응할 수 없다
        for k in controller_steps(sp.setpoint_id, ch.target_value - a.value, valves):
            if not k.target.startswith("XMV"):
                continue
            if k.value_before is None:
                unknown.append(f"{k.target} 현재값 미제공 → 즉시 변화 {k.immediate_delta:+.2f}%p 의 포화 여부 판단 불가")
            elif not 0.0 <= k.value_after <= 100.0:
                hard.append(f"{k.target} {k.value_before:g}% {k.immediate_delta:+.2f}%p → {k.value_after:g}% (밸브 포화)")
    return hard, unknown
