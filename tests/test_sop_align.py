import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import sop_align
from server.alarm_response import analyze_alarm
from server.plant import Plant
from server.scenario import DEFAULT_FLAGS, LIVE_FORMAT, STEP_HOURS, live_scenario


def run_plant(root, idv=None, end_h=2.5, onset_h=1.0, seed=777):
    timeline = [] if idv is None else [
        {"step": round(onset_h / STEP_HOURS), "op": "disturbance", "id": f"IDV({idv})", "value": 1}]
    plant = Plant(root)
    plant.reset(running=True, scenario=live_scenario({
        "format": LIVE_FORMAT, "step_hours": STEP_HOURS, "seed": seed, "controller": DEFAULT_FLAGS,
        "initial_speed": 960, "through_step": round(end_h / STEP_HOURS), "timeline": timeline}))
    while plant.running:
        plant.tick()
    return plant


class SopAlignTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_index_tags_are_live_observed_keys(self):
        plant = run_plant(self.root, end_h=0.05)
        observed = plant.history[-1][1]
        tags = {c["tag"] for e in sop_align.INDEX["idv"] for r in e["rules"] for c in r["conditions"]}
        self.assertEqual(sorted(t for t in tags if t not in observed), [])

    def test_fault_free_history_selects_no_idv_sop(self):
        plant = run_plant(self.root, end_h=3.0)
        result = sop_align.align({"kind": "probe"}, list(plant.history))
        self.assertIsNone(result["idv_selected"])

    def test_a_feed_loss_alarm_aligns_to_idv06_and_reaches_zoro_state(self):
        plant = run_plant(self.root, idv=6, end_h=4.0)  # first XMEAS(15) low alarm near 3.6 h
        checkpoint = next(cp for cp in plant.alarm_checkpoints.values() if cp.alarm["kind"] != "trip")
        self.assertTrue(checkpoint.history)
        with patch("server.alarm_response.HORIZON_HOURS", 10 * STEP_HOURS):
            result = analyze_alarm(checkpoint, self.root / "trace.jsonl")
        self.assertEqual(result["alignment"]["idv_selected"], "IDV-06")
        self.assertEqual(result["sop"]["id"], "TEP-IDV-06")
        self.assertIn("XMEAS(1)", [r["tag"] for r in result["readings"]])

    def test_sensor_sop_matches_severity_band(self):
        high = sop_align.match_sensor_sop({"kind": "warning", "tag": "XMEAS(7)", "side": "high", "value": 2900})
        severe = sop_align.match_sensor_sop({"kind": "warning", "tag": "XMEAS(7)", "side": "high", "value": 2980})
        self.assertEqual(high["severity"], "H")
        self.assertEqual(severe["severity"], "HH")
        gap = sop_align.align({"kind": "warning", "tag": "XMEAS(9)", "side": "high", "value": 151}, [])
        self.assertIsNone(gap["sensor_sop"])
        self.assertIn("XMEAS(9)", gap["sensor_gap"])


if __name__ == "__main__":
    unittest.main()
