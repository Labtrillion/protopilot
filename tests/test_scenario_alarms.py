"""The demo scenarios' `alarm_limits` must ring for each scenario's purpose and stay quiet in fault-free operation.

The limits were set just outside what the default seed shows in fault-free operation, so a change to the kernel,
the controller or the scenarios can silently stop them from ringing (or make them ring for nothing). Runs use the
scenario's own `initial_speed`, so alarms are judged at the same interval as on the console.
"""

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from server.plant import Plant

SCENARIO_DIR = Path(__file__).parents[1] / "docs/scenarios"
REACTOR_TAGS = {"XMEAS(7)", "XMEAS(9)"}
DOWNSTREAM_TAGS = {"XMEAS(12)", "XMEAS(15)"}


@dataclass(frozen=True)
class Alarm:
    minute: float
    tag: str
    raised: bool  # False for the matching "복구" (clear) event


class ScenarioAlarmRun:
    """One replay of a scenario document on a fresh Plant, with the console alarms it raised and cleared."""

    def __init__(self, document):
        self.document = document
        self._alarms = None

    @classmethod
    def from_file(cls, name):
        return cls(json.loads((SCENARIO_DIR / name).read_text(encoding="utf-8")))

    def without(self, *disturbances):
        """The same scenario with the given disturbance events removed."""
        events = [event for event in self.document["events"]
                  if not (event["op"] == "disturbance" and event["id"] in disturbances)]
        return ScenarioAlarmRun({**self.document, "events": events})

    def fault_free(self):
        """Same limits, duration and speed with no operations: the scenario's limits on a normal run."""
        return ScenarioAlarmRun({**self.document, "events": []})

    def alarms(self):
        if self._alarms is None:
            self._alarms = self._replay()
        return self._alarms

    def raised(self, after=0.0, before=float("inf")):
        return [alarm for alarm in self.alarms() if alarm.raised and after <= alarm.minute < before]

    def _replay(self):
        with tempfile.TemporaryDirectory() as root:
            plant = Plant(Path(root))
            plant.command({"op": "replay_scenario", "scenario": self.document})
            plant.command({"op": "run", "enabled": True})
            seen, alarms = set(), []
            while plant.running:
                plant.tick()
                # Plant.events is newest first and bounded; read each tick so nothing scrolls off unseen.
                for event in reversed(plant.events):
                    if event["id"] in seen or event["kind"] not in ("warning", "clear"):
                        continue
                    seen.add(event["id"])
                    alarms.append(Alarm(round(event["time_hours"] * 60, 1), event["tag"], event["kind"] == "warning"))
            return alarms


class ScenarioAlarmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {path.stem: ScenarioAlarmRun.from_file(path.name) for path in sorted(SCENARIO_DIR.glob("*.json"))}

    def test_limits_stay_quiet_in_fault_free_operation(self):
        self.assertEqual(len(self.runs), 5)
        for name, run in self.runs.items():
            with self.subTest(name):
                self.assertEqual(run.fault_free().raised(), [])

    def test_s1_alarm_comes_from_the_added_valve_stiction(self):
        run = self.runs["s1-economic-then-stiction"]
        self.assertEqual(run.raised(before=180), [])
        self.assertEqual({alarm.tag for alarm in run.raised(after=180)}, {"XMEAS(9)"})
        self.assertEqual(run.without("IDV(14)").raised(), [])

    def test_s2_sensor_spike_rings_and_clears(self):
        alarms = self.runs["s2-false-high-temp"].alarms()
        self.assertEqual(alarms, [Alarm(120.0, "XMEAS(9)", True), Alarm(122.0, "XMEAS(9)", False)])

    def test_s3_alarms_pause_in_the_observed_recovery_window(self):
        run = self.runs["s3-restore-after-cooling"]
        self.assertTrue(run.raised(before=210))
        self.assertEqual(run.raised(after=210, before=375), [])
        self.assertTrue(run.raised(after=375))

    def test_s4_alarms_reach_the_reactor_and_downstream_units(self):
        tags = {alarm.tag for alarm in self.runs["s4-composition-and-two-coolers"].raised(after=60)}
        self.assertTrue(tags & REACTOR_TAGS)
        self.assertTrue(tags & DOWNSTREAM_TAGS)

    def test_s5_alarms_follow_the_leftover_production_target(self):
        alarms = self.runs["s5-shift-plan-mismatch"].raised()
        self.assertTrue(alarms)
        self.assertGreater(alarms[0].minute, 60)  # after the shift change, never before the raised target


if __name__ == "__main__":
    unittest.main()
