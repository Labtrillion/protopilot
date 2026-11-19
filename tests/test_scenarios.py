import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from tep_studio.agent.tools import TepToolset

from server.alarm_response import analyze_alarm, run_branch
from server.assistant import respond
from server.experiments import create_experiments
from server.plant import Plant
from server.recording import ArchivedRecording, encode
from server.scenario import SPEEDS, STEP_HOURS


class LiveScenarioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plant = Plant(self.root)

    def test_replay_restores_native_outputs_at_the_same_step(self):
        plant = self.plant
        self.assertEqual(len(plant.latest["values"]), 264)
        self.assertEqual(len(plant.latest["setpoints"]), 9)
        self.assertEqual(len(plant.latest["state"]), 50)
        self.assertAlmostEqual(plant.latest["derived"]["YA"], 54.955, places=3)
        plant.command({"op": "setpoint", "id": "ya", "value": 56})
        plant.tick()
        plant.command({"op": "setpoint", "id": "yac", "value": 60})
        plant.command({"op": "speed", "value": 15})
        plant.command({"op": "disturbance", "id": "IDV(4)", "value": 1})
        plant.tick()
        plant.command({"op": "pressure_test"})
        plant.tick()
        expected = plant.latest
        original_adjustments = [
            (e["tag"], e["time_hours"], e["previous_value"], e["value"])
            for e in plant.events if e.get("tag") in expected["setpoints"]
        ]
        original = ArchivedRecording.open(self.root, plant.recording.id)
        scenario = json.loads(encode(original.scenario()))
        self.assertEqual(original.series("XMEAS(7)")["points"][-1][1], expected["values"]["XMEAS(7)"])
        plant.command({"op": "replay_scenario", "scenario": scenario})
        self.assertFalse(plant.running)
        self.assertEqual(plant.step, 0)
        plant.command({"op": "run", "enabled": True})
        while plant.running:
            plant.tick()
        for key in ("step", "time_hours", "values", "native_values", "actuators", "control",
                    "hours_since_onset", "setpoints", "derived", "metrics", "shutdown"):
            self.assertEqual(expected[key], plant.latest[key], key)
        replayed_adjustments = [
            (e["tag"], e["time_hours"], e["previous_value"], e["value"])
            for e in plant.events if e.get("tag") in expected["setpoints"]
        ]
        self.assertEqual(len(original_adjustments), 2)
        self.assertEqual(original_adjustments, replayed_adjustments)
        self.assertFalse(any(plant.protection.values()))
        self.assertTrue(any(alarm["source"] == "센서 시험" for alarm in plant.active.values()))

    def test_pressure_adjustment_records_native_separator_readings_at_its_step(self):
        plant = self.plant
        plant.tick()
        plant.command({"op": "offset", "id": "XMEAS(13)", "value": 50})
        baseline = {
            "XMEAS(13)": plant.latest["native_values"]["XMEAS(13)"],
            "XMEAS(12)": plant.latest["values"]["XMEAS(12)"],
        }
        change_time = plant.latest["time_hours"]
        plant.command({"op": "setpoint", "id": "reactor_pressure", "value": 2645})
        change = next(event for event in plant.events if event.get("tag") == "reactor_pressure")
        self.assertEqual(change["time_hours"], change_time)
        self.assertEqual(change["separator_readings"], baseline)

        plant.tick()
        scenario = plant.scenario()
        plant.command({"op": "replay_scenario", "scenario": scenario})
        plant.command({"op": "run", "enabled": True})
        while plant.running:
            plant.tick()
        replayed = next(event for event in plant.events if event.get("tag") == "reactor_pressure")
        self.assertEqual(replayed["time_hours"], change_time)
        self.assertEqual(replayed["separator_readings"], baseline)

    def test_invalid_upload_leaves_the_active_run_untouched(self):
        plant = self.plant
        run = plant.recording.id
        invalid = {**plant.scenario(), "timeline": [{"step": 0, "op": "setpoint", "id": "ya", "value": 101}]}
        with self.assertRaises(ValueError):
            plant.command({"op": "replay_scenario", "scenario": invalid})
        self.assertEqual(plant.recording.id, run)
        self.assertTrue(plant.running)

    def test_control_locks_in_both_protection_actions_on_native_pv(self):
        plant = self.plant
        self.assertTrue(plant.latest["controller_flags"]["overrides"])
        plant.measurements[6] = 2930
        plant.measurements[7] = 95
        plant.command({"op": "mv", "id": "XMV(5)", "value": 100})
        with patch.object(plant.sim, "advance", wraps=plant.sim.advance) as advance:
            plant.tick()
            self.assertLess(advance.call_args_list[0].args[0][4], 100)
        self.assertTrue(any(e["tag"] == "XMEAS(7)" and e.get("active") for e in plant.events))
        self.assertTrue(any(e["tag"] == "XMEAS(8)" and e.get("active") for e in plant.events))

    def test_cooling_alarm_branches_from_the_live_checkpoint(self):
        plant = self.plant
        plant.command({"op": "cooling_demo"})
        while plant.running:
            plant.tick()
        alarm = next(alarm for alarm in plant.active.values() if alarm["tag"] == "XMEAS(9)")
        checkpoint = plant.alarm_checkpoints[alarm["id"]]
        self.assertEqual(checkpoint.step, plant.step)
        self.assertEqual(plant.active_disturbances(), ["IDV(11)", "IDV(14)"])

        state_before = plant.sim.snapshot().model_bytes
        with patch("server.alarm_response.HORIZON_HOURS", 10 * STEP_HOURS):
            baseline = run_branch(checkpoint, "hold")
        self.assertEqual(plant.sim.snapshot().model_bytes, state_before)

        plant.command({"op": "run", "enabled": True})
        plant.speed = 15
        observed = [plant.measurements]
        with patch.dict(SPEEDS, {15: 1}):
            for _ in range(10):
                plant.tick()
                observed.append(plant.measurements)
        measurements = np.asarray(observed)
        self.assertEqual(baseline["peak_temperature"], np.max(measurements[:, 8]))
        self.assertEqual(baseline["mean_production"], np.mean(measurements[1:, 16]))

        log = self.root / "agent-trace.jsonl"
        answers = {"answers": {
            "diagnosis": {"choice": "combined", "confidence": 0.91},
            "response": {"choice": "reduce_30", "confidence": 0.85},
        }}

        def respond(_url, payload, **_options):
            questions = payload["questions"]
            if "diagnosis" in questions:
                return answers
            self.assertTrue(all(question["type"] == "score" for question in questions.values()))
            return {"answers": {name: {"type": "score", "score": 0.75} for name in questions}}

        with patch.dict("os.environ", {"ZORO_BASE_URL": "http://127.0.0.1:30001"}):
            with patch("zoro.client._request", side_effect=respond) as request:
                with patch("server.alarm_response.HORIZON_HOURS", 1.01):
                    result = analyze_alarm(checkpoint, log)
        self.assertEqual(result["sop"]["id"], "TEP-COOL-01")
        self.assertEqual(result["decision"]["response"]["choice"], "reduce_30")
        self.assertEqual(result["start"]["step"], checkpoint.step)
        self.assertEqual([branch["action_applied"] for branch in result["branches"]], [False, True, True, True])
        sweep = result["sweep"]
        self.assertEqual(sweep["baseline"], result["branches"][0])
        self.assertEqual(sweep["delay_minutes"], (0, 15, 30, 45, 60))
        self.assertEqual(sweep["reduction_pct"], (1, 2.5, 5, 7.5, 10))
        self.assertEqual([(branch["delay_minutes"], branch["reduction_pct"]) for branch in sweep["branches"]],
                         [(delay, rate) for delay in sweep["delay_minutes"] for rate in sweep["reduction_pct"]])
        self.assertTrue(all(branch["action_applied"] for branch in sweep["branches"]))
        self.assertTrue(all("alarm_recovery_minutes" in branch for branch in sweep["branches"]))
        self.assertEqual(sweep["scores"], {"hold": 0.75, **{branch["id"]: 0.75 for branch in sweep["branches"]}})
        self.assertIsNone(sweep["score_reason"])
        self.assertEqual(request.call_count, 4)
        for call in request.call_args_list:
            self.assertIn("/v1/systemone", call.args[0])
            self.assertNotIn("IDV", encode(call.args[1]))
        trace = json.loads(log.read_text())
        self.assertEqual(trace["simulation_context"]["evaluation_faults"], ["IDV(11)", "IDV(14)"])


class ExperimentsTest(unittest.TestCase):
    def test_mode_configuration_round_trips_and_exports_all_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_experiments(Path(directory)).server.test_client()
            config = {"name": "regression", "mode": "mode3", "seed": 42, "horizon": 0.15,
                      "disturbances": [{"idv": "idv_04", "start_time": 0.03}]}
            result = client.post("/experiments/api/run", json=config)
            self.assertEqual(result.status_code, 201, result.get_data(as_text=True))
            run = result.json["run_id"]
            saved = client.get(f"/experiments/api/scenario/{run}").json
            for key, value in config.items():
                if key == "disturbances":
                    self.assertEqual(saved[key][0]["start_time"], value[0]["start_time"])
                else:
                    self.assertEqual(saved[key], value)
            replay = client.post("/experiments/api/run", json=saved)
            self.assertEqual(replay.json["minimum_constraint_margins"], result.json["minimum_constraint_margins"])
            for fmt in ("csv", "json", "parquet"):
                payload = client.get(f"/experiments/api/dataset?run_id={run}&format={fmt}")
                self.assertEqual(payload.status_code, 200)
                self.assertTrue(payload.data)
            self.assertTrue(client.get(f"/experiments/api/dataset?run_id={run}&format=parquet").data.startswith(b"PAR1"))

    def test_zoro_and_fallback_route_to_real_experiments_and_write_logs(self):
        tools = TepToolset()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "trace.jsonl"
            replies = [{"answers": {"tool": {"choice": "run_scenario", "confidence": 0.92}}},
                       {"choices": [{"message": {"content": '{"config":{"horizon":0.15}}'}}]}]
            with patch.dict("os.environ", {"ZORO_BASE_URL": "http://127.0.0.1:30001"}):
                with patch("zoro.client._request", side_effect=replies) as request:
                    zoro = respond("0.15시간 실행", [], tools, log)
                    self.assertTrue(request.call_args_list[0].args[0].endswith("/v1/systemone"))
                    self.assertFalse(request.call_args_list[0].args[1]["reasoning"])
            with patch.dict("os.environ", {"ZORO_BASE_URL": ""}):
                fallback = respond("IDV4를 0.05시간 후 적용하고 0.2시간 실행을 정상 상태와 비교해줘", [], tools, log)
            self.assertEqual(len(zoro.run_ids), 1)
            self.assertEqual(len(fallback.run_ids), 2)
            self.assertAlmostEqual(tools.store.get(fallback.run_ids[-1]).scenario.horizon, 0.2)
            traces = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([r["fallback"] for r in traces], [False, True])
            self.assertEqual([r["result"] for r in traces], ["ok", "ok"])


if __name__ == "__main__":
    unittest.main()
