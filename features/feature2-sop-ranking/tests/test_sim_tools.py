"""SimulationToolkit(실제 시뮬레이터 어댑터)와 ProtoPilot case 구성 — 가짜 분기 실행기로 검사."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sop_ranking.agent import ZoroAgent
from sop_ranking.cases import SimulatorCase
from sop_ranking.inputs import Alarm, EventInput, Observation
from sop_ranking.cases import ActiveSetpoint
from sop_ranking.outputs import TerminationReason
from sop_ranking.protopilot_cases import build_alarm_catalog, build_benchmark, build_policy, sop_rag
from sop_ranking.sim_tools import BranchOutcome, SimulationError, SimulationToolkit
from sop_ranking.tools import ToolExecutor
from sop_ranking.decision import FakeZoroClient, ScriptedZoroClient

# server/demo-sops.json 의 TEP-PRESSURE-01 과 같은 구조
SOP = {"id": "TEP-PRESSURE-01", "revision": 1, "title": "반응기 압력 상한", "tags": ["XMEAS(7)"], "side": "high",
       "source_url": "https://doi.org/10.1016/0098-1354(93)80018-I",
       "checks": ["압력 계측값과 알람 기준을 확인합니다.", "유지와 감량을 비교할 때 모델 트립과 정상 운전 범위 이탈 시간을 먼저 봅니다."],
       "candidates": ["hold", "reduce_now"]}
SETPOINTS = {"reactor_pressure": 2705.0, "reactor_temperature": 120.4, "reactor_level": 65.0, "separator_level": 50.0,
             "stripper_level": 50.0, "production_rate": 22.89, "pct_g": 53.8}
RANGES = {"production_rate": ("m³/h", 1, 40), "reactor_pressure": ("kPa(g)", 2000, 2895)}
LIMITS = {"XMEAS(7)": (None, 2895), "XMEAS(8)": (50, 100), "XMEAS(9)": (None, 150),
          "XMEAS(12)": (30, 100), "XMEAS(15)": (30, 100)}
UNITS = {"XMEAS(7)": "kPa(g)", "XMEAS(8)": "%", "XMEAS(9)": "°C", "XMEAS(12)": "%", "XMEAS(15)": "%", "XMEAS(17)": "m³/h"}
BASE = {f"XMEAS({n})": 1.0 for n in range(1, 42)}
BASE.update({"XMEAS(8)": 65.0, "XMEAS(9)": 120.4, "XMEAS(10)": 0.337, "XMEAS(12)": 50.0, "XMEAS(15)": 50.0,
             "XMEAS(17)": 22.89, "XMEAS(19)": 230.0, "XMEAS(20)": 341.0})
for n, v in zip(range(29, 37), (32.2, 8.9, 26.4, 6.9, 18.8, 0.65, 4.8, 2.3)):
    BASE[f"XMEAS({n})"] = v
for n, v in zip(range(37, 42), (0.018, 0.836, 0.099, 53.7, 43.8)):
    BASE[f"XMEAS({n})"] = v


class FakeRunner:
    """감량하면 압력이 1시간에 걸쳐 2800 으로 내려가고, 유지하면 2905 에 머무는 가짜 분기 실행기."""

    def __init__(self, fail: SimulationError | None = None):
        self.calls: list[tuple[str, SimulatorCase, float]] = []
        self.fail = fail

    def run(self, snapshot_id, case, horizon_s):
        self.calls.append((snapshot_id, case, horizon_s))
        if self.fail:
            raise self.fail
        ch = case.setpoint_changes[0] if case.setpoint_changes else None
        times, samples = [], []
        for i in range(int(horizon_s // 180) + 1):
            t = i * 180.0
            x = dict(BASE)
            if ch and t >= ch.apply_at_s:
                k = min(1.0, (t - ch.apply_at_s) / 3600)
                x["XMEAS(7)"] = 2905 - 105 * k
                x["XMEAS(17)"] = ch.target_value
            else:
                x["XMEAS(7)"] = 2905.0
            times.append(t)
            samples.append(x)
        return BranchOutcome(times_s=times, samples=samples, tripped=False, simulator_version="fake-tep",
                             disturbance_profile_id="checkpoint:abc", seed=1)


def setup(runner):
    catalog = build_alarm_catalog(SOP, SETPOINTS, RANGES, ["XMEAS(7)"])
    bench = build_benchmark(SETPOINTS, LIMITS, UNITS)
    policy = build_policy(catalog, ["XMEAS(7)"], bench)
    event = EventInput(
        event_id="RUN1-A7", sim_time_s=3600.0, snapshot_id="RUN1:7", state_version=0,
        alarms=[Alarm(alarm_id="7", sensor_id="XMEAS(7)", condition="high", raised_at_s=3600.0, active=True,
                      threshold=2895.0, unit="kPa(g)")],
        observations=[Observation(sensor_id="XMEAS(7)", value=2905.0, unit="kPa(g)", measured_at_s=3600.0, quality="good")],
        active_setpoints=[ActiveSetpoint(setpoint_id=k, value=v, unit=RANGES.get(k, ("", 0, 0))[0], since_s=None)
                          for k, v in SETPOINTS.items()])
    kit = SimulationToolkit(runner, alarm_tags=["XMEAS(7)"], alarm_limits=LIMITS, units=UNITS)
    return catalog, policy, event, kit


def test_catalog_from_team_sop():
    catalog, policy, _, _ = setup(FakeRunner())
    reduce = catalog.case("CASE-REDUCE_NOW")
    assert reduce.setpoint_changes[0].setpoint_id == "production_rate"
    assert reduce.setpoint_changes[0].target_value == pytest.approx(22.89 * 0.95)
    assert catalog.resolve_action_ref("reduce_now") == ("SIM_REDUCE_NOW", "alias")
    assert policy.benchmark.production_ratio_min == 0.94
    assert [v.tag for v in policy.benchmark.tracked] == ["XMEAS(7)", "XMEAS(9)", "XMEAS(8)", "XMEAS(12)", "XMEAS(15)"]


def test_agent_runs_branches_through_adapter_and_decides():
    runner = FakeRunner()
    catalog, policy, event, kit = setup(runner)
    client = FakeZoroClient(["SIM_REDUCE_NOW", "RECOMMEND:SIM_REDUCE_NOW"])
    agent = ZoroAgent(catalog, policy, client, ToolExecutor(kit.tools(), virtual=False))
    rep = agent.run(event, sop_rag(SOP, catalog))
    assert rep.sop_stage.final.sop_route == "PROVIDE_SOP"
    assert rep.sop_stage.final.recommended_action_id == "SIM_REDUCE_NOW"
    assert rep.sop_stage.tool_requests == []
    assert "virtual_experiments" not in client.requests[0].state
    assert {question.type for question in client.requests[0].questions.values()} == {"choice", "noul", "score"}
    assert [c.case_id for _, c, _ in runner.calls] == ["CASE-REDUCE_NOW", *[
        f"CASE-REDUCE_NOW:VARIANT:{i}" for i in range(2, 6)]]
    assert all(snapshot == "RUN1:7" and horizon == policy.benchmark.horizon_s for snapshot, _, horizon in runner.calls)
    assert [c.setpoint_changes[0].target_value for _, c, _ in runner.calls] == pytest.approx([
        22.89 * 0.95 * (1 + step / 100) for step in [0, *policy.setpoint_variant_steps_pct]])
    assert all(i.original_alarm_recovered and i.score.final_window_inside for i in rep.final_state.impacts)
    assert rep.final.recommended_action_id == "SIM_REDUCE_NOW"
    cmd = rep.final.setpoint_command
    assert cmd.decision == "propose_setpoint" and cmd.setpoint_changes[0].setpoint_id == "production_rate"
    assert rep.final.tools_virtual is False
    assert all(t.evaluation is not None for t in rep.tool_requests)
    assert "RECOMMEND:SIM_REDUCE_NOW" in client.requests[-1].questions["next_action"].criteria


def test_sop_stage_requests_retrieval_after_low_applicability():
    runner = FakeRunner()
    catalog, policy, event, kit = setup(runner)
    key = "PROVIDE_SOP:SIM_REDUCE_NOW"
    client = ScriptedZoroClient([{"answers": {
        "next_action": {"type": "choice", "choice": key, "confidence": 1, "probabilities": {key: 1}},
        f"applicable:{key}": {"type": "noul", "noul": 0.1},
        f"evidence:{key}": {"type": "score", "score": 2},
    }}])
    report = ZoroAgent(catalog, policy, client, ToolExecutor(kit.tools(), virtual=False)).run(event, sop_rag(SOP, catalog))
    assert report.final.sop_route == "RETRIEVE_AGAIN"
    assert report.final.recommended_action_id is None
    assert runner.calls == []


def test_simulation_errors_map_to_tool_errors():
    for recoverable, reason in ((False, TerminationReason.UNRECOVERABLE_TOOL_ERROR),):
        catalog, policy, event, kit = setup(FakeRunner(SimulationError("branch failed", recoverable)))
        agent = ZoroAgent(catalog, policy, FakeZoroClient(["SIM_HOLD"]), ToolExecutor(kit.tools(), virtual=False))
        rep = agent.run(event, sop_rag(SOP, catalog))
        assert rep.final.termination_reason == reason and rep.final_state.impacts == []
    catalog, policy, event, kit = setup(FakeRunner(SimulationError("busy", True)))
    agent = ZoroAgent(catalog, policy, FakeZoroClient(["SIM_HOLD", "REQUEST_HUMAN_REVIEW"]),
                      ToolExecutor(kit.tools(), virtual=False))
    rep = agent.run(event, sop_rag(SOP, catalog))
    assert rep.tool_results[0].status == "failed" and rep.tool_results[0].error.recoverable
    assert rep.final.outcome == "human_review"


def test_branch_outcome_cannot_carry_ground_truth():
    with pytest.raises(ValidationError):
        BranchOutcome(times_s=[0, 1], samples=[{}, {}], tripped=False, simulator_version="x",
                      disturbance_profile_id="y", active_disturbances=["IDV(4)"])
