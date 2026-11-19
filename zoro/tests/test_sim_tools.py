"""SimulationToolkit(실제 시뮬레이터 어댑터)와 ProtoPilot case 구성 — 가짜 분기 실행기로 검사."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from zoro_agent.agent import ZoroAgent
from zoro_agent.cases import SimulatorCase
from zoro_agent.inputs import Alarm, EventInput, Observation
from zoro_agent.cases import ActiveSetpoint
from zoro_agent.outputs import TerminationReason
from zoro_agent.protopilot_cases import build_alarm_catalog, build_benchmark, build_policy, sop_rag
from zoro_agent.sim_tools import BranchOutcome, SimulationError, SimulationToolkit
from zoro_agent.tools import ToolExecutor
from zoro_agent.zoro import FakeZoroClient

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
    client = FakeZoroClient(["SIM_HOLD", "SIM_REDUCE_NOW", "RECOMMEND:SIM_REDUCE_NOW", "RECOMMEND:SIM_HOLD",
                             "REQUEST_HUMAN_REVIEW"])
    agent = ZoroAgent(catalog, policy, client, ToolExecutor(kit.tools(), virtual=False))
    rep = agent.run(event, sop_rag(SOP, catalog))
    # 시뮬레이터에 넘어간 것: 등록 case 와 snapshot
    assert [(s, c.case_id) for s, c, _ in runner.calls] == [("RUN1:7", "CASE-HOLD"), ("RUN1:7", "CASE-REDUCE_NOW")]
    assert runner.calls[1][1].setpoint_changes[0].target_value == pytest.approx(22.89 * 0.95)
    # 결과가 다음 판단에 반영됨: 유지는 경보 미회복, 감량은 회복 → 감량 권고
    imps = {i.run_id.split("-", 2)[2]: i for i in rep.final_state.impacts}
    assert imps["SIM_HOLD"].original_alarm_recovered is False and imps["SIM_HOLD"].exceedances
    assert imps["SIM_REDUCE_NOW"].original_alarm_recovered is True
    assert imps["SIM_REDUCE_NOW"].score.final_window_inside and imps["SIM_HOLD"].score.failed_gates
    assert rep.final.recommended_action_id == "SIM_REDUCE_NOW"
    cmd = rep.final.setpoint_command
    assert cmd.decision == "propose_setpoint" and cmd.setpoint_changes[0].setpoint_id == "production_rate"
    assert rep.final.tools_virtual is False
    assert all(t.evaluation is not None for t in rep.tool_requests)
    assert "RECOMMEND:SIM_REDUCE_NOW" in client.requests[-1].questions["next_action"].criteria


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
