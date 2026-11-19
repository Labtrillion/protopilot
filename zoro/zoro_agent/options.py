"""선택지 구성기: RAG 후보 + 등록 행동 + 현재 상태 + 실행 이력 → 허용 선택지.

상태 결정 규칙
- excluded(제외): hard 검사 하나라도 fail (미등록, 미지원, 중복 완료, 한도, 근거 없음, 정책 위반)
- deferred(보류): soft 검사 fail 또는 어떤 검사든 unknown (오래된 값, 조건 미확인, 진행 중 실험)
- open(허용): 모든 검사 pass
unknown 은 절대 pass 로 취급하지 않는다.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from .benchmark import CaseSelection, select_case
from .cases import envelope_problems
from .common import CheckStatus, Model
from .policy import AgentPolicy
from .rag import RagCandidate
from .registry import ActionCatalog, ActionKind, ActionSpec, ConditionKind
from .state import AgentState, ArgsError, resolve_args, sensor_freshness

RECOMMEND_PREFIX = "RECOMMEND:"
UNMAPPED_PREFIX = "UNMAPPED:"


class OptionKind(StrEnum):
    TOOL_CALL = "tool_call"
    RECOMMENDATION = "recommendation"
    HUMAN_REVIEW = "human_review"


class OptionStatus(StrEnum):
    OPEN = "open"
    DEFERRED = "deferred"
    EXCLUDED = "excluded"


class OptionCheck(Model):
    name: str
    status: CheckStatus
    blocking: Literal["hard", "soft"]
    detail: str


class OptionEvaluation(Model):
    option_id: str
    kind: OptionKind
    action_id: str | None
    status: OptionStatus
    description: str
    reason: str
    evidence_ids: list[str] = []
    requested_sensor_ids: list[str] = []
    checks: list[OptionCheck]


class OptionSet(Model):
    event_id: str
    state_version: int
    options: list[OptionEvaluation]

    def get(self, option_id: str) -> OptionEvaluation | None:
        return next((o for o in self.options if o.option_id == option_id), None)

    def by_status(self, status: OptionStatus) -> list[OptionEvaluation]:
        return [o for o in self.options if o.status == status]

    def open_ids(self) -> list[str]:
        return [o.option_id for o in self.by_status(OptionStatus.OPEN)]


def _C(name: str, status: CheckStatus, detail: str, blocking: Literal["hard", "soft"] = "soft") -> OptionCheck:
    return OptionCheck(name=name, status=status, blocking=blocking, detail=detail)


def _finalize(option_id, kind, action_id, description, checks, evidence_ids) -> OptionEvaluation:
    if any(c.status == CheckStatus.FAIL and c.blocking == "hard" for c in checks):
        status = OptionStatus.EXCLUDED
    elif any(c.status != CheckStatus.PASS for c in checks):
        status = OptionStatus.DEFERRED
    else:
        status = OptionStatus.OPEN
    bad = [f"{c.name}={c.status}: {c.detail}" for c in checks if c.status != CheckStatus.PASS]
    return OptionEvaluation(
        option_id=option_id,
        kind=kind,
        action_id=action_id,
        status=status,
        description=description,
        reason="; ".join(bad) if bad else "모든 검사 통과",
        evidence_ids=evidence_ids,
        checks=checks,
    )


def evaluate_condition(cid: str, state: AgentState, catalog: ActionCatalog, policy: AgentPolicy) -> tuple[CheckStatus, str]:
    spec = catalog.condition(cid)
    if spec is None:
        return CheckStatus.UNKNOWN, f"정의되지 않은 조건 {cid}"
    if spec.kind == ConditionKind.SENSOR_FRESH:
        return sensor_freshness(state, spec.sensor_id or "", policy.freshness)
    if spec.kind == ConditionKind.SAFETY_CHECK:
        sc = state.safety(spec.check_id or "")
        if sc is None:
            return CheckStatus.UNKNOWN, f"{spec.check_id}: 외부 규칙 결과 없음"
        return sc.status, f"{spec.check_id}: {sc.status} ({sc.source})"
    # RUN_SUCCEEDED
    runs = state.runs_for(spec.action_id or "")
    if any(r.status == "succeeded" for r in runs):
        return CheckStatus.PASS, f"{spec.action_id} 실험 성공"
    if any(r.status in ("queued", "running") for r in runs):
        return CheckStatus.UNKNOWN, f"{spec.action_id} 실험 진행 중"
    return CheckStatus.FAIL, f"{spec.action_id} 실험 미완료"


def _duplicate_check(spec: ActionSpec, state: AgentState) -> OptionCheck:
    hist = [
        h for h in state.tool_history
        if h.action_id == spec.action_id and (spec.rerun.scope == "unlimited" or h.snapshot_id == state.snapshot_id)
    ]
    runs = state.runs_for(spec.action_id) if spec.kind == ActionKind.SIMULATION else []
    if any(h.status == "running" for h in hist) or any(r.status in ("queued", "running") for r in runs):
        return _C("not_duplicate", CheckStatus.FAIL, "동일 실험 진행 중")
    if spec.rerun.scope == "per_snapshot" and (
        any(h.status == "succeeded" for h in hist) or any(r.status == "succeeded" for r in runs)
    ):
        return _C("not_duplicate", CheckStatus.FAIL, f"같은 snapshot({state.snapshot_id})에서 이미 완료", "hard")
    if not spec.rerun.retry_after_failure and any(h.status in ("failed", "aborted") for h in hist):
        return _C("not_duplicate", CheckStatus.FAIL, "실패 후 재시도 불허 정책", "hard")
    if spec.rerun.max_runs is not None and len(hist) >= spec.rerun.max_runs:
        return _C("not_duplicate", CheckStatus.FAIL, f"최대 시도 {spec.rerun.max_runs}회 도달", "hard")
    return _C("not_duplicate", CheckStatus.PASS, f"이전 시도 {len(hist)}회")


def case_candidates(catalog: ActionCatalog, policy: AgentPolicy) -> list[ActionSpec]:
    base = policy.recommendation.baseline_action_id
    return [a for a in catalog.actions
            if a.kind == ActionKind.SIMULATION and a.recommendable and a.action_id != base]


def case_selection(state: AgentState, catalog: ActionCatalog, policy: AgentPolicy) -> CaseSelection | None:
    """심슨(기준) 대비 case 탐색 상태. 벤치마크 정책이 없거나 기준 점수가 없으면 None."""
    bench = policy.benchmark
    if bench is None:
        return None
    base = state.succeeded_run(policy.recommendation.baseline_action_id)
    base_imp = state.impact_for(base.run_id) if base else None
    if base_imp is None or base_imp.score is None:
        return None
    cands = {a.action_id: a for a in case_candidates(catalog, policy)}
    comparable = set(state.comparison.comparable_run_ids) if state.comparison else set()
    tested = []
    for r in state.runs:
        imp = state.impact_for(r.run_id)
        if (r.snapshot_id == state.snapshot_id and r.status == "succeeded" and r.action_id in cands
                and r.run_id in comparable and imp is not None and imp.score is not None):
            tested.append((imp.score, r.action_id))
    remaining = 0
    for a in cands.values():
        if not a.supported or state.runs_for(a.action_id):
            continue
        if policy.sop_first and _duplicate_check(a, state).status == CheckStatus.FAIL:
            continue
        case = catalog.case(a.case_id)
        if case and envelope_problems(case, catalog.setpoint_map(), state.active_setpoints, state.sim_time_s,
                                      state.valve_positions())[0]:
            continue  # 허용 범위 밖 case 는 시험할 수 없음
        remaining += 1
    remaining = min(remaining, policy.budget.max_simulations - state.usage.simulations)
    return select_case(base_imp.score, tested, remaining, bench)


def _case_checks(spec: ActionSpec, state: AgentState, catalog: ActionCatalog) -> list[OptionCheck]:
    """시뮬레이션 case 가 현재 상태에서 감독기 허용 범위 안인가."""
    case = catalog.case(spec.case_id)
    if spec.kind != ActionKind.SIMULATION or case is None:
        return []
    hard, unknown = envelope_problems(case, catalog.setpoint_map(), state.active_setpoints, state.sim_time_s,
                                      state.valve_positions())
    if hard:
        return [_C("setpoint_envelope", CheckStatus.FAIL, "; ".join(hard), "hard")]
    if unknown:
        return [_C("setpoint_envelope", CheckStatus.UNKNOWN, "; ".join(unknown))]
    desc = ", ".join(f"{c.setpoint_id}→{c.target_value:g} {c.unit}" for c in case.setpoint_changes) or "현재 목표 유지"
    return [_C("setpoint_envelope", CheckStatus.PASS, f"{case.case_id}: {desc} (허용 범위 내)")]


def _eval_action(
    spec: ActionSpec,
    cands: list[RagCandidate],
    state: AgentState,
    catalog: ActionCatalog,
    policy: AgentPolicy,
    tools: set[str],
    sop_only: bool = False,
) -> OptionEvaluation:
    checks = [_C("registered", CheckStatus.PASS, "등록된 action_id")]
    is_sop_candidate = sop_only and spec.kind == ActionKind.SIMULATION
    supported = spec.supported and (is_sop_candidate or spec.tool_name in tools)
    checks.append(
        _C("supported", CheckStatus.PASS if supported else CheckStatus.FAIL,
           f"tool={spec.tool_name}, supported={spec.supported}, 연결됨={spec.tool_name in tools}", "hard")
    )
    cond_ids = list(dict.fromkeys(spec.preconditions + [c for cand in cands for c in cand.condition_ids]))
    for cid in cond_ids:
        st, d = evaluate_condition(cid, state, catalog, policy)
        checks.append(_C(f"condition:{cid}", st, d))
    for ref in dict.fromkeys(r for cand in cands for r in cand.unmapped_condition_refs):
        checks.append(_C(f"condition:{ref}", CheckStatus.UNKNOWN, "RAG 조건이 등록 조건과 매핑되지 않음"))
    for s in spec.required_fresh_sensors:
        st, d = sensor_freshness(state, s, policy.freshness)
        checks.append(_C(f"input_fresh:{s}", st, d))
    if is_sop_candidate:
        case = catalog.case(spec.case_id)
        versions = {(r.sop_id, r.version) for r in case.sop_refs if r.version} if case else set()
        evidence_ids = {eid for candidate in cands for eid in candidate.evidence_ids}
        evidence = [e for e in state.rag.evidence if e.evidence_id in evidence_ids]
        linked = any((e.sop_id, e.sop_version) in versions or (e.doc_id, e.doc_version) in versions for e in evidence)
        checks.append(_C("sop_version", CheckStatus.PASS if linked else CheckStatus.FAIL,
                         "검색 근거와 등록 SOP 버전 일치 필요", "hard"))
        checks += _case_checks(spec, state, catalog)
        for sid in dict.fromkeys(policy.recommendation.required_fresh_sensors):
            if sid in spec.required_fresh_sensors:
                continue
            status, detail = sensor_freshness(state, sid, policy.freshness)
            checks.append(_C(f"input_fresh:{sid}", status, detail))
        for cid in policy.recommendation.required_safety_checks:
            safety = state.safety(cid)
            checks.append(_C(f"safety:{cid}", safety.status if safety else CheckStatus.UNKNOWN, cid, "hard"))
        return _finalize(f"PROVIDE_SOP:{spec.action_id}", OptionKind.RECOMMENDATION, spec.action_id,
                         case.description if case else spec.description, checks, [e.evidence_id for e in evidence])
    if spec.kind == ActionKind.SENSOR_QUERY:
        stale = [s for s in spec.target_sensors if sensor_freshness(state, s, policy.freshness)[0] != CheckStatus.PASS]
        checks.append(
            _C("query_needed", CheckStatus.PASS if stale else CheckStatus.FAIL,
               f"갱신 필요: {stale}" if stale else "대상 센서가 모두 최신", "hard")
        )
    checks.append(_duplicate_check(spec, state))
    b = policy.budget
    left_tools = b.max_tool_calls - state.usage.tool_calls
    if left_tools <= 0:
        checks.append(_C("budget", CheckStatus.FAIL, "도구 호출 한도 도달", "hard"))
    elif spec.kind == ActionKind.SIMULATION and b.max_simulations - state.usage.simulations <= 0:
        checks.append(_C("budget", CheckStatus.FAIL, "시뮬레이션 한도 도달", "hard"))
    else:
        checks.append(_C("budget", CheckStatus.PASS, f"남은 도구 호출 {left_tools}"))
    known_ev = state.rag.evidence_ids()
    ev_ids = list(dict.fromkeys(e for c in cands for e in c.evidence_ids if e in known_ev))
    if spec.requires_rag_evidence:
        checks.append(
            _C("rag_evidence", CheckStatus.PASS if ev_ids else CheckStatus.FAIL,
               f"근거 {ev_ids}" if ev_ids else "RAG 근거 없음", "hard")
        )
    checks += _case_checks(spec, state, catalog)
    if spec.kind == ActionKind.SIMULATION and spec.recommendable and \
            spec.action_id != policy.recommendation.baseline_action_id:
        sel = case_selection(state, catalog, policy)
        if sel is not None and sel.status != "continue":
            checks.append(_C("case_search_open", CheckStatus.FAIL, f"case 탐색 종료({sel.status}): {sel.reason}", "hard"))
    try:
        resolve_args(spec.args_template, state)
        checks.append(_C("args_resolvable", CheckStatus.PASS, "등록 설정으로 인자 구성 가능"))
    except ArgsError as e:
        checks.append(_C("args_resolvable", CheckStatus.FAIL, str(e)))
    purpose = " / ".join(dict.fromkeys(c.purpose for c in cands if c.purpose))
    desc = spec.description + (f" | 목적: {purpose}" if purpose else "")
    case = catalog.case(spec.case_id)
    if case is not None:
        ch = ", ".join(f"{c.setpoint_id} 목표 {c.target_value:g} {c.unit}" for c in case.setpoint_changes)
        desc += f" | case {case.case_id}: {case.policy}" + (f" ({ch})" if ch else "")
    return _finalize(spec.action_id, OptionKind.TOOL_CALL, spec.action_id, desc, checks, ev_ids)


def _eval_recommendation(
    spec: ActionSpec, cands: list[RagCandidate], state: AgentState, catalog: ActionCatalog, policy: AgentPolicy
) -> OptionEvaluation:
    rp = policy.recommendation
    if policy.benchmark is not None and spec.action_id == rp.baseline_action_id:
        return _eval_baseline_recommendation(spec, cands, state, catalog, policy)
    checks: list[OptionCheck] = _case_checks(spec, state, catalog)
    if policy.benchmark is not None:
        sel = case_selection(state, catalog, policy)
        if sel is None:
            checks.append(_C("case_selection", CheckStatus.FAIL, "심슨(기준) 점수 없음"))
        elif sel.status == "continue":
            checks.append(_C("case_selection", CheckStatus.FAIL, f"탐색 계속: {sel.reason}"))
        elif sel.status == "select_case" and sel.selected_action_id == spec.action_id:
            checks.append(_C("case_selection", CheckStatus.PASS, sel.reason))
        else:
            checks.append(_C("case_selection", CheckStatus.FAIL,
                             f"선정 결과 {sel.status} / {sel.selected_action_id}: {sel.reason}", "hard"))
    run = state.succeeded_run(spec.action_id)
    runs = state.runs_for(spec.action_id)
    if run:
        checks.append(_C("run_succeeded", CheckStatus.PASS, f"{run.run_id} 성공"))
    elif any(r.status in ("queued", "running") for r in runs):
        checks.append(_C("run_succeeded", CheckStatus.FAIL, "실험 진행 중"))
    else:
        checks.append(_C("run_succeeded", CheckStatus.FAIL, "성공한 실험 결과 없음 (미실행/실패)"))
    base = state.succeeded_run(rp.baseline_action_id)
    checks.append(
        _C("baseline_run", CheckStatus.PASS if base else CheckStatus.FAIL,
           f"기준 {base.run_id}" if base else f"기준 실험({rp.baseline_action_id}) 없음")
    )
    cmp = state.comparison
    n_ok = len(cmp.comparable_run_ids) if cmp else 0
    checks.append(
        _C("min_candidate_runs", CheckStatus.PASS if n_ok >= rp.min_candidate_runs else CheckStatus.FAIL,
           f"비교 가능 후보 {n_ok}/{rp.min_candidate_runs}")
    )
    if run:
        if cmp and run.run_id in cmp.comparable_run_ids:
            checks.append(_C("comparable", CheckStatus.PASS, "기준과 동일 조건"))
        elif cmp and run.run_id in cmp.not_comparable:
            checks.append(_C("comparable", CheckStatus.FAIL, cmp.not_comparable[run.run_id], "hard"))
        else:
            checks.append(_C("comparable", CheckStatus.UNKNOWN, "비교 판정 없음"))
        imp = state.impact_for(run.run_id)
        if imp is None:
            checks.append(_C("impact", CheckStatus.UNKNOWN, "영향 분석 결과 없음"))
        else:
            if rp.require_alarm_recovery:
                rec = imp.original_alarm_recovered
                st = CheckStatus.UNKNOWN if rec is None else (CheckStatus.PASS if rec else CheckStatus.FAIL)
                checks.append(_C("alarm_recovery", st, f"원래 알람 회복(예측)={rec}", "hard"))
            if rp.block_if_limit_exceedance:
                n = len(imp.exceedances)
                checks.append(
                    _C("no_limit_exceedance", CheckStatus.PASS if n == 0 else CheckStatus.FAIL,
                       f"한계 초과 {n}건(예측)", "hard")
                )
    for cid in rp.required_safety_checks:
        sc = state.safety(cid)
        st = CheckStatus.UNKNOWN if sc is None else sc.status
        checks.append(_C(f"safety:{cid}", st, f"외부 안전 검사 {cid}={st}", "hard"))
    for s in rp.required_fresh_sensors:
        st, d = sensor_freshness(state, s, policy.freshness)
        checks.append(_C(f"input_fresh:{s}", st, d))
    known_ev = state.rag.evidence_ids()
    ev_ids = list(dict.fromkeys(e for c in cands for e in c.evidence_ids if e in known_ev))
    if rp.require_rag_evidence:
        checks.append(
            _C("rag_evidence", CheckStatus.PASS if ev_ids else CheckStatus.FAIL,
               f"근거 {ev_ids}" if ev_ids else "RAG 근거 없음", "hard")
        )
    desc = f"최종 권고: {spec.description} — 기준 대비 비교 실험(가상 예측) 결과 기반"
    return _finalize(f"{RECOMMEND_PREFIX}{spec.action_id}", OptionKind.RECOMMENDATION, spec.action_id, desc, checks, ev_ids)


def _eval_baseline_recommendation(spec: ActionSpec, cands: list[RagCandidate], state: AgentState,
                                  catalog: ActionCatalog, policy: AgentPolicy) -> OptionEvaluation:
    """탐색이 끝났고 기준안을 이긴 후보가 없으며 기준안이 적격일 때 권고한다."""
    rp = policy.recommendation
    checks: list[OptionCheck] = []
    run = state.succeeded_run(spec.action_id)
    checks.append(_C("run_succeeded", CheckStatus.PASS if run else CheckStatus.FAIL,
                     f"{run.run_id} 성공" if run else "기준 실험 결과 없음"))
    if policy.sop_first:
        checks += _case_checks(spec, state, catalog)
        impact = state.impact_for(run.run_id) if run else None
        if rp.require_alarm_recovery:
            recovered = impact.original_alarm_recovered if impact else None
            status = CheckStatus.UNKNOWN if recovered is None else (CheckStatus.PASS if recovered else CheckStatus.FAIL)
            checks.append(_C("alarm_recovery", status, f"원래 알람 회복(예측)={recovered}", "hard"))
        if rp.block_if_limit_exceedance:
            status = CheckStatus.UNKNOWN if impact is None else (CheckStatus.FAIL if impact.exceedances else CheckStatus.PASS)
            checks.append(_C("no_limit_exceedance", status, "기준 실험 한계 초과 검사", "hard"))
    sel = case_selection(state, catalog, policy)
    if sel is None:
        checks.append(_C("case_selection", CheckStatus.FAIL, "심슨(기준) 점수 없음"))
    elif sel.status == "continue":
        checks.append(_C("case_selection", CheckStatus.FAIL, f"탐색 계속: {sel.reason}"))
    elif sel.status == "select_baseline":
        checks.append(_C("case_selection", CheckStatus.PASS, sel.reason))
    else:
        checks.append(_C("case_selection", CheckStatus.FAIL, f"{sel.status}: {sel.reason}", "hard"))
    for cid in rp.required_safety_checks:
        sc = state.safety(cid)
        st = CheckStatus.UNKNOWN if sc is None else sc.status
        checks.append(_C(f"safety:{cid}", st, f"외부 안전 검사 {cid}={st}", "hard"))
    for s in rp.required_fresh_sensors:
        st, d = sensor_freshness(state, s, policy.freshness)
        checks.append(_C(f"input_fresh:{s}", st, d))
    known_ev = state.rag.evidence_ids()
    ev_ids = list(dict.fromkeys(e for c in cands for e in c.evidence_ids if e in known_ev))
    if rp.require_rag_evidence:
        checks.append(_C("rag_evidence", CheckStatus.PASS if ev_ids else CheckStatus.FAIL,
                         f"근거 {ev_ids}" if ev_ids else "RAG 근거 없음", "hard"))
    desc = (f"최종 권고: SOP 기준 대응안 유지 — {spec.description}" if policy.sop_first else
            f"최종 권고: 현재 목표 유지(hold) — {spec.description}. 심슨을 의미 있게 이긴 case 없음(가상 예측)")
    return _finalize(f"{RECOMMEND_PREFIX}{spec.action_id}", OptionKind.RECOMMENDATION, spec.action_id, desc, checks, ev_ids)


def build_options(
    state: AgentState, catalog: ActionCatalog, policy: AgentPolicy, available_tools: set[str],
    *, sop_only: bool = False, case1_baseline: bool = False,
) -> OptionSet:
    evals: list[OptionEvaluation] = []
    by_action: dict[str, list[RagCandidate]] = {}
    for c in state.rag.candidates:
        if c.action_id is None:
            evals.append(
                _finalize(
                    f"{UNMAPPED_PREFIX}{c.raw_ref}", OptionKind.TOOL_CALL, None, c.purpose,
                    [_C("registered", CheckStatus.FAIL, f"등록되지 않은 행동 {c.raw_ref!r} — 실행하지 않음", "hard")],
                    c.evidence_ids,
                )
            )
        else:
            by_action.setdefault(c.action_id, []).append(c)
    action_ids = list(dict.fromkeys(
        [*by_action, *(a.action_id for a in catalog.actions if a.offer_without_rag)]
    ))
    baseline = state.succeeded_run(policy.recommendation.baseline_action_id) if case1_baseline else None
    baseline_impact = state.impact_for(baseline.run_id) if baseline else None
    for aid in action_ids:
        spec = catalog.get(aid)
        assert spec is not None  # 어댑터가 등록 ID만 매핑
        if spec.kind == ActionKind.HUMAN_REVIEW:
            continue
        if sop_only and spec.kind not in {ActionKind.SIMULATION, ActionKind.SENSOR_QUERY}:
            continue
        option = _eval_action(spec, by_action.get(aid, []), state, catalog, policy, available_tools, sop_only=sop_only)
        if sop_only and spec.kind == ActionKind.SENSOR_QUERY:
            option.option_id = f"REQUEST_INFO:{aid}"
        if (case1_baseline and spec.kind == ActionKind.SIMULATION and aid != policy.recommendation.baseline_action_id
                and option.status == OptionStatus.OPEN
                and (baseline_impact is None or baseline_impact.score is None)):
            option.status, option.reason = OptionStatus.DEFERRED, "case1 기준 실험과 벤치마크 점수 필요"
        evals.append(option)
    for aid in ([] if sop_only else action_ids):
        spec = catalog.get(aid)
        is_hold = policy.benchmark is not None and aid == policy.recommendation.baseline_action_id
        if spec and spec.kind == ActionKind.SIMULATION and (spec.recommendable or is_hold):
            evals.append(_eval_recommendation(spec, by_action.get(aid, []), state, catalog, policy))
    hr = catalog.human_review_action()
    if hr is not None:
        evals.append(
            _finalize(
                "ESCALATE" if sop_only else hr.action_id, OptionKind.HUMAN_REVIEW, hr.action_id, hr.description,
                [_C("supported", CheckStatus.PASS if hr.supported else CheckStatus.FAIL, "운영자 검토 요청", "hard")],
                [],
            )
        )
    if sop_only:
        missing = list(dict.fromkeys(c.name.removeprefix("input_fresh:") for option in evals
                                    if option.kind == OptionKind.RECOMMENDATION for c in option.checks
                                    if c.name.startswith("input_fresh:") and c.status != CheckStatus.PASS))
        if missing:
            option = _finalize("REQUEST_INFO", OptionKind.HUMAN_REVIEW, None,
                               f"추가 센서 정보 요청: {', '.join(missing)}", [], [])
            option.requested_sensor_ids = missing
            evals.append(option)
        evals.append(_finalize("RETRIEVE_AGAIN", OptionKind.HUMAN_REVIEW, None,
                               "관련 SOP 및 지식 근거를 다시 검색 요청", [], []))
    return OptionSet(event_id=state.event_id, state_version=state.state_version, options=evals)
