import json
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from unittest.mock import patch

import numpy as np

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
        for exchange in result["exchanges"]:
            self.assertNotIn("IDV", encode(exchange["request"]))
        self.assertEqual(json.loads(log.read_text())["flow"], "zoro_agent")


if __name__ == "__main__":
    unittest.main()
