import json
import tempfile
import unittest
from pathlib import Path

from server.minute_scenario import MinuteScenario, ScenarioEvent
from server.plant import Plant
from server.scenario import STEP_HOURS, live_scenario

SCENARIO_DIR = Path(__file__).parents[1] / "docs/scenarios"


def steps(minutes):
    return round(minutes / 60 / STEP_HOURS)


class MinuteScenarioConversionTest(unittest.TestCase):
    def test_minutes_become_control_steps_and_durations_are_undone(self):
        live = MinuteScenario.from_document({
            "format": "protopilot-scenario-1", "duration_min": 10, "title": "kept out of live-1",
            "alarm_limits": {"XMEAS(7)": [None, 2725]},
            "events": [{"at_min": 1, "op": "offset", "id": "XMEAS(9)", "value": 32, "duration_min": 2, "note": "spike"}],
        }).to_live()
        self.assertEqual(live["through_step"], steps(10))
        self.assertEqual(live["timeline"], [
            {"step": 0, "op": "limit", "id": "XMEAS(7)", "low": None, "value": 2725},
            {"step": steps(1), "op": "offset", "id": "XMEAS(9)", "value": 32},
            {"step": steps(3), "op": "offset", "id": "XMEAS(9)", "value": 0},
        ])
        self.assertEqual(live_scenario(live)["through_step"], steps(10))

    def test_operations_at_the_same_minute_keep_their_written_order(self):
        live = MinuteScenario.from_document({
            "format": "protopilot-scenario-1", "duration_min": 5,
            "events": [{"at_min": 1, "op": "disturbance", "id": f"IDV({i})", "value": 1} for i in (12, 8, 11)],
        }).to_live()
        self.assertEqual([entry["id"] for entry in live["timeline"]], ["IDV(12)", "IDV(8)", "IDV(11)"])

    def test_undo_restores_each_operation_kind(self):
        def undo(change):
            return ScenarioEvent(0, change, 1).undo()
        self.assertEqual(undo({"op": "disturbance", "id": "IDV(1)", "value": 1})["value"], 0)
        self.assertIsNone(undo({"op": "setpoint", "id": "production_rate", "value": 21})["value"])
        self.assertEqual(undo({"op": "limit", "id": "XMEAS(7)", "low": None, "value": 2700})["low"], None)
        with self.assertRaises(ValueError):
            undo({"op": "speed", "value": 60})

    def test_malformed_documents_are_rejected(self):
        base = {"format": "protopilot-scenario-1", "duration_min": 10, "events": []}
        for broken in ({**base, "duration_min": -1}, {**base, "events": {}}, {**base, "alarm_limits": {"XMEAS(7)": 2725}},
                       {**base, "events": [{"op": "speed", "value": 60}]},
                       {**base, "events": [{"at_min": 1, "op": "speed", "value": 60, "duration_min": 1}]}):
            with self.assertRaises(ValueError):
                MinuteScenario.from_document(broken)
        self.assertFalse(MinuteScenario.accepts({"format": "protopilot-live-1"}))


class BundledScenarioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plant = Plant(Path(self.temp.name))

    def load(self, name):
        return json.loads((SCENARIO_DIR / name).read_text(encoding="utf-8"))

    def test_every_demo_scenario_loads_as_a_paused_replay(self):
        names = sorted(path.name for path in SCENARIO_DIR.glob("*.json"))
        self.assertEqual(len(names), 5)
        for name in names:
            self.plant.command({"op": "replay_scenario", "scenario": self.load(name)})
            self.assertFalse(self.plant.running, name)
            self.assertEqual(self.plant.step, 0, name)

    def test_disturbances_reach_the_kernel_at_the_written_minute(self):
        self.plant.command({"op": "replay_scenario", "scenario": self.load("s4-composition-and-two-coolers.json")})
        self.assertEqual(self.plant.limits["XMEAS(7)"], (None, 2725))
        self.plant.command({"op": "run", "enabled": True})
        self.plant.speed = 15  # 5 steps per tick, so the run stops exactly on the onset step
        onset = steps(60)
        while self.plant.step < onset - 5:
            self.plant.tick()
        self.assertEqual(self.plant.active_disturbances(), [])
        while self.plant.step < onset:
            self.plant.tick()
        self.assertEqual(self.plant.active_disturbances(), ["IDV(8)", "IDV(11)", "IDV(12)"])

    def test_a_rejected_upload_leaves_the_active_run_untouched(self):
        run = self.plant.recording.id
        late = {"format": "protopilot-scenario-1", "duration_min": 10,
                "events": [{"at_min": 20, "op": "disturbance", "id": "IDV(1)", "value": 1}]}
        with self.assertRaises(ValueError):
            self.plant.command({"op": "replay_scenario", "scenario": late})
        self.assertEqual(self.plant.recording.id, run)
        self.assertTrue(self.plant.running)

    def test_step_cannot_override_minutes_or_bypass_the_replay_end(self):
        run = self.plant.recording.id
        for minute in (1, 20):
            with self.subTest(at_min=minute):
                scenario = {
                    "format": "protopilot-scenario-1", "duration_min": 10,
                    "events": [{"at_min": minute, "step": 0, "op": "offset", "id": "XMEAS(9)", "value": 1}],
                }
                with self.assertRaisesRegex(ValueError, "at_min"):
                    self.plant.command({"op": "replay_scenario", "scenario": scenario})
                self.assertEqual(self.plant.recording.id, run)
                self.assertNotIn("XMEAS(9)", self.plant.offsets)


if __name__ == "__main__":
    unittest.main()
