import json
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from unittest.mock import patch

import numpy as np

from server.alarm_response import HORIZON_HOURS, run_branch
from server.plant import Plant
from server.recording import encode
from server.scenario import SPEEDS
from server.zoro_bridge import CheckpointRunner, event_from_checkpoint, run_zoro_agent, snapshot_id

from zoro_agent.cases import SimulatorCase


class ZoroBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plant = Plant(self.root)
        self.plant.command({"op": "cooling_demo"})
        while self.plant.running:
            self.plant.tick()
        self.alarm = next(a for a in self.plant.active.values() if a["tag"] == "XMEAS(9)")
        self.checkpoint = self.plant.alarm_checkpoints[self.alarm["id"]]
        self.run_id = self.plant.recording.id

    def test_hold_branch_matches_the_live_continuation(self):
        snap = snapshot_id(self.run_id, self.alarm["id"])
        hold = SimulatorCase(case_id="CASE-HOLD", policy="hold", setpoint_changes=[], values_status="example")
        outcome = CheckpointRunner({snap: self.checkpoint}).run(snap, hold, 100 * 0.0005 * 3600)
        plant = self.plant
        plant.command({"op": "run", "enabled": True})
        plant.speed = 15
        with patch.dict(SPEEDS, {15: 1}):
            for _ in range(100):
                plant.tick()
        live = {f"XMEAS({i + 1})": float(v) for i, v in enumerate(plant.measurements)}
        self.assertEqual(outcome.samples[-1], live)
        self.assertEqual(outcome.times_s, [0.0, 180.0])

    def test_event_never_carries_active_disturbances(self):
        event = event_from_checkpoint(self.run_id, self.alarm["id"], self.checkpoint)
        dumped = event.model_dump_json()
        self.assertNotIn("IDV", dumped)
        self.assertEqual(event.alarms[-1].threshold, self.alarm["limit"])
        self.assertTrue(any(s.setpoint_id == "production_rate" for s in event.active_setpoints))

    def test_event_alarms_come_from_the_alarm_monitor(self):
        """Agent alarms are the AlarmMonitor's observed channel (feat/setpoint-decision), not the console list."""
        event = event_from_checkpoint(self.run_id, self.alarm["id"], self.checkpoint)
        self.assertEqual({a.alarm_id for a in event.alarms}, set(self.checkpoint.monitor.active["observed"]))
        own = next(a for a in event.alarms if a.alarm_id == f"LIMIT {self.alarm['tag']} {self.alarm['side']}")
        self.assertEqual((own.sensor_id, own.condition, own.threshold),
                         (self.alarm["tag"], self.alarm["side"], self.alarm["limit"]))

    def test_branch_cost_and_alarm_minutes_match_the_alarm_response_branch(self):
        """Same checkpoint, same horizon: the agent's hold branch is metered exactly like alarm_response.run_branch."""
        snap = snapshot_id(self.run_id, self.alarm["id"])
        hold = SimulatorCase(case_id="CASE-HOLD", policy="hold", setpoint_changes=[], values_status="example")
        outcome = CheckpointRunner({snap: self.checkpoint}).run(snap, hold, HORIZON_HOURS * 3600)
        reference = run_branch(self.checkpoint, "hold")
        self.assertAlmostEqual(outcome.operating_cost_usd, reference["operating_cost"], places=9)
        self.assertAlmostEqual(outcome.produced_m3, reference["produced_m3"], places=9)
        self.assertAlmostEqual(outcome.production_loss_m3, reference["production_loss_m3"], places=9)
        self.assertEqual(outcome.alarm_minutes, reference["alarm_minutes"])
        self.assertEqual(outcome.observed_alarm_minutes, reference["observed_alarm_minutes"])

    def test_agent_requests_branches_and_decides_from_their_results(self):
        state_before = self.plant.sim.snapshot().model_bytes
        log = self.root / "agent-trace.jsonl"
        result = run_zoro_agent(self.plant, RLock(), self.run_id, self.alarm["id"], log, horizon_s=1.05 * 3600)
        self.assertEqual(self.plant.sim.snapshot().model_bytes, state_before)
        final = result["final"]
        self.assertIn(final["termination_reason"], {"recommendation_ready", "no_valid_options", "limit_reached"})
        sims = [t for t in result["tool_requests"] if t["tool_name"] == "run_simulation"]
        self.assertEqual(sims[0]["case"]["case_id"], "CASE-HOLD")
        self.assertTrue(all(t["snapshot_id"] == snapshot_id(self.run_id, self.alarm["id"]) for t in sims))
        runs = result["final_state"]["runs"]
        self.assertEqual(len(runs), len(sims))
        self.assertTrue(all(r["status"] == "succeeded" for r in runs))
        self.assertTrue(all(i["score"] is not None for i in result["final_state"]["impacts"]))
        # Scores use the TEP internal operating cost integrated per control step, not the Table 9 estimate.
        for impact in result["final_state"]["impacts"]:
            score = impact["score"]
            self.assertEqual(score["cost_basis"], "tep_operating_cost_internal")
            self.assertAlmostEqual(score["usd_per_m3"], score["cost_usd"]["total"] / score["product_m3"])
            self.assertIn("alarm_minutes", impact["metrics"])
        for exchange in result["exchanges"]:
            self.assertNotIn("IDV", encode(exchange["request"]))
        self.assertEqual(json.loads(log.read_text())["flow"], "zoro_agent")


if __name__ == "__main__":
    unittest.main()
