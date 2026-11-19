"""판단 반복 실행부.

사건+RAG → 상태 → 선택지 → Zoro → 검증 → 등록 도구 실행 → 상태·이력 갱신 → Zoro 재호출 → 종료.
모델 호출 횟수는 고정하지 않는다. 종료 조건은 TerminationReason 참조.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from pydantic import ValidationError

from .case_variants import case1_search
from .cases import resolve_case
from .common import CheckStatus
from .inputs import EventInput
from .options import OptionKind, OptionSet, OptionStatus, build_options, case_selection
from .outputs import (
    FinalOutput,
    LogEntry,
    LogStage,
    ProgressStatus,
    RationaleItem,
    RunReport,
    SetpointCommand,
    SimulatedEffect,
    TerminationReason,
    ZoroExchange,
)
from .policy import AgentPolicy
from .rag import NormalizedRagResult
from .registry import ActionCatalog, ActionKind
from .state import AgentState, DecisionRecord, apply_tool_result, assess_comparison, sensor_freshness
from .tool_io import ToolRequest, ToolResult
from .tools import ToolExecutor
from zoro.client import ZoroClient, ZoroClientError
from zoro.schemas import NoulAnswer, ScoreAnswer

from .decision import (
    Decision,
    DecisionValidation,
    build_zoro_request,
    to_decision,
)

DISCLAIMER = (
    "가상의 연구용 시나리오 결과다. 시뮬레이션 결과는 예측이며 실제 공정 관측이 아니다. "
    "정책 수치는 예시 설정이고 실제 공정에서 검증된 기준이 아니다. 실행 전 운영자 확인이 필요하다."
)


class ZoroAgent:
    def __init__(
        self,
        catalog: ActionCatalog,
        policy: AgentPolicy,
        client: ZoroClient,
        executor: ToolExecutor,
        on_progress: Callable[[ProgressStatus], None] | None = None,
        retrieve_again: Callable[[EventInput, NormalizedRagResult], NormalizedRagResult] | None = None,
    ):
        self.catalog = catalog
        self.policy = policy
        self.client = client
        self.executor = executor
        self.on_progress = on_progress
        self.retrieve_again = retrieve_again

    # ------------------------------------------------------------ main loop
    def run(self, event: EventInput, rag: NormalizedRagResult) -> RunReport:
        if not self.policy.sop_first:
            return self._run(event, rag)
        started_at = time.monotonic()
        first = self._run(event, rag, sop_only=True, started_at=started_at)
        action_id = first.final.recommended_action_id
        if action_id is None or self.policy.benchmark is None or "run_simulation" not in self.executor.available():
            return first
        catalog, policy, search_rag = case1_search(self.catalog, self.policy, first.final_state.rag, action_id)
        search = ZoroAgent(catalog, policy, self.client, self.executor, self.on_progress)
        report = search._run(event, search_rag, case1_baseline=True, initial_state=first.final_state,
                             started_at=started_at)
        report.sop_stage = first
        return report

    def run_sop(self, event: EventInput, rag: NormalizedRagResult) -> RunReport:
        """Evaluate SOP applicability and select one response before simulation."""
        return self._run(event, rag, sop_only=True)

    def _run(self, event: EventInput, rag: NormalizedRagResult, *, sop_only: bool = False,
             case1_baseline: bool = False, initial_state: AgentState | None = None,
             started_at: float | None = None) -> RunReport:
        self._log_entries: list[LogEntry] = []
        self._progress: list[ProgressStatus] = []
        self._decisions: list[Decision] = []
        self._exchanges: list[ZoroExchange] = []
        self._treqs: list[ToolRequest] = []
        self._tres: list[ToolResult] = []
        t0 = time.monotonic() if started_at is None else started_at
        if sop_only:
            rag = rag.without_economic_evidence()
        st = (initial_state.model_copy(deep=True) if initial_state else AgentState.from_inputs(event, rag))
        st.rag = rag
        self.state = st
        self._sop_only = sop_only
        retrieved_again = False
        self._log(LogStage.INPUT_RECEIVED, "사건·RAG 입력 수신", data={
            "alarms": [a.alarm_id for a in st.alarms if a.active],
            "rag_candidates": [(c.raw_ref, c.action_id, c.mapping) for c in rag.candidates],
            "rag_warnings": rag.warnings,
        })
        budget = self.policy.budget
        it = 0
        opts: OptionSet | None = None
        while True:
            it += 1
            if st.usage.decisions >= budget.max_decisions:
                return self._finish(TerminationReason.LIMIT_REACHED, opts, None, "Zoro 판단 횟수 한도 도달")
            if budget.max_wall_time_s is not None and time.monotonic() - t0 > budget.max_wall_time_s:
                return self._finish(TerminationReason.LIMIT_REACHED, opts, None, "실행 시간 한도 도달")

            opts = build_options(st, self.catalog, self.policy, self.executor.available(),
                                 sop_only=sop_only, case1_baseline=case1_baseline)
            self._log(LogStage.OPTIONS_BUILT, f"선택지 {len(opts.open_ids())}개 허용", data={
                o.option_id: {"status": str(o.status), "reason": o.reason} for o in opts.options
            })
            self._emit(it, "options_built", opts)

            actionable = [o for o in opts.by_status(OptionStatus.OPEN) if o.kind != OptionKind.HUMAN_REVIEW]
            if not actionable and not (sop_only and opts.open_ids()):
                budget_hit = any(
                    c.name == "budget" and c.status == CheckStatus.FAIL for o in opts.options for c in o.checks
                )
                reason = TerminationReason.LIMIT_REACHED if budget_hit else TerminationReason.NO_VALID_OPTIONS
                d = self._policy_decision(opts, "운영자 검토 외 유효한 선택지 없음")
                return self._finish(reason, opts, d, "운영자 검토 외 유효한 다음 선택지 없음")

            decision_id = f"{st.event_id}-D{len(st.decisions) + 1:02d}"
            request_id = f"{decision_id}-ZQ"
            req = build_zoro_request(st, opts, self.catalog, self.policy, sop_only=sop_only)
            exch = ZoroExchange(request_id=request_id, decision_id=decision_id, state_version=st.state_version,
                                options=opts, request=req, raw_response=None)
            self._exchanges.append(exch)
            self._log(LogStage.MODEL_REQUESTED, f"Zoro 호출 ({self.client.kind})", decision_id=decision_id,
                      data={"allowed": opts.open_ids(), "client_kind": self.client.kind})
            self._emit(it, "awaiting_model", opts)
            try:
                raw = self.client.decide(req, request_id)
            except ZoroClientError as e:
                return self._finish(TerminationReason.MODEL_CALL_FAILED, opts, None, str(e))
            exch.raw_response = raw
            st.usage.decisions += 1

            dec = to_decision(raw=raw, decision_id=decision_id, tool_request_id=f"{decision_id}-T1",
                              state=st, options=opts, catalog=self.catalog, policy=self.policy)
            if sop_only and dec.decision_kind == "recommendation":
                answers = raw.body["answers"]
                try:
                    applicable = NoulAnswer.model_validate(answers.get(f"applicable:{dec.selected_option_id}"))
                    evidence = ScoreAnswer.model_validate(answers.get(f"evidence:{dec.selected_option_id}"))
                except ValidationError:
                    dec.decision_kind = "human_review"
                    dec.selected_option_id = dec.action_id = None
                    dec.validation.valid = False
                    dec.validation.errors.append("선택한 SOP의 Noul 또는 Score 응답 누락·형식 오류")
                else:
                    if applicable.noul <= 0.5 or evidence.score < 1.0:
                        dec.decision_kind = "human_review"
                        dec.selected_option_id, dec.action_id = "RETRIEVE_AGAIN", None
                        dec.validation.warnings.append("선택한 SOP의 적용 가능성 또는 근거 강도 부족")
            self._record(dec)
            if not dec.validation.valid:
                self._log(LogStage.DECISION_REJECTED, f"모델 선택 거부: {dec.validation.errors}",
                          decision_id=decision_id, data={"model_choice_raw": dec.model_choice_raw})
                return self._finish(TerminationReason.MODEL_RESPONSE_INVALID, opts, dec,
                                    "; ".join(dec.validation.errors))
            self._log(LogStage.MODEL_SELECTED, f"선택 {dec.selected_option_id} ({dec.decision_kind})",
                      decision_id=decision_id, data={
                          "distribution": dec.selection_distribution.probabilities
                          if dec.selection_distribution else None,
                          "warnings": dec.validation.warnings})
            if dec.decision_kind == "recommendation":
                return self._finish(TerminationReason.RECOMMENDATION_READY, opts, dec, "")
            if dec.decision_kind == "human_review":
                if sop_only and dec.selected_option_id == "REQUEST_INFO":
                    return self._finish(TerminationReason.HUMAN_REVIEW_SELECTED, opts, dec, "추가 센서 정보 요청")
                if sop_only and dec.selected_option_id == "RETRIEVE_AGAIN":
                    if self.retrieve_again and not retrieved_again:
                        refreshed = self.retrieve_again(event, st.rag)
                        st.rag = refreshed.without_economic_evidence()
                        st.state_version += 1
                        retrieved_again = True
                        continue
                    return self._finish(TerminationReason.HUMAN_REVIEW_SELECTED, opts, dec, "관련 SOP 및 지식 재검색 요청")
                return self._finish(TerminationReason.HUMAN_REVIEW_SELECTED, opts, dec, "Zoro가 운영자 검토 선택")

            treq = dec.tool_request
            assert treq is not None
            spec = self.catalog.get(treq.action_id)
            st.usage.tool_calls += 1
            if spec and spec.kind == ActionKind.SIMULATION:
                st.usage.simulations += 1
            self._treqs.append(treq)
            self._log(LogStage.TOOL_STARTED, f"{treq.tool_name}({treq.action_id}) 시작",
                      decision_id=decision_id, tool_request_id=treq.tool_request_id, data={"args": treq.args})
            self._emit(it, "running_tool", opts)
            res = self.executor.execute(treq)
            self._tres.append(res)
            stage = {"succeeded": LogStage.TOOL_SUCCEEDED, "running": LogStage.TOOL_RUNNING}.get(
                res.status, LogStage.TOOL_FAILED)
            self._log(stage, f"{treq.tool_name} → {res.status}" + (" [가상]" if res.is_virtual else ""),
                      decision_id=decision_id, tool_request_id=treq.tool_request_id,
                      data={"error": res.error.model_dump() if res.error else None})
            rec = apply_tool_result(st, treq, res)
            st.comparison = assess_comparison(st, self.policy.recommendation.baseline_action_id)
            self._log(LogStage.STATE_UPDATED, f"상태 v{rec.state_version_before}→v{rec.state_version_after}, "
                      f"정보 갱신={rec.info_updated}", decision_id=decision_id,
                      tool_request_id=treq.tool_request_id, run_id=rec.run_id,
                      data={"notes": rec.notes, "updated_sensors": rec.updated_sensor_ids,
                            "comparison": st.comparison.model_dump() if st.comparison else None})
            self._emit(it, "state_updated", opts)
            if res.status in ("failed", "aborted") and (res.error is None or not res.error.recoverable):
                return self._finish(TerminationReason.UNRECOVERABLE_TOOL_ERROR, opts, None,
                                    f"{treq.tool_name}: {res.error.message if res.error else res.status}")

    # ------------------------------------------------------------ helpers
    def _record(self, d: Decision) -> None:
        self._decisions.append(d)
        self.state.decisions.append(DecisionRecord(
            decision_id=d.decision_id, state_version=d.state_version, decided_by=d.decided_by,
            decision_kind=d.decision_kind, selected_option_id=d.selected_option_id,
            action_id=d.action_id, valid=d.validation.valid))

    def _policy_decision(self, opts: OptionSet, why: str) -> Decision:
        hr = self.catalog.human_review_action()
        d = Decision(
            decision_id=f"{self.state.event_id}-D{len(self.state.decisions) + 1:02d}", event_id=self.state.event_id,
            state_version=self.state.state_version, request_id=None, decided_by="policy", client_kind=None,
            decision_kind="human_review", selected_option_id=hr.action_id if hr else None,
            action_id=hr.action_id if hr else None,
            validation=DecisionValidation(valid=True, warnings=[why], allowed_option_ids=opts.open_ids()),
        )
        self._record(d)
        self._log(LogStage.POLICY_DECISION, f"정책 결정: 운영자 검토 ({why})", decision_id=d.decision_id)
        return d

    def _log(self, stage: LogStage, summary: str, **kw) -> None:
        self._log_entries.append(LogEntry(
            seq=len(self._log_entries) + 1, event_id=self.state.event_id, state_version=self.state.state_version,
            stage=stage, summary=summary, sim_time_s=self.state.sim_time_s,
            logged_at=datetime.now(timezone.utc), **kw))

    def _remaining(self) -> dict[str, int]:
        b, u = self.policy.budget, self.state.usage
        return {"decisions": b.max_decisions - u.decisions, "tool_calls": b.max_tool_calls - u.tool_calls,
                "simulations": b.max_simulations - u.simulations}

    def _emit(self, it: int, phase: str, opts: OptionSet | None, reason: TerminationReason | None = None) -> None:
        p = ProgressStatus(
            event_id=self.state.event_id, state_version=self.state.state_version, iteration=it, phase=phase,
            usage=self.state.usage.model_copy(), remaining=self._remaining(),
            open_option_ids=opts.open_ids() if opts else [],
            deferred_option_ids=[o.option_id for o in opts.by_status(OptionStatus.DEFERRED)] if opts else [],
            last_decision_id=self._decisions[-1].decision_id if self._decisions else None,
            finished=phase == "finished", termination_reason=reason)
        self._progress.append(p)
        if self.on_progress:
            self.on_progress(p)

    def _finish(self, reason: TerminationReason, opts: OptionSet | None, dec: Decision | None, detail: str) -> RunReport:
        final = build_final_output(self.state, reason, opts, dec, detail, self.catalog, self.policy,
                                   self.client.kind if self._exchanges else None, self.executor.virtual,
                                   sop_only=self._sop_only)
        self._log(LogStage.TERMINATED, f"종료: {reason} → {final.outcome}", data={"detail": detail})
        self._emit(len(self._progress), "finished", opts, reason)
        return RunReport(final=final, decisions=self._decisions, exchanges=self._exchanges,
                         tool_requests=self._treqs, tool_results=self._tres, progress=self._progress,
                         log=self._log_entries, final_state=self.state)


# ---------------------------------------------------------------- final output
def build_final_output(
    st: AgentState,
    reason: TerminationReason,
    opts: OptionSet | None,
    dec: Decision | None,
    detail: str,
    catalog: ActionCatalog,
    policy: AgentPolicy,
    client_kind,
    tools_virtual: bool,
    *, sop_only: bool = False,
) -> FinalOutput:
    rp = policy.recommendation
    rationale: list[RationaleItem] = []
    unc: list[str] = []
    is_rec = reason == TerminationReason.RECOMMENDATION_READY and dec is not None
    selected = opts.get(dec.selected_option_id) if opts and dec else None
    sop_route = None
    if sop_only:
        sop_route = "PROVIDE_SOP" if is_rec else (
            dec.selected_option_id if dec and dec.selected_option_id in {"REQUEST_INFO", "RETRIEVE_AGAIN"} else "ESCALATE")
    cmp = st.comparison
    compared = ([cmp.baseline_run_id] if cmp and cmp.baseline_run_id else []) + (cmp.comparable_run_ids if cmp else [])

    if is_rec:
        aid = dec.action_id
        spec = catalog.get(aid)
        run = st.succeeded_run(aid)
        imp = st.impact_for(run.run_id) if run else None
        if run and imp:
            rationale.append(RationaleItem(basis="experiment", refs=[run.run_id], statement=(
                f"[가상 예측] {aid} 실험 {run.run_id}: 원래 알람 회복={imp.original_alarm_recovered}, "
                f"회복 시간={imp.recovery_time_s}s(run 시작 기준), 한계 초과 {len(imp.exceedances)}건")))
            for m, dv in (cmp.metric_deltas.get(run.run_id, {}) if cmp else {}).items():
                rationale.append(RationaleItem(basis="experiment", refs=[run.run_id, cmp.baseline_run_id], statement=(
                    f"[가상 예측] 기준({cmp.baseline_run_id}) 대비 {m} {dv:+g} {imp.metrics[m].unit}")))
        for other in (cmp.comparable_run_ids if cmp else []):
            if run and other != run.run_id:
                oi = st.impact_for(other)
                rationale.append(RationaleItem(basis="experiment", refs=[other], statement=(
                    f"[가상 예측] 비교 후보 {other}: 한계 초과 {len(oi.exceedances) if oi else '?'}건, "
                    f"알람 회복={oi.original_alarm_recovered if oi else None}")))
        sel = case_selection(st, catalog, policy)
        if sel is not None:
            rationale.append(RationaleItem(basis="experiment", refs=[s.run_id for s in sel.steps] + [sel.baseline_run_id],
                                           statement=f"[가상 예측] case 탐색 {sel.status}: {sel.reason}"))
            for s in sel.steps:
                rationale.append(RationaleItem(basis="experiment", refs=[s.run_id], statement=(
                    f"[가상 예측] {s.action_id}: 기준안 대비 {'승' if s.beats_baseline else '패'}({s.vs_baseline})"
                    + (f", 최선 대비 {'승' if s.beats_incumbent else '패'}({s.vs_incumbent})"
                       if s.beats_incumbent is not None else ""))))
        opt = selected
        if opt:
            rationale.append(RationaleItem(basis="policy", refs=[c.name for c in opt.checks], statement=(
                "권고 허용 정책의 필수 검사 통과: " + ", ".join(c.name for c in opt.checks))))
            ev = {e.evidence_id: e for e in st.rag.evidence}
            for eid in opt.evidence_ids:
                e = ev[eid]
                rationale.append(RationaleItem(basis="rag_evidence", refs=[eid], statement=(
                    f"{e.doc_id} {e.doc_version or '(버전 미상)'} §{e.clause}: {e.text[:120]}")))
        for cid in rp.required_safety_checks:
            sc = st.safety(cid)
            rationale.append(RationaleItem(basis="safety_check", refs=[cid],
                                           statement=f"외부 안전 검사 {cid}={sc.status if sc else 'unknown'}"))
        for s in rp.required_fresh_sensors:
            o = st.latest_observation(s)
            if o:
                rationale.append(RationaleItem(basis="observation", refs=[s], statement=(
                    f"[실제 관측] {s}={o.value} {o.unit} @ {o.measured_at_s}s (sample {o.sample_id})")))
        if sop_only:
            summary = f"SOP 대응안: {opt.description if opt else aid}"
        else:
            summary = f"{aid} (case {spec.case_id if spec else '-'}) 권고 — 기준/후보 비교(가상 예측) 기반, 감독기·운영자 확인 필요"
    else:
        summary = f"운영자 검토 요청: {reason}" + (f" — {detail}" if detail else "")
        if sop_route in {"REQUEST_INFO", "RETRIEVE_AGAIN"}:
            summary = detail
        rationale.append(RationaleItem(basis="agent_state", statement=summary))
        for o in (opts.options if opts else []):
            if o.status != OptionStatus.OPEN:
                rationale.append(RationaleItem(basis="agent_state", refs=[o.option_id],
                                               statement=f"{o.option_id} {o.status}: {o.reason}"))

    if not sop_only:
        unc.append("모든 대응 효과·후속 영향은 시뮬레이션 예측이며 실제 공정에서 확인되지 않았다.")
    if tools_virtual and st.tool_history:
        unc.append("이번 실행의 도구 결과는 가짜(가상) 도구가 만든 값이다.")
    if client_kind == "fake":
        unc.append("Zoro 실제 호출이 아닌 가짜 클라이언트 응답이다.")
    unc.append("모델 선택 분포는 행동 선택 분포이며 안전·회복 확률이 아니다.")
    for cid in rp.advisory_safety_checks + rp.required_safety_checks:
        sc = st.safety(cid)
        if sc is None or sc.status != CheckStatus.PASS:
            unc.append(f"안전 검사 {cid} 상태 {sc.status if sc else '없음'} — 확인 필요")
    for u in st.rag.unresolved:
        unc.append(f"[검색으로 판단 불가] {u.topic}: {u.detail}")
    for w in st.rag.warnings:
        unc.append(f"[RAG 정규화] {w}")
    if cmp:
        for rid, why in cmp.not_comparable.items():
            unc.append(f"{rid} 비교 제외: {why}")
    for m in st.missing_info:
        unc.append(f"미확보 정보: {m.item} ({m.reason})")
    for sid in dict.fromkeys(o.sensor_id for o in st.observations):
        s, d = sensor_freshness(st, sid, policy.freshness)
        if s != CheckStatus.PASS:
            unc.append(f"관측 신선도 {s}: {d}")

    return FinalOutput(
        event_id=st.event_id, state_version=st.state_version,
        outcome="recommendation" if is_rec else "human_review", termination_reason=reason,
        recommended_action_id=dec.action_id if is_rec else None,
        setpoint_command=build_setpoint_command(st, catalog, policy, dec.action_id if is_rec else None, summary),
        summary=summary, rationale=rationale, uncertainties=unc, compared_run_ids=compared,
        sop_route=sop_route,
        sop_sources=[e for e in st.rag.evidence if selected and e.evidence_id in selected.evidence_ids]
                    if sop_route == "PROVIDE_SOP" else [],
        requested_sensor_ids=selected.requested_sensor_ids if sop_route == "REQUEST_INFO" and selected else [],
        selection_distribution=(dec.selection_distribution.probabilities
                                if dec and dec.selection_distribution else None),
        case_selection=case_selection(st, catalog, policy),
        model_client_kind=client_kind, tools_virtual=tools_virtual, disclaimer=DISCLAIMER,
    )


def build_setpoint_command(st: AgentState, catalog: ActionCatalog, policy: AgentPolicy,
                           action_id: str | None, reason: str) -> SetpointCommand:
    """권고 action 의 case 를 명령으로. 권고가 없으면 기준(hold) case — 바꿀 근거가 부족하면 현재 목표 유지."""
    base_spec = catalog.get(policy.recommendation.baseline_action_id)
    spec = catalog.get(action_id) if action_id else base_spec
    case = catalog.case(spec.case_id) if spec else None
    if action_id is None and case is not None and case.policy != "hold":
        case = None
    if case is None:
        return SetpointCommand(
            decision="hold", case_id=None, reason=reason + " | 등록 hold case 없음 — 현재 목표 유지",
            setpoint_changes=[], sop_refs=[], expected_cost_direction={}, expected_direction_basis=None,
            simulated_effects=[], watch_signals=[], issued_sim_time_s=st.sim_time_s, review_at_sim_time_s=None,
            revert_rules=[], values_status="example")
    resolved = resolve_case(case, catalog.setpoint_map(), st.active_setpoints, st.valve_positions())
    effects: list[SimulatedEffect] = []
    cmp = st.comparison
    run = st.succeeded_run(action_id) if action_id else None
    if run and cmp and cmp.baseline_run_id and run.run_id in cmp.metric_deltas:
        imp = st.impact_for(run.run_id)
        for m, dv in cmp.metric_deltas[run.run_id].items():
            effects.append(SimulatedEffect(metric=m, delta=dv, unit=imp.metrics[m].unit, run_id=run.run_id,
                                           baseline_run_id=cmp.baseline_run_id))
    return SetpointCommand(
        decision=case.policy, case_id=case.case_id,
        reason=reason if action_id else "권고 조건 미충족 → 현재 목표 유지(hold). " + reason,
        setpoint_changes=resolved.setpoint_changes, sop_refs=case.sop_refs,
        expected_cost_direction=case.expected_cost_direction, expected_direction_basis=case.expected_direction_basis,
        simulated_effects=effects, watch_signals=case.watch_signals, issued_sim_time_s=st.sim_time_s,
        review_at_sim_time_s=st.sim_time_s + case.review_after_s, revert_rules=case.revert_rules,
        values_status=resolved.values_status)
