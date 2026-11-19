import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from server.plant import Plant
from server.recording import encode
from server.scenario import SPEEDS, STEP_HOURS
from server.setpoint_decision import analyze_setpoints, capture_point, retrieve_rules, run_setpoint_branch


def run_until(plant, hours):
    while plant.sim.time < hours:
        plant.tick()


class SetpointDecisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plant = Plant(self.root)
        self.plant.command({"op": "speed", "value": 960})
        self.plant.command({"op": "disturbance", "id": "IDV(6)", "value": 1})
        run_until(self.plant, 2.5)

    def test_zoro_gate_and_moves_drive_multi_setpoint_branches(self):
        point = capture_point(self.plant)
        self.assertIn("FEEDA-1", [rule["id"] for rule in retrieve_rules(point)])

        def reply(url, payload, *, timeout):
            questions = payload["questions"]
            if all(q["type"] == "noul" for q in questions.values()):
                return {"answers": {name: {"type": "noul", "noul": 0.93 if name == "production_rate" else 0.08}
                                    for name in questions}}
            return {"answers": {name: {"type": "choice", "choice": "decrease_small", "confidence": 0.61,
                                       "probabilities": {k: (0.61 if k == "decrease_small" else 0.1) for k in q["criteria"]}}
                                for name, q in questions.items()}}

        log = self.root / "agent-trace.jsonl"
        with patch.dict("os.environ", {"ZORO_BASE_URL": "http://127.0.0.1:30001"}):
            with patch("server.assistant._request", side_effect=reply) as request:
                with patch("server.setpoint_decision.BRANCH_HOURS", 20 * STEP_HOURS):
                    result = analyze_setpoints(point, log)
        decision = result["decision"]
        self.assertFalse(decision["fallback"])
        self.assertEqual(decision["changes"][0]["setpoint"], "production_rate")
        self.assertAlmostEqual(decision["changes"][0]["move"], -0.05)
        self.assertEqual([b["id"] for b in result["branches"]], ["hold", "rule", "zoro"])
        self.assertTrue(all("error" not in b for b in result["branches"]))
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertIn("/v1/systemone", call.args[0])
            self.assertNotIn("IDV", encode(call.args[1]))
            self.assertNotIn("센서 시험", encode(call.args[1]))
        state = request.call_args_list[0].args[1]["state"]
        self.assertIn("FEEDA-1", [rule["id"] for rule in state["operating_procedures"]])
        self.assertEqual(len(state["plant"]["sensors"]), 41)
        trace = json.loads(log.read_text())
        self.assertEqual(trace["flow"], "live_setpoint_decision")
        self.assertEqual(trace["simulation_context"]["evaluation_faults"], ["IDV(6)"])
        self.assertTrue(result["logged"])

    def test_without_zoro_the_local_rule_decides_and_is_logged(self):
        point = capture_point(self.plant)
        log = self.root / "agent-trace.jsonl"
        with patch.dict("os.environ", {"ZORO_BASE_URL": ""}):
            with patch("server.setpoint_decision.BRANCH_HOURS", 20 * STEP_HOURS):
                result = analyze_setpoints(point, log)
        decision = result["decision"]
        self.assertTrue(decision["fallback"])
        self.assertEqual(decision["reason"], "not_configured")
        self.assertEqual([b["id"] for b in result["branches"]], ["hold", "rule"])
        self.assertTrue(json.loads(log.read_text())["decision"]["fallback"])

    def test_branch_cost_loss_and_alarm_minutes_match_the_live_plant(self):
        plant = self.plant
        point = capture_point(plant)
        with patch("server.setpoint_decision.BRANCH_HOURS", 10 * STEP_HOURS):
            hold = run_setpoint_branch(point, "hold", [])
        cost_before = plant.metrics.finalize()["operating_cost_total"]
        plant.speed = 15
        with patch.dict(SPEEDS, {15: 1}):
            for _ in range(10):
                plant.tick()
        live_cost = plant.metrics.finalize()["operating_cost_total"] - cost_before
        self.assertAlmostEqual(hold["operating_cost"], live_cost, places=9)
        reference = point.controller.setpoints.production_rate
        self.assertAlmostEqual(hold["production_loss_m3"], reference * 10 * STEP_HOURS - hold["produced_m3"], places=12)
        self.assertGreater(hold["cost_per_m3"], 0)
        self.assertIn("alarm_minutes", hold)
        tight = {**point.limits, "XMEAS(9)": (None, 100.0)}  # every step above a 100 degC limit
        monitor = point.monitor.copy()
        monitor.set_limits(tight)
        with patch("server.setpoint_decision.BRANCH_HOURS", 10 * STEP_HOURS):
            alarmed = run_setpoint_branch(replace(point, limits=tight, monitor=monitor), "hold", [])
        self.assertAlmostEqual(alarmed["alarm_minutes"], 10 * STEP_HOURS * 60)

    def test_history_keeps_two_hours_of_three_minute_samples(self):
        history = self.plant.history
        self.assertLessEqual(history[-1].time_hours - history[0].time_hours, 2.0 + 1e-6)
        gaps = [b.time_hours - a.time_hours for a, b in zip(history, list(history)[1:])]
        self.assertTrue(all(gap >= 0.05 - 1e-6 for gap in gaps))
        self.assertEqual(len(history[-1].values), 41)


if __name__ == "__main__":
    unittest.main()


class RolloutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plant = Plant(Path(self.temp.name))
        self.plant.command({"op": "speed", "value": 960})
        run_until(self.plant, 0.5)

    def test_hold_baseline_replays_the_environment_bitwise(self):
        from server.rollout import Rollout

        plant = self.plant
        plant.rollout = Rollout(plant)
        self.addCleanup(plant.rollout.close)
        run_until(plant, 0.8)
        plant.command({"op": "disturbance", "id": "IDV(6)", "value": 1})
        plant.command({"op": "mv", "id": "XMV(9)", "value": 45.0})
        plant.command({"op": "offset", "id": "XMEAS(7)", "value": 30.0})
        run_until(plant, 1.6)
        report = plant.rollout.compare(*plant.rollout.snapshot(plant))
        hold = plant.rollout.shadows["hold"]
        self.assertEqual(hold.step, plant.step)
        self.assertEqual(hold.measurements.tolist(), plant.measurements.tolist())  # no live setpoint change
        self.assertEqual(report["baselines"]["hold"], report["live"])
        self.assertGreater(report["live"]["operating_cost"], 0)
        self.assertAlmostEqual(report["live"]["elapsed_hours"], plant.step * STEP_HOURS - report["anchor"]["time_hours"],
                               places=6)

    def test_live_setpoint_change_and_rule_baseline_diverge_from_hold(self):
        from server.rollout import Rollout

        plant = self.plant
        plant.command({"op": "disturbance", "id": "IDV(6)", "value": 1})
        run_until(plant, 1.0)
        plant.rollout = Rollout(plant)
        self.addCleanup(plant.rollout.close)
        plant.command({"op": "setpoint", "id": "production_rate", "value": 20.0})
        run_until(plant, 3.5)
        report = plant.rollout.compare(*plant.rollout.snapshot(plant))
        live, hold, rule = report["live"], report["baselines"]["hold"], report["baselines"]["rule"]
        self.assertEqual(live["setpoint_changes"], 1)
        self.assertEqual(hold["setpoint_changes"], 0)
        self.assertGreater(rule["setpoint_changes"], 0)  # A-feed valve saturates -> the rule cuts production
        self.assertGreater(live["production_loss_m3"], hold["production_loss_m3"])
        self.assertEqual(live["elapsed_hours"], hold["elapsed_hours"])


class AlarmInfoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plant = Plant(Path(self.temp.name))
        self.plant.command({"op": "speed", "value": 960})

    def test_console_alarm_is_described_by_sensor_problem_and_duration(self):
        from server.setpoint_decision import alarm_info

        plant = self.plant
        run_until(plant, 0.5)
        plant.command({"op": "pressure_test"})
        run_until(plant, 0.9)
        info = alarm_info(capture_point(plant))
        pressure = next(a for a in info if a["tag"] == "XMEAS(7)")
        self.assertEqual(pressure["sensor"], "reactor_pressure")
        self.assertEqual(pressure["problem"], "above upper limit")
        self.assertEqual(pressure["related_setpoint"], "reactor_pressure")
        self.assertGreater(pressure["exceeded_by"], 0)
        self.assertGreater(pressure["active_for_minutes"], 0)
        self.assertIn("trend_per_h", pressure)
        self.assertNotIn("source", pressure)  # process vs. sensor test is not told to the agent
        self.assertNotIn("센서 시험", encode(info))

    def test_condition_rules_are_pluggable_and_respect_for_minutes(self):
        from server.alarm_rules import CONDITIONS, AlarmMonitor, condition

        rules = [{"id": "A-LOST", "tag": "XMEAS(1)", "condition": {"type": "threshold", "below": 0.05}, "for_minutes": 30},
                 {"id": "A-LOST-LONG", "tag": "XMEAS(1)", "condition": {"type": "threshold", "below": 0.05}, "for_minutes": 600},
                 {"id": "OFF", "enabled": False, "tag": "XMEAS(1)", "condition": {"type": "threshold", "below": 1e9}}]
        plant = self.plant
        plant.alarm_monitor = AlarmMonitor(plant.limits, rules)
        plant.command({"op": "disturbance", "id": "IDV(6)", "value": 1})
        run_until(plant, 2.0)
        hits = plant.alarm_monitor.describe_active()
        self.assertEqual([h["origin"] for h in hits], ["condition rule A-LOST"])
        self.assertGreaterEqual(hits[0]["active_for_minutes"], 30)
        self.assertEqual(hits[0]["sensor"], "feed_A_flow")
        self.assertGreater(plant.alarm_monitor.summary()["alarm_minutes_by_rule"]["A-LOST"], 0)

        @condition("always")
        def _always(rule, cond, view):
            return {"problem": "test condition", "value": view.latest(rule["tag"])}
        self.addCleanup(CONDITIONS.pop, "always")
        monitor = AlarmMonitor({}, [{"id": "X", "tag": "XMEAS(9)", "condition": {"type": "always"}}])
        monitor.update(0.0, plant.measurements, {}, plant.controller.setpoints, STEP_HOURS)
        self.assertEqual(monitor.describe_active()[0]["problem"], "test condition")

    def test_agent_alarm_and_branch_minutes_come_from_one_engine(self):
        plant = self.plant
        run_until(plant, 0.5)
        plant.command({"op": "pressure_test"})
        run_until(plant, 0.9)
        point = capture_point(plant)
        live_hit = next(a for a in point.monitor.describe_active("observed") if a["tag"] == "XMEAS(7)")
        self.assertEqual(point.monitor.describe_active("true"), [])  # the plant itself is fine
        with patch("server.setpoint_decision.BRANCH_HOURS", 10 * STEP_HOURS):
            hold = run_setpoint_branch(point, "hold", [])
        self.assertEqual(hold["alarm_minutes"], 0.0)  # scored on true values
        self.assertAlmostEqual(hold["observed_alarm_minutes"], 10 * STEP_HOURS * 60)  # what the operator saw
        self.assertIn("LIMIT XMEAS(7) high", hold["observed_alarm_minutes_by_rule"])
        self.assertEqual(live_hit["problem"], "above upper limit")


class AgentConfigTest(unittest.TestCase):
    def test_default_file_and_override_are_validated(self):
        from server.agent_config import DEFAULT_PATH, load

        default = load()
        self.assertEqual(default["branch_hours"], 4.0)
        self.assertEqual(default["baselines"], ("hold", "rule"))
        raw = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            custom = Path(directory) / "agent.json"
            raw["setpoint_decision"]["branch_hours"] = 8
            custom.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(load(custom)["branch_hours"], 8.0)
            self.assertEqual(load(custom)["rulebook_path"], Path(directory) / "setpoint-sops.json")
            raw["setpoint_decision"]["need_threshold"] = 1.5
            custom.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "need_threshold"):
                load(custom)
