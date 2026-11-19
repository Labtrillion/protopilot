from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from zoro_agent.agent import ZoroAgent
from zoro_agent.common import CheckStatus, find_forbidden_keys
from zoro_agent.inputs import EventInput, Observation
from zoro_agent.options import OptionStatus, build_options, evaluate_condition
from zoro_agent.outputs import TerminationReason
from zoro_agent.scenario import load_scenario
from zoro_agent.state import AgentState, apply_tool_result, assess_comparison, sensor_freshness
from zoro_agent.tool_io import (
    ExperimentConditions,
    ExternalComparison,
    ImpactResult,
    SensorQueryPayload,
    SimulationPayload,
    SimulationRun,
    ToolError,
    ToolRequest,
    ToolResult,
)
from zoro_agent.tools import FakeToolkit, ToolExecutor
from zoro_agent.zoro import (
    QUESTION_ID,
    FakeZoroClient,
    ScriptedZoroClient,
    ZoroClientError,
    ZoroHttpConfig,
    build_zoro_request,
)

SCEN = Path(__file__).resolve().parents[1] / "examples" / "scenario_tep_purge"


@pytest.fixture()
def sc():
    return load_scenario(SCEN)


def make_agent(sc, client, script=None, policy=None):
    ex = ToolExecutor(FakeToolkit(script or sc.fake_tools).tools(), virtual=True)
    return ZoroAgent(sc.catalog, policy or sc.policy, client, ex), ex


def choice(c, probs=None):
    return {"answers": {QUESTION_ID: {"type": "choice", "choice": c, "confidence": 0.9,
                                      "probabilities": probs or {c: 0.9}}}}


def fresh_state(sc) -> AgentState:
    """XMEAS(30) 을 최신값으로 갱신한 상태."""
    st = AgentState.from_inputs(sc.event, sc.rag)
    st.observations.append(Observation(sensor_id="XMEAS(30)", value=0.83, unit="mol%", measured_at_s=21590,
                                       quality="good", sample_id="PURGE-0117"))
    return st


def treq(action_id, tool, args, rid="T1", snap="SNAP-TEP-0001-T21600"):
    return ToolRequest(tool_request_id=rid, decision_id="D1", event_id="EVT-TEP-0001", state_version=0,
                       action_id=action_id, tool_name=tool, args=args, snapshot_id=snap, issued_sim_time_s=21600)


def sim_result(rid, action_id, run_id, status="succeeded", sample="PURGE-0117", horizon=1800, impact=True):
    run = SimulationRun(run_id=run_id, snapshot_id="SNAP-TEP-0001-T21600", action_id=action_id, case_id="X",
                        conditions=ExperimentConditions(snapshot_id="SNAP-TEP-0001-T21600", horizon_s=horizon,
                                                        simulator_version="v", disturbance_profile_id="D",
                                                        initial_composition_sample_id=sample),
                        status=status)
    imp = ImpactResult(run_id=run_id, original_alarm_recovered=True, recovery_time_s=500,
                       metrics={"m": {"value": 1.0, "unit": "t"}}) if impact else None
    return ToolResult(tool_request_id=rid, tool_name="run_simulation", status=status,
                      payload=SimulationPayload(run=run, impact=imp), is_virtual=True)


# 1 ------------------------------------------------------------------ 미등록 행동
def test_unregistered_action_is_never_executed(sc):
    opts = build_options(AgentState.from_inputs(sc.event, sc.rag), sc.catalog, sc.policy, {"sensor_query"})
    unm = opts.get("UNMAPPED:XMV(6) 100% 수동 개방")
    assert unm.status == OptionStatus.EXCLUDED and "UNMAPPED:XMV(6) 100% 수동 개방" not in opts.open_ids()

    for bad in ("UNMAPPED:XMV(6) 100% 수동 개방", "open_valve(TV-201, 80)"):
        agent, ex = make_agent(sc, ScriptedZoroClient([choice(bad)]))
        rep = agent.run(sc.event, sc.rag)
        assert ex.calls == [] and rep.tool_requests == []
        assert rep.final.termination_reason == TerminationReason.MODEL_RESPONSE_INVALID
        assert rep.final.outcome == "human_review"

    # 도구 실행기도 등록되지 않은 도구 이름은 실행하지 않는다
    ex = ToolExecutor({}, virtual=True)
    r = ex.execute(treq("X", "set_valve", {}))
    assert r.status == "failed" and r.error.code == "tool_not_registered" and ex.calls == []


# 2 ------------------------------------------------------------------ unknown ≠ pass
def test_unknown_condition_is_not_pass(sc):
    st = fresh_state(sc)
    status, _ = evaluate_condition("COND_PRODUCT_QUALITY_OK", st, sc.catalog, sc.policy)
    assert status == CheckStatus.UNKNOWN
    # 정의되지 않은 조건도 unknown
    assert evaluate_condition("NOPE", st, sc.catalog, sc.policy)[0] == CheckStatus.UNKNOWN

    # 측정 시각 없는 값 → unknown → 시뮬레이션 보류(open 아님)
    st2 = AgentState.from_inputs(sc.event, sc.rag)
    st2.observations.append(Observation(sensor_id="XMEAS(30)", value=0.8, unit="mol%", measured_at_s=None,
                                        quality="good"))
    st2.observations = [o for o in st2.observations if not (o.sensor_id == "XMEAS(30)" and o.measured_at_s == 20100)]
    assert sensor_freshness(st2, "XMEAS(30)", sc.policy.freshness)[0] == CheckStatus.UNKNOWN
    opts = build_options(st2, sc.catalog, sc.policy, {"run_simulation", "sensor_query", "relation_query"})
    assert opts.get("SIM_HOLD").status == OptionStatus.DEFERRED

    # 외부 안전 검사 unknown 이 필수인 정책이면 권고는 열리지 않는다
    pol = sc.policy.model_copy(deep=True)
    pol.recommendation.required_safety_checks = ["SC-PRODUCT-QUALITY-IN-SPEC"]
    agent, _ = make_agent(sc, FakeZoroClient(sc.fake_model_preference), policy=pol)
    rep = agent.run(sc.event, sc.rag)
    assert rep.final.outcome == "human_review"
    assert all(not o.option_id.startswith("RECOMMEND:") or o.status != OptionStatus.OPEN
               for ex in rep.exchanges for o in ex.options.options)


# 3 ------------------------------------------------------------------ 조회 ≠ 최신화
def test_stale_value_not_refreshed_by_query_alone(sc):
    st = AgentState.from_inputs(sc.event, sc.rag)
    req = treq("QUERY_PURGE_ANALYZER", "sensor_query", {"sensor_ids": ["XMEAS(30)"]})
    old = Observation(sensor_id="XMEAS(30)", value=0.91, unit="mol%", measured_at_s=20100, quality="good",
                      sample_id="PURGE-0112")
    res = ToolResult(tool_request_id="T1", tool_name="sensor_query", status="succeeded",
                     payload=SensorQueryPayload(observations=[old]), finished_sim_time_s=21660, is_virtual=True)
    rec = apply_tool_result(st, req, res)
    assert rec.status == "succeeded" and rec.info_updated is False
    assert sensor_freshness(st, "XMEAS(30)", sc.policy.freshness)[0] == CheckStatus.FAIL
    assert len([o for o in st.observations if o.sensor_id == "XMEAS(30)"]) == 1
    opts = build_options(st, sc.catalog, sc.policy, {"run_simulation", "sensor_query"})
    assert opts.get("SIM_SP19_DN050").status == OptionStatus.DEFERRED

    # 시료 ID 없이 같은 시각 값이 다시 와도 최신값이 아니다
    st.observations = [o.model_copy(update={"sample_id": None}) for o in st.observations]
    res2 = res.model_copy(update={"tool_request_id": "T2", "payload": SensorQueryPayload(
        observations=[old.model_copy(update={"sample_id": None})])})
    rec2 = apply_tool_result(st, req.model_copy(update={"tool_request_id": "T2"}), res2)
    assert rec2.info_updated is False
    assert sensor_freshness(st, "XMEAS(30)", sc.policy.freshness)[0] == CheckStatus.FAIL


# 4 ------------------------------------------------------------------ 실패 결과 미반영
def test_failed_tool_result_is_not_applied(sc):
    st = AgentState.from_inputs(sc.event, sc.rag)
    new = Observation(sensor_id="XMEAS(30)", value=0.7, unit="mol%", measured_at_s=21599, quality="good",
                      sample_id="LAB-9")
    res = ToolResult(tool_request_id="T1", tool_name="sensor_query", status="failed",
                     payload=SensorQueryPayload(observations=[new]),
                     error=ToolError(code="timeout", message="analyzer timeout", recoverable=True), is_virtual=True)
    rec = apply_tool_result(st, treq("QUERY_PURGE_ANALYZER", "sensor_query", {"sensor_ids": ["XMEAS(30)"]}), res)
    assert rec.info_updated is False
    assert st.latest_observation("XMEAS(30)").sample_id == "PURGE-0112"

    st2 = fresh_state(sc)
    rec2 = apply_tool_result(st2, treq("SIM_HOLD", "run_simulation", {}),
                             sim_result("T1", "SIM_HOLD", "R1", status="failed"))
    assert rec2.info_updated is False and st2.impacts == []
    assert st2.runs[0].status == "failed"

    # 복구 불가 오류 → 반복 종료
    script = copy.deepcopy(sc.fake_tools)
    script["sensor_query"]["XMEAS(30)"] = [{"status": "failed", "error": {"code": "down", "message": "analyzer offline",
                                                                      "recoverable": False}}]
    agent, _ = make_agent(sc, FakeZoroClient(sc.fake_model_preference), script=script)
    rep = agent.run(sc.event, sc.rag)
    assert rep.final.termination_reason == TerminationReason.UNRECOVERABLE_TOOL_ERROR
    assert all(o.sample_id != "PURGE-0117" for o in rep.final_state.observations)


# 5 ------------------------------------------------------------------ 중복 실행 방지
def test_duplicate_running_or_completed_experiments_blocked(sc):
    tools = {"run_simulation", "sensor_query", "relation_query"}
    st = fresh_state(sc)
    apply_tool_result(st, treq("SIM_HOLD", "run_simulation", {}, rid="T1"),
                      sim_result("T1", "SIM_HOLD", "R1", status="running", impact=False))
    o = build_options(st, sc.catalog, sc.policy, tools).get("SIM_HOLD")
    assert o.status == OptionStatus.DEFERRED and "진행 중" in o.reason

    st = fresh_state(sc)
    apply_tool_result(st, treq("SIM_HOLD", "run_simulation", {}, rid="T1"),
                      sim_result("T1", "SIM_HOLD", "R1"))
    o = build_options(st, sc.catalog, sc.policy, tools).get("SIM_HOLD")
    assert o.status == OptionStatus.EXCLUDED and "이미 완료" in o.reason

    # 모델이 완료된 실험을 다시 고르면 거부
    agent, ex = make_agent(sc, ScriptedZoroClient([
        choice("QUERY_PURGE_ANALYZER"), choice("QUERY_PURGE_ANALYZER"),
        choice("SIM_HOLD"), choice("SIM_HOLD")]))
    rep = agent.run(sc.event, sc.rag)
    assert [c.action_id for c in ex.calls].count("SIM_HOLD") == 1
    assert rep.final.termination_reason == TerminationReason.MODEL_RESPONSE_INVALID


# 6 ------------------------------------------------------------------ 비교 조건
def test_runs_with_different_conditions_not_treated_as_ab(sc):
    st = fresh_state(sc)
    apply_tool_result(st, treq("SIM_HOLD", "run_simulation", {}, rid="T1"),
                      sim_result("T1", "SIM_HOLD", "R1", sample="PURGE-0112"))
    apply_tool_result(st, treq("SIM_SP19_DN050", "run_simulation", {}, rid="T2"),
                      sim_result("T2", "SIM_SP19_DN050", "R2", sample="PURGE-0117"))
    apply_tool_result(st, treq("SIM_SP19_UP050", "run_simulation", {}, rid="T3"),
                      sim_result("T3", "SIM_SP19_UP050", "R3", sample="PURGE-0112", horizon=600))
    cmp = assess_comparison(st, "SIM_HOLD")
    assert cmp.comparable_run_ids == []
    assert "initial_composition_sample_id" in cmp.not_comparable["R2"]
    assert "horizon_s" in cmp.not_comparable["R3"]
    st.comparison = cmp
    opts = build_options(st, sc.catalog, sc.policy, {"run_simulation"})
    assert opts.get("RECOMMEND:SIM_SP19_DN050").status == OptionStatus.EXCLUDED

    # 외부 비교 모듈이 비교 불가로 판정하면 로컬 판정이 통과해도 제외
    st2 = fresh_state(sc)
    apply_tool_result(st2, treq("SIM_HOLD", "run_simulation", {}, rid="T1"),
                      sim_result("T1", "SIM_HOLD", "R1"))
    apply_tool_result(st2, treq("SIM_SP19_DN050", "run_simulation", {}, rid="T2"),
                      sim_result("T2", "SIM_SP19_DN050", "R2"))
    assert assess_comparison(st2, "SIM_HOLD").comparable_run_ids == ["R2"]
    st2.external_comparisons.append(ExternalComparison(comparison_id="C1", baseline_run_id="R1", run_ids=["R2"],
                                                       comparable=False, reasons_not_comparable=["외란 상이"]))
    assert assess_comparison(st2, "SIM_HOLD").comparable_run_ids == []


# 7 ------------------------------------------------------------------ 이전 결과 → 다음 요청
def test_previous_results_reflected_in_next_request(sc):
    client = FakeZoroClient(sc.fake_model_preference)
    agent, _ = make_agent(sc, client)
    rep = agent.run(sc.event, sc.rag)
    r1, r2, r3, r4 = (client.requests[i] for i in range(4))
    assert r1.state["history"] == []
    h = r2.state["history"][0]
    assert h["selected"] == "QUERY_PURGE_ANALYZER" and h["info_updated"] is False
    ai = lambda r: next(o for o in r.state["actual_observations"]["latest"] if o["sensor_id"] == "XMEAS(30)")
    assert ai(r2)["sample_id"] == "PURGE-0112" and ai(r3)["sample_id"] == "PURGE-0117" and ai(r3)["freshness"] == "pass"
    assert "SIM_HOLD" not in r2.questions[QUESTION_ID].criteria
    assert "SIM_HOLD" in r3.questions[QUESTION_ID].criteria
    assert r4.state["virtual_experiments"]["runs"][0]["action_id"] == "SIM_HOLD"
    assert r4.state["event"]["state_version"] > r1.state["event"]["state_version"]
    assert rep.final.outcome == "recommendation"
    assert rep.final.recommended_action_id == "SIM_SP19_DN050"
    # 모든 결정은 서로 다른 요청이며 ID 로 연결된다
    assert len({d.request_id for d in rep.decisions}) == len(rep.decisions)
    for tr in rep.tool_requests:
        assert any(d.decision_id == tr.decision_id for d in rep.decisions)


# 8 ------------------------------------------------------------------ 실제 vs 가상
def test_actual_and_virtual_data_are_separated(sc):
    client = FakeZoroClient(sc.fake_model_preference)
    agent, _ = make_agent(sc, client)
    rep = agent.run(sc.event, sc.rag)
    st = rep.final_state
    assert all(o.origin == "plant_measurement" for o in st.observations)
    assert all(r.kind == "simulation_prediction" for r in st.runs)
    assert all(i.kind == "simulation_prediction" for i in st.impacts)
    n_obs_input = len(sc.event.observations)
    assert len(st.observations) == n_obs_input + 1  # 실제 조회로 얻은 새 측정 1건만 추가
    with pytest.raises(ValidationError):
        Observation(origin="simulation_prediction", sensor_id="XMEAS(7)", value=1, unit="kPa(g)",
                    measured_at_s=1, quality="good")
    last = client.requests[-1].state
    assert "virtual" not in json.dumps(last["actual_observations"])
    assert last["virtual_experiments"]["runs"]
    assert rep.final.simulation_results_are_predictions and rep.final.tools_virtual
    assert rep.final.model_client_kind == "fake"


# 9 ------------------------------------------------------------------ 허용 목록 밖 선택
def test_out_of_list_model_choice_is_rejected(sc):
    for body in (choice("SIM_CHANGE_PRODUCTION_TARGET"),  # 등록됐지만 이번 요청에서 제외된 선택지
                 choice("SIM_HOLD"),  # 보류(오래된 조성) 중인 선택지
                 {"answers": {QUESTION_ID: {"type": "refusal"}}},
                 {"answers": {}},
                 choice("QUERY_PURGE_ANALYZER", {"QUERY_PURGE_ANALYZER": 0.5, "DROP_TABLE": 0.5}),
                 # 분포는 허용 키만 담았지만 선택 자체가 목록 밖
                 choice("SIM_CHANGE_PRODUCTION_TARGET", {"QUERY_PURGE_ANALYZER": 1.0})):
        agent, ex = make_agent(sc, ScriptedZoroClient([body]))
        rep = agent.run(sc.event, sc.rag)
        assert ex.calls == []
        assert rep.decisions[0].validation.valid is False
        assert rep.decisions[0].selected_option_id is None
        assert rep.final.termination_reason == TerminationReason.MODEL_RESPONSE_INVALID


# 10 ----------------------------------------------------------------- 한도
def test_loop_stops_at_limits(sc):
    pol = sc.policy.model_copy(deep=True)
    pol.budget.max_decisions = 2
    agent, ex = make_agent(sc, FakeZoroClient(sc.fake_model_preference), policy=pol)
    rep = agent.run(sc.event, sc.rag)
    assert rep.final.termination_reason == TerminationReason.LIMIT_REACHED
    assert len(rep.exchanges) == 2 and rep.final.outcome == "human_review"

    pol = sc.policy.model_copy(deep=True)
    pol.budget.max_tool_calls = 3  # 조회 2 + 시뮬레이션 1 후 더 이상 도구 불가
    agent, ex = make_agent(sc, FakeZoroClient(sc.fake_model_preference), policy=pol)
    rep = agent.run(sc.event, sc.rag)
    assert len(ex.calls) == 3
    assert rep.final.termination_reason == TerminationReason.LIMIT_REACHED


# ------------------------------------------------------------------ 추가 안전장치
def test_hidden_ground_truth_never_reaches_model(sc):
    raw = json.loads((SCEN / "event_input.json").read_text(encoding="utf-8"))
    raw["fault_id"] = "FAULT-X"
    with pytest.raises(ValidationError):  # 숨은 정답 필드는 입력 단계에서 거부
        EventInput.model_validate(raw)
    assert find_forbidden_keys({"a": [{"hidden_cause": 1}], "fault_schedule": []}) == [
        "$.a[0].hidden_cause", "$.fault_schedule"]
    client = FakeZoroClient(sc.fake_model_preference)
    make_agent(sc, client)[0].run(sc.event, sc.rag)
    truth = json.loads((SCEN / "evaluation_only.json").read_text(encoding="utf-8"))
    for r in client.requests:
        dumped = json.dumps(r.model_dump(), ensure_ascii=False)
        assert find_forbidden_keys(r.model_dump()) == []
        assert truth["fault_id"] not in dumped and truth["hidden_cause"] not in dumped


def test_rag_adapter_does_not_invent_mappings(sc):
    by_ref = {c.raw_ref: c for c in sc.rag.candidates}
    assert by_ref["purge_b_setpoint_dn050"].action_id == "SIM_SP19_DN050"
    assert by_ref["purge_b_setpoint_dn050"].mapping == "alias"
    assert by_ref["SIM_SP19_UP050"].mapping == "exact"
    assert by_ref["XMV(6) 100% 수동 개방"].action_id is None
    assert by_ref["XMV(6) 100% 수동 개방"].unmapped_condition_refs == ["separator pressure check"]
    assert by_ref["SIM_SP19_UP050"].condition_ids == ["COND_OVERRIDE_INACTIVE", "COND_PURGE_ANALYZER_FRESH"]


def test_http_client_config_requires_env(monkeypatch):
    monkeypatch.delenv("ZORO_BASE_URL", raising=False)
    with pytest.raises(ZoroClientError):
        ZoroHttpConfig.from_env()
    monkeypatch.setenv("ZORO_BASE_URL", "http://localhost:30001/")
    monkeypatch.setenv("ZORO_REASONING", "auto")
    cfg = ZoroHttpConfig.from_env()
    assert cfg.base_url == "http://localhost:30001" and cfg.reasoning == "auto"


# ------------------------------------------------------------------ 시뮬레이터 case / 설정값 명령
def test_simulation_request_carries_registered_case(sc):
    agent, ex = make_agent(sc, FakeZoroClient(sc.fake_model_preference))
    rep = agent.run(sc.event, sc.rag)
    sims = [t for t in rep.tool_requests if t.tool_name == "run_simulation"]
    # UP150·UP200 은 XMV(6) 36.2% 에서 즉시 −39·−52 %p → 밸브 포화라 시험하지 않는다
    assert [t.case.case_id for t in sims] == ["CASE-HOLD", "CASE-SP19-UP050", "CASE-SP19-DN050", "CASE-SP19-UP100",
                                              "CASE-SP19-DN100", "CASE-SP19-DN150", "CASE-SP19-DN200"]
    hold, b = sims[0], sims[2]
    assert hold.case.policy == "hold" and hold.case.setpoint_changes == []
    ch = b.case.setpoint_changes[0]
    assert (ch.setpoint_id, ch.scheme_index, ch.loop_id, ch.target_value, ch.unit) == (
        "SETPT(19)", 19, "CONTRL19", 13.323, "mol%")
    assert ch.from_value == 13.823 and ch.from_source == "active_setpoint"
    # Studio 제어 코드로 계산한 하위 루프 즉시 변화
    sp6, xmv6 = ch.controller_response
    assert (sp6.loop, sp6.target, xmv6.loop, xmv6.target) == ("CONTRL19", "SETPT(6)", "CONTRL6", "XMV(6)")
    assert sp6.immediate_delta == pytest.approx(0.1067, abs=1e-4) and xmv6.immediate_delta == pytest.approx(13.01, abs=0.01)
    assert xmv6.value_before == 36.2 and xmv6.value_after == pytest.approx(49.21, abs=0.01)
    assert all(t.case.values_status == "example" for t in sims)
    # 실행된 case 는 모두 카탈로그 등록값 그대로다
    for t in sims:
        reg = sc.catalog.case(t.case.case_id)
        assert [(c.setpoint_id, c.target_value) for c in reg.setpoint_changes] == [
            (c.setpoint_id, c.target_value) for c in t.case.setpoint_changes]


def test_final_output_is_setpoint_command(sc):
    agent, _ = make_agent(sc, FakeZoroClient(sc.fake_model_preference))
    rep = agent.run(sc.event, sc.rag)
    cmd = rep.final.setpoint_command
    assert cmd.decision == "propose_setpoint" and cmd.case_id == "CASE-SP19-DN050"
    assert [(c.setpoint_id, c.target_value) for c in cmd.setpoint_changes] == [("SETPT(19)", 13.323)]
    assert {r.step_id for r in cmd.sop_refs} == {"check_purge_override", "simulate_purge_b_cases"}
    assert cmd.actual_effects is None and cmd.requires_supervisor_approval
    cmp = rep.final_state.comparison
    run = rep.final_state.succeeded_run("SIM_SP19_DN050")
    assert {e.metric: e.delta for e in cmd.simulated_effects} == cmp.metric_deltas[run.run_id]
    assert cmd.review_at_sim_time_s == cmd.issued_sim_time_s + 1800
    assert {r.rule_id for r in cmd.revert_rules} >= {"RV-OVERRIDE", "RV-STALE"}

    # 권고가 없으면 hold (현재 목표 유지), 설정값 변경 없음
    agent, _ = make_agent(sc, ScriptedZoroClient([choice("REQUEST_HUMAN_REVIEW")]))
    rep = agent.run(sc.event, sc.rag)
    cmd = rep.final.setpoint_command
    assert rep.final.outcome == "human_review"
    assert cmd.decision == "hold" and cmd.case_id == "CASE-HOLD" and cmd.setpoint_changes == []
    assert cmd.simulated_effects == []


def test_catalog_rejects_unregistered_or_out_of_range_setpoints(sc):
    base = json.loads((SCEN / "action_catalog.json").read_text(encoding="utf-8"))
    from zoro_agent.registry import ActionCatalog

    def bad(mut):
        raw = copy.deepcopy(base)
        mut(raw)
        with pytest.raises(ValidationError):
            ActionCatalog.model_validate(raw)

    bad(lambda r: r["cases"][1]["setpoint_changes"][0].update(target_value=20.0))      # 허용 범위 밖
    bad(lambda r: r["cases"][1]["setpoint_changes"][0].update(setpoint_id="XMV(6)"))   # 밸브 직접 명령
    bad(lambda r: r["cases"][1]["setpoint_changes"][0].update(unit="%"))               # 단위 불일치
    bad(lambda r: r["cases"][0].update(setpoint_changes=[{"setpoint_id": "SETPT(19)", "target_value": 12.5,
                                                          "unit": "mol%"}]))         # hold 에 변경
    bad(lambda r: r["actions"][3].update(case_id=None))                                # case 없는 시뮬레이션
    ActionCatalog.model_validate(base)


def test_setpoint_envelope_checked_against_current_state(sc):
    tools = {"run_simulation", "sensor_query", "relation_query"}
    st = fresh_state(sc)
    assert build_options(st, sc.catalog, sc.policy, tools).get("SIM_SP19_DN050").status == OptionStatus.OPEN

    st.active_setpoints = []  # 현재 목표 모름 → 보류
    assert build_options(st, sc.catalog, sc.policy, tools).get("SIM_SP19_DN050").status == OptionStatus.DEFERRED

    st = fresh_state(sc)  # 현재값에서 변경폭 초과 → 제외
    st.active_setpoints = [st.active_setpoints[0].model_copy(update={"value": 14.6})]
    o = build_options(st, sc.catalog, sc.policy, tools).get("SIM_SP19_DN200")  # 14.6 → 11.823
    assert o.status == OptionStatus.EXCLUDED and "변경폭" in o.reason

    st = fresh_state(sc)  # 밸브 현재값을 모르면 포화 여부를 판단할 수 없어 보류
    st.observations = [o for o in st.observations if o.sensor_id != "XMV(6)"]
    assert build_options(st, sc.catalog, sc.policy, tools).get("SIM_SP19_DN050").status == OptionStatus.DEFERRED

    st = fresh_state(sc)  # 직전 변경 직후 → 보류
    st.active_setpoints = [st.active_setpoints[0].model_copy(update={"since_s": 21000})]
    assert build_options(st, sc.catalog, sc.policy, tools).get("SIM_SP19_DN050").status == OptionStatus.DEFERRED


# ------------------------------------------------------------------ 벤치마크 (심슨 대비)
from zoro_agent.alarm_adapter import adapt_alarm_event  # noqa: E402
from zoro_agent.benchmark import (  # noqa: E402
    RunScore, TrackedVariable, beats, closeness, evaluation_spec, read_studio_csv, score_timeseries, select_case)

RAG_DIR = Path(__file__).resolve().parents[1] / "zoro-rag"


def rs(rid, acc, upm, ok=True, vol=100.0):
    return RunScore(run_id=rid, tripped=False, usd_per_m3=upm, cost_usd={"total": upm * vol}, product_m3=vol,
                    tracking_accuracy=acc, per_variable_accuracy={}, in_threshold_fraction=1.0 if ok else 0.0,
                    time_to_stable_s=0 if ok else None, final_window_inside=ok, quality_ok=True,
                    failed_gates=[] if ok else ["final_window_inside"])


@pytest.mark.skipif(not (RAG_DIR / "validation").exists(), reason="zoro-rag 검증 CSV 없음")
def test_cost_matches_rag_studio_eval(sc):
    ref = json.loads((RAG_DIR / "validation" / "measured_results.json").read_text(encoding="utf-8"))
    for name in ("nominal", "cooling_step", "cooling_combined"):
        t, x, tripped = read_studio_csv(RAG_DIR / "validation" / f"{name}.csv")
        s = score_timeseries(name, t, x, sc.policy.benchmark.tracked, sc.policy.benchmark, tripped)
        assert s.cost_usd["total"] == pytest.approx(ref[name]["cost_usd"]["total"], rel=1e-9)
        assert s.usd_per_m3 == pytest.approx(ref[name]["usd_per_m3"], rel=1e-9)
        assert s.product_m3 == pytest.approx(ref[name]["product_m3"], rel=1e-9)
        assert not tripped and s.failed_gates == []


def test_accuracy_is_closeness_to_setpoint_within_threshold():
    v = TrackedVariable(tag="XMEAS(7)", setpoint=2705, unit="kPa(g)", hi=2895, lo=None, source="t")
    assert closeness(v, 2705) == 1 and closeness(v, 2800) == pytest.approx(0.5)
    assert closeness(v, 2895) == 0 and closeness(v, 2950) == 0
    assert closeness(v, 2610) == pytest.approx(0.5)  # 단측 경보: 반대쪽도 같은 거리
    w = TrackedVariable(tag="XMEAS(9)", setpoint=120.4, unit="degC", hi=121.4, lo=120.0, source="t")
    assert closeness(w, 120.2) == pytest.approx(0.5) and closeness(w, 120.9) == pytest.approx(0.5)


def test_beats_requires_eligibility_then_margin(sc):
    b = sc.policy.benchmark
    base = rs("S", 0.80, 7.60)
    assert beats(rs("A", 0.84, 7.60), base, base, b, b.vs_baseline)[0]          # 정확도 +0.04 ≥ 0.03
    assert not beats(rs("A", 0.82, 7.60), base, base, b, b.vs_baseline)[0]      # +0.02 < 0.03
    assert beats(rs("A", 0.80, 7.50), base, base, b, b.vs_baseline)[0]          # 비용 −1.3% ≥ 1%
    assert not beats(rs("A", 0.90, 7.66), base, base, b, b.vs_baseline)[0]      # 비용 +0.8% > 허용 0.5%
    assert not beats(rs("A", 0.78, 7.40), base, base, b, b.vs_baseline)[0]      # 정확도 −0.02 > 허용 0.01
    assert not beats(rs("A", 0.95, 7.00, ok=False), base, base, b, b.vs_baseline)[0]  # 부적격은 못 이김
    bad = rs("S", 0.50, 7.60, ok=False)
    assert beats(rs("A", 0.60, 7.90), bad, bad, b, b.vs_baseline)[0]            # 심슨 부적격, 후보 적격
    assert not beats(rs("A", 0.90, 7.00, vol=98.0), base, base, b, b.vs_baseline)[0]  # 생산량 99% 미만


def test_case_search_stops_by_patience(sc):
    b = sc.policy.benchmark
    base = rs("S", 0.70, 7.60)
    seq = [(rs("C1", 0.71, 7.60), "C1"), (rs("C2", 0.80, 7.60), "C2"), (rs("C3", 0.81, 7.60), "C3"),
           (rs("C4", 0.75, 7.55), "C4"), (rs("C5", 0.80, 7.70), "C5")]
    sel = select_case(base, seq, remaining=3, policy=b)
    assert sel.status == "continue" and sel.selected_run_id == "C2" and sel.since_incumbent == 3
    sel = select_case(base, seq + [(rs("C6", 0.79, 7.60), "C6")], remaining=2, policy=b)
    assert sel.status == "select_case" and sel.selected_action_id == "C2"   # 이후 4개 모두 C2 를 못 이김
    seq2 = seq[:2] + [(rs("C3", 0.83, 7.60), "C3")]                           # +0.03 ≥ 0.02 → 교체, 카운터 초기화
    assert select_case(base, seq2, remaining=4, policy=b).selected_run_id == "C3"
    assert select_case(base, seq[:2], remaining=0, policy=b).status == "select_case"  # 남은 case 없음
    assert select_case(base, [seq[0]], remaining=0, policy=b).status == "select_baseline"
    badbase = rs("S", 0.4, 7.6, ok=False)
    assert select_case(badbase, [(rs("C1", 0.5, 7.6, ok=False), "C1")], remaining=0,
                       policy=b).status == "none_acceptable"


def test_agent_stops_case_search_and_closes_remaining_cases(sc):
    agent, ex = make_agent(sc, FakeZoroClient(sc.fake_model_preference))
    rep = agent.run(sc.event, sc.rag)
    ran = [c.action_id for c in ex.calls]
    assert "SIM_SP19_UP150" not in ran and "SIM_SP19_UP200" not in ran
    sel = rep.final.case_selection
    assert sel.status == "select_case" and sel.selected_action_id == "SIM_SP19_DN050" and sel.since_incumbent == 4
    last = rep.exchanges[-1].options
    assert last.get("SIM_SP19_UP200").status == OptionStatus.EXCLUDED
    assert "밸브 포화" in last.get("SIM_SP19_UP200").reason
    assert last.get("RECOMMEND:SIM_SP19_DN100").status == OptionStatus.EXCLUDED
    # 모든 case 시뮬레이션 요청에 같은 평가 명세가 붙는다
    specs = [t.evaluation for t in rep.tool_requests if t.tool_name == "run_simulation"]
    assert all(s == specs[0] for s in specs)
    xm7 = next(v for v in specs[0].tracked if v.tag == "XMEAS(7)")
    assert xm7.hi == 2895.0 and xm7.weight == sc.policy.benchmark.alarm_weight


def test_hold_recommended_when_no_case_beats_eligible_simpson(sc):
    script = copy.deepcopy(sc.fake_tools)
    sims = script["simulation"]
    for k, v in sims.items():
        if k == "defaults":
            continue
        s = v["impact"]["score"]
        if k == "CASE-HOLD":
            s.update(final_window_inside=True, failed_gates=[], tracking_accuracy=0.80, usd_per_m3=7.60)
            v["impact"].update(original_alarm_recovered=True, exceedances=[])
        else:
            s.update(tracking_accuracy=0.79, usd_per_m3=7.61)
    agent, ex = make_agent(sc, FakeZoroClient(sc.fake_model_preference), script=script)
    rep = agent.run(sc.event, sc.rag)
    assert rep.final.case_selection.status == "select_baseline"
    assert rep.final.outcome == "recommendation" and rep.final.recommended_action_id == "SIM_HOLD"
    cmd = rep.final.setpoint_command
    assert cmd.decision == "hold" and cmd.setpoint_changes == []


def test_alarm_event_adapter():
    raw = json.loads((SCEN / "alarm_event.json").read_text(encoding="utf-8"))
    raw["simulation_context"]["disturbance_id"] = "IDV(6)"
    r = adapt_alarm_event(raw, snapshot_id="SNAP-X")
    ev = r.event
    a = ev.alarms[0]
    assert (a.sensor_id, a.condition, a.threshold, a.raw_tag, a.active) == ("XMEAS(7)", "high", 2895.0, "PIR100", True)
    assert "IDV(6)" not in ev.model_dump_json() and "simulation_context" in r.dropped_fields
    assert {o.sensor_id for o in ev.observations} == {"XMEAS(7)"}  # 단위 없는 XMEAS9·XMV10 은 제외
    assert all(o.quality == "uncertain" and o.measured_at_s == 3600 for o in ev.observations)
    assert any("단위 미제공" in w for w in r.warnings)
    with pytest.raises(ValueError):
        adapt_alarm_event({**raw, "schema_version": "v0"}, snapshot_id="S")


# ------------------------------------------------------------------ 지연님 RAG 출력 연결
def test_jiyeon_rag_output_drives_agent():
    sc2 = load_scenario(SCEN, rag="jiyeon")
    raw = sc2.rag_raw
    assert sc2.rag.adapter == "rag_jiyeon_alarm_result_v1"
    plan_ids = {c.action_id for c in sc2.rag.candidates}
    assert {"SIM_HOLD", "SIM_SP19_DN050", "SIM_SP19_UP200"} <= plan_ids and None not in plan_ids
    dn = next(c for c in sc2.rag.candidates if c.action_id == "SIM_SP19_DN050")
    assert dn.condition_ids == ["COND_OVERRIDE_INACTIVE", "COND_PURGE_ANALYZER_FRESH"] and not dn.unmapped_condition_refs
    assert all(e.doc_version and e.doc_version.startswith("sha256:") for e in sc2.rag.evidence)
    assert "alarm_context" in raw and "IDV" not in sc2.rag.model_dump_json()
    client = FakeZoroClient(sc2.fake_model_preference)
    agent, _ = make_agent(sc2, client)
    rep = agent.run(sc2.event, sc2.rag)
    assert rep.final.recommended_action_id == "SIM_SP19_DN050"
    used = {r for item in rep.final.rationale if item.basis == "rag_evidence" for r in item.refs}
    assert used and used <= {e["evidence_id"] for e in raw["evidence"]}
    for r in client.requests:
        assert find_forbidden_keys(r.model_dump()) == []
