"""경계 B: 에이전트 ↔ Zoro-1.

공식 Typed Decision 형식 (https://huggingface.co/trillionlabs/Zoro-1, 2026-11-19 확인)
  요청: {"state": str|object, "questions": {qid: {"type": "choice", "instructions": str,
                                                   "criteria": {option: description}}}}
  응답: {"answers": {qid: {"type": "choice", "choice": str, "confidence": float,
                            "probabilities": {option: float}}}}
  서버(trida-stack `serve.py --mode decide`): POST /v1/decide  body {"request": <요청>, "reasoning": false}
  거부(refusal) 또는 답 누락은 "답 없음"으로 보고 fail-closed 처리한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, ValidationError

from zoro.client import HttpZoroClient, ZoroClient, ZoroClientError, ZoroHttpConfig
from zoro.schemas import ChoiceAnswer, ChoiceQuestion, NoulQuestion, ScoreQuestion, ZoroRawResponse, ZoroRequest

from .benchmark import evaluation_spec
from .cases import resolve_case
from .common import Model, find_forbidden_keys
from .options import OptionKind, OptionSet, OptionStatus, case_selection
from .policy import AgentPolicy
from .registry import ActionCatalog
from .state import AgentState, sensor_freshness
from .tool_io import ToolRequest

QUESTION_ID = "next_action"
INSTRUCTIONS = (
    "현재 사건 상태, 실제 관측, 가상 실험 결과와 이전 판단 이력을 보고 "
    "다음 분석 또는 권고 행동 하나를 선택하라. criteria 에 있는 선택지만 고를 수 있다."
)


# ---------------------------------------------------------------- schemas
class SelectionDistribution(Model):
    meaning: Literal["action_selection_distribution"] = "action_selection_distribution"
    note: str = "모델의 행동 선택 분포. 안전 확률·회복 확률·정답률이 아님"
    probabilities: dict[str, float]
    confidence: float


class EvidenceRef(Model):
    ref_type: Literal["rag_evidence", "run", "comparison", "observation", "safety_check", "policy_check"]
    ref_id: str
    note: str | None = None


class DecisionValidation(Model):
    valid: bool
    errors: list[str] = []
    warnings: list[str] = []
    allowed_option_ids: list[str]


class Decision(Model):
    """원본 응답을 검증·변환한 내부 결정."""

    decision_id: str
    event_id: str
    state_version: int
    request_id: str | None
    decided_by: Literal["zoro", "policy"]
    client_kind: Literal["real", "fake"] | None
    decision_kind: Literal["tool_call", "recommendation", "human_review"]
    selected_option_id: str | None = Field(description="검증 통과한 선택. 거부 시 null")
    model_choice_raw: str | None = Field(default=None, description="모델이 실제로 고른 값(거부된 경우 포함)")
    action_id: str | None
    tool_request: ToolRequest | None = None
    evidence_refs: list[EvidenceRef] = []
    selection_distribution: SelectionDistribution | None = None
    validation: DecisionValidation


# ---------------------------------------------------------------- request builder
class LeakError(RuntimeError):
    pass


def build_zoro_request(state: AgentState, options: OptionSet, catalog: ActionCatalog, policy: AgentPolicy,
                       *, sop_only: bool = False) -> ZoroRequest:
    latest = []
    for sid in dict.fromkeys(o.sensor_id for o in state.observations):
        o = state.latest_observation(sid)
        assert o is not None
        st, detail = sensor_freshness(state, sid, policy.freshness)
        latest.append({
            "sensor_id": sid, "value": o.value, "unit": o.unit, "measured_at_s": o.measured_at_s,
            "age_s": None if o.measured_at_s is None else round(state.sim_time_s - o.measured_at_s, 1),
            "quality": str(o.quality), "freshness": str(st), "sample_id": o.sample_id,
        })
    ev_used = {e for o in options.options for e in o.evidence_ids}
    history = []
    for d in state.decisions:
        t = next((h for h in state.tool_history if h.decision_id == d.decision_id), None)
        history.append({
            "decision_id": d.decision_id, "selected": d.selected_option_id, "kind": d.decision_kind,
            "tool_status": t.status if t else None, "info_updated": t.info_updated if t else None,
            "outcome": "; ".join(t.notes) if t else None,
        })
    runs = []
    for r in state.runs:
        imp = state.impact_for(r.run_id)
        runs.append({
            "run_id": r.run_id, "action_id": r.action_id, "case_id": r.case_id, "status": r.status,
            "conditions": r.conditions.model_dump(),
            "impact": None if imp is None else {
                "original_alarm_recovered": imp.original_alarm_recovered,
                "recovery_time_s": imp.recovery_time_s,
                "exceedances": [e.model_dump() for e in imp.exceedances],
                "affected_equipment": imp.affected_equipment,
                "metrics": {k: v.model_dump() for k, v in imp.metrics.items()},
                "score": imp.score.model_dump(exclude={"run_id"}) if imp.score else None,
            },
        })
    b = policy.budget
    st = {
        "event": {"event_id": state.event_id, "state_version": state.state_version,
                  "sim_time_s": state.sim_time_s, "time_basis": "simulation_seconds",
                  "snapshot_id": state.snapshot_id},
        "active_alarms": [a.model_dump() for a in state.alarms if a.active],
        "active_setpoints": [a.model_dump() for a in state.active_setpoints],
        "actual_observations": {"note": "실제 공정 측정값(최신)", "latest": latest},
        "information_gaps": [m.model_dump() for m in state.missing_info],
        "safety_checks": [s.model_dump() for s in state.safety_checks],
        "sop_evidence": [
            {"evidence_id": e.evidence_id, "doc_id": e.doc_id, "doc_version": e.doc_version,
             "sop_id": e.sop_id, "sop_version": e.sop_version, "clause": e.clause, "excerpt": e.text[:300]}
            for e in state.rag.evidence if sop_only or e.evidence_id in ev_used
        ],
        "rag_unresolved": [u.model_dump() for u in state.rag.unresolved],
        "history": history,
        "virtual_experiments": {
            "note": "시뮬레이션 예측값(가상). 실제 관측 아님. 시각은 run 시작 기준 [s]",
            "runs": runs,
            "comparison": state.comparison.model_dump() if state.comparison else None,
            "case_selection": (sel.model_dump() if (sel := case_selection(state, catalog, policy)) else None),
        },
        "unavailable_options": [
            {"option_id": o.option_id, "status": str(o.status), "reason": o.reason}
            for o in options.options if o.status != OptionStatus.OPEN
        ],
        "budget_remaining": {
            "decisions": b.max_decisions - state.usage.decisions,
            "tool_calls": b.max_tool_calls - state.usage.tool_calls,
            "simulations": b.max_simulations - state.usage.simulations,
        },
    }
    criteria = {
        o.option_id: o.description + (f" | 근거: {', '.join(o.evidence_ids)}" if o.evidence_ids else "")
        for o in options.by_status(OptionStatus.OPEN)
    }
    req = ZoroRequest(
        state=st,
        questions={QUESTION_ID: ChoiceQuestion(instructions=INSTRUCTIONS, criteria=criteria)},
    )
    if sop_only:
        for key in ("virtual_experiments", "budget_remaining"):
            req.state.pop(key)
        req.questions[QUESTION_ID].instructions = (
            "알람, 관측, SOP 및 지식 근거로 가장 적합한 대응 한 가지를 선택하라. "
            "필수 계측이 부족하면 REQUEST_INFO, 근거가 부족하면 RETRIEVE_AGAIN, "
            "운영자 판단이 필요하면 ESCALATE를 선택하라."
        )
        for option in options.by_status(OptionStatus.OPEN):
            if option.kind != OptionKind.RECOMMENDATION:
                continue
            key = option.option_id
            req.questions[f"applicable:{key}"] = NoulQuestion(instructions=(
                f"현재 알람과 확인된 계측에서 {criteria[key]}의 SOP 적용 조건이 충족되는가?"))
            req.questions[f"evidence:{key}"] = ScoreQuestion(instructions=(
                f"검색된 SOP와 지식이 {criteria[key]}의 대응을 어느 정도 뒷받침하는가?"),
                criteria=["근거 없음 또는 모순", "간접 근거 또는 필수 정보 일부 누락", "현재 상황에 직접 적용되는 충분한 근거"])
    leaks = find_forbidden_keys(req.model_dump(mode="json"))
    if leaks:
        raise LeakError(f"모델 입력에 금지 키 포함: {leaks}")
    return req


# ---------------------------------------------------------------- clients
class FakeZoroClient:
    """연결 없이 흐름을 검증하는 가짜 모델. 선호 순서상 첫 허용 선택지를 고르고 고정 분포를 만든다.
    분포 값은 의미 없는 데모용 숫자다."""

    kind: Literal["real", "fake"] = "fake"

    def __init__(self, preference: list[str], top_p: float = 0.7):
        self.preference = preference
        self.top_p = top_p
        self.requests: list[ZoroRequest] = []

    def decide(self, request: ZoroRequest, request_id: str) -> ZoroRawResponse:
        self.requests.append(request)
        allowed = list(request.questions[QUESTION_ID].criteria)
        preference = [key for p in self.preference for key in (p, f"PROVIDE_SOP:{p}", f"REQUEST_INFO:{p}")]
        choice = next((p for p in preference if p in allowed), allowed[0])
        rest = [a for a in allowed if a != choice]
        probs = {choice: self.top_p if rest else 1.0}
        probs.update({a: round((1 - self.top_p) / len(rest), 4) for a in rest})
        body = {"answers": {QUESTION_ID: {"type": "choice", "choice": choice,
                                          "confidence": probs[choice], "probabilities": probs}}}
        for key, question in request.questions.items():
            if isinstance(question, NoulQuestion):
                body["answers"][key] = {"type": "noul", "noul": 1.0}
            elif isinstance(question, ScoreQuestion):
                body["answers"][key] = {"type": "score", "score": 2.0}
        return ZoroRawResponse(request_id=request_id, client_kind="fake", model="fake-zoro",
                               received_at=datetime.now(timezone.utc), latency_ms=0.0, body=body)


class ScriptedZoroClient:
    """정해진 원본 응답 본문을 순서대로 반환 (테스트용)."""

    kind: Literal["real", "fake"] = "fake"

    def __init__(self, bodies: list[Any]):
        self.bodies = list(bodies)
        self.requests: list[ZoroRequest] = []

    def decide(self, request: ZoroRequest, request_id: str) -> ZoroRawResponse:
        self.requests.append(request)
        return ZoroRawResponse(request_id=request_id, client_kind="fake", model="scripted",
                               received_at=datetime.now(timezone.utc), body=self.bodies.pop(0))


def client_from_env(fake_preference: list[str]) -> ZoroClient:
    mode = os.environ.get("ZORO_MODE", "fake").strip().lower()
    if mode == "http":
        return HttpZoroClient(ZoroHttpConfig.from_env())
    if mode == "fake":
        return FakeZoroClient(fake_preference)
    raise ZoroClientError(f"알 수 없는 ZORO_MODE={mode}")


# ---------------------------------------------------------------- response → decision
def to_decision(
    *,
    raw: ZoroRawResponse,
    decision_id: str,
    tool_request_id: str,
    state: AgentState,
    options: OptionSet,
    catalog: ActionCatalog,
    policy: AgentPolicy | None = None,
) -> Decision:
    from .state import ArgsError, resolve_args

    allowed = options.open_ids()
    errors: list[str] = []
    warnings: list[str] = []
    answers = raw.body.get("answers") if isinstance(raw.body, dict) else None
    ans = answers.get(QUESTION_ID) if isinstance(answers, dict) else None
    answer: ChoiceAnswer | None = None
    if not isinstance(ans, dict):
        errors.append(f"응답에 answers.{QUESTION_ID} 없음")
    elif ans.get("type") == "refusal":
        errors.append("모델이 답을 거부(refusal)")
    else:
        try:
            answer = ChoiceAnswer.model_validate(ans)
        except ValidationError as e:
            errors.append(f"choice 형식 오류: {e.errors()[0]['msg']}")
    if answer:
        if answer.choice not in allowed:
            errors.append(f"허용 목록 밖 선택 {answer.choice!r} → 거부")
        extra = sorted(set(answer.probabilities) - set(allowed))
        if extra:
            errors.append(f"분포에 허용 목록 밖 키 {extra}")
        missing = sorted(set(allowed) - set(answer.probabilities))
        if missing:
            warnings.append(f"분포에 없는 허용 선택지 {missing}")
    raw_choice = answer.choice if answer else (ans.get("choice") if isinstance(ans, dict) else None)
    dist = (SelectionDistribution(probabilities=answer.probabilities, confidence=answer.confidence)
            if answer else None)
    common = dict(decision_id=decision_id, event_id=state.event_id, state_version=state.state_version,
                  request_id=raw.request_id, decided_by="zoro", client_kind=raw.client_kind,
                  model_choice_raw=raw_choice, selection_distribution=dist)

    opt = options.get(answer.choice) if answer and not errors else None
    if opt is not None and opt.status != OptionStatus.OPEN:  # 이중 방어
        errors.append(f"선택지 {opt.option_id} 상태 {opt.status} → 거부")
        opt = None
    tool_request = None
    if opt is not None and opt.kind == OptionKind.TOOL_CALL:
        spec = catalog.get(opt.action_id)
        if spec is None or spec.tool_name is None:
            errors.append("선택지에 연결된 등록 도구 없음")
        else:
            try:
                tool_request = ToolRequest(
                    tool_request_id=tool_request_id, decision_id=decision_id, event_id=state.event_id,
                    state_version=state.state_version, action_id=spec.action_id, tool_name=spec.tool_name,
                    case=(resolve_case(catalog.case(spec.case_id), catalog.setpoint_map(), state.active_setpoints,
                                       state.valve_positions()) if spec.case_id else None),
                    args=resolve_args(spec.args_template, state),
                    evaluation=(evaluation_spec(state.alarms, policy.benchmark)
                                if policy and policy.benchmark and spec.case_id else None),
                    snapshot_id=state.snapshot_id, issued_sim_time_s=state.sim_time_s,
                )
            except ArgsError as e:
                errors.append(f"인자 구성 실패: {e}")
    validation = DecisionValidation(valid=not errors, errors=errors, warnings=warnings, allowed_option_ids=allowed)
    if errors or opt is None:
        return Decision(**common, decision_kind="human_review", selected_option_id=None, action_id=None,
                        validation=validation)

    refs = [EvidenceRef(ref_type="rag_evidence", ref_id=e) for e in opt.evidence_ids]
    if opt.kind == OptionKind.RECOMMENDATION:
        run = state.succeeded_run(opt.action_id or "")
        if run:
            refs.append(EvidenceRef(ref_type="run", ref_id=run.run_id, note="가상 예측"))
        if state.comparison and state.comparison.baseline_run_id:
            refs.append(EvidenceRef(ref_type="run", ref_id=state.comparison.baseline_run_id, note="기준(가상)"))
        refs += [EvidenceRef(ref_type="policy_check", ref_id=c.name) for c in opt.checks]
    return Decision(**common, decision_kind=str(opt.kind), selected_option_id=opt.option_id,
                    action_id=opt.action_id, tool_request=tool_request, evidence_refs=refs,
                    validation=validation)
