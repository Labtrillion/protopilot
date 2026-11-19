import json
from math import isclose
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

from server.app import ConsoleHandler
from server.plant import Plant
from server.scenario import STEP_HOURS
from sop_ranking.score_selection import select_candidates


def test_console_evaluates_only_candidates_covering_ninety_percent(tmp_path, monkeypatch):
    plant = Plant(tmp_path)
    plant.command({"op": "speed", "value": 960})
    while len(plant.history) < 2:
        plant.tick()
    before = plant.sim.snapshot().model_bytes
    handler = SimpleNamespace(plant=plant, lock=RLock(), respond=Mock())
    monkeypatch.setenv("ZORO_BASE_URL", "http://zoro.invalid")
    monkeypatch.setattr("setpoint_gate.setpoint_decision.BRANCH_HOURS", 20 * STEP_HOURS)
    score_questions = []
    locked = {"production_rate", "reactor_pressure"}

    def reply(url, payload, *, timeout, request_id=None):
        assert url.endswith("/v1/systemone")
        questions = payload["questions"]
        qtype = next(iter(questions.values()))["type"]
        if qtype == "noul":
            scores = {"production_rate": 0.98, "reactor_pressure": 0.95, "reactor_temperature": 0.90}
            answers = {name: {"type": "noul", "noul": scores.get(name, 0.1)} for name in questions}
        elif qtype == "score":
            score_questions.extend(sorted(questions))
            assert locked.isdisjoint(name.split(":")[0] for name in questions)
            assert {name.split(":")[1] for name in questions} == {"decrease_10pct", "increase_10pct"}
            ratings = dict(zip(score_questions, [1.8, 1.2, 0.6, 0.4]))
            answers = {name: {"type": "score", "score": ratings.get(name, 0)} for name in questions}
        elif "first" in questions:
            assert set(questions["first"]["criteria"]) == set(score_questions[:3])
            answers = {"first": {"type": "choice", "choice": score_questions[2]}}
        else:
            assert set(questions) == locked
            answers = {name: {"type": "choice", "choice": "decrease_small"} for name in questions}
        return {"answers": answers}

    monkeypatch.setattr("zoro.client._request", reply)
    ConsoleHandler.setpoint_decision(handler, {"run": plant.recording.id})
    assert handler.respond.call_count == 1
    data = handler.respond.call_args.args[0]
    assert {item["setpoint"] for item in data["handoff"]["baseline_changes"]} == locked
    feature2 = data["feature2"]
    assert feature2["selection"]["coverage"] == 0.9
    assert [case["id"] for case in feature2["evaluated"]] == [score_questions[i] for i in (2, 0, 1)]
    for case in feature2["evaluated"]:
        assert {change["setpoint"] for change in case["changes"][:2]} == locked
        alternative = case["changes"][2]
        assert alternative["setpoint"] not in locked
        assert abs(alternative["move"]) == 0.10
        assert isclose(alternative["to"], alternative["from"] * (1 + alternative["move"]), abs_tol=1e-6)
        assert {"cost_per_m3", "tracking_accuracy", "production_loss_m3"} <= case.keys()
    assert plant.sim.snapshot().model_bytes == before
    trace = json.loads((tmp_path / "agent-trace.jsonl").read_text())
    assert trace["feature2"]["selection"] == feature2["selection"]


def test_zero_scores_select_no_candidates(monkeypatch):
    handoff = {
        "baseline_changes": [],
        "evaluation_policy": {"evidence": []},
    }
    state = {
        "plant": {}, "operating_procedures": [],
        "setpoints": {"pressure": {"setpoint": 100, "limits": [0, 200], "unit": "kPa"}},
    }

    def reply(context, questions, **kwargs):
        return {name: {"type": "score", "score": 0} for name in questions}

    monkeypatch.setattr("sop_ranking.score_selection.zoro_decide", reply)
    result = select_candidates(handoff, state, timeout=5, request_id="test")
    assert result["selected"] == []
    assert result["reason"] == "zero_scores"


def test_only_in_range_ten_percent_movements_are_scored(monkeypatch):
    handoff = {
        "baseline_changes": [{"setpoint": "pressure"}],
        "evaluation_policy": {"evidence": []},
    }
    state = {
        "plant": {}, "operating_procedures": [],
        "setpoints": {
            "pressure": {"setpoint": 100, "limits": [0, 200], "unit": "kPa"},
            "level": {"setpoint": 50, "limits": [45, 54], "unit": "%"},
            "zero": {"setpoint": 0, "limits": [0, 100], "unit": "%"},
        },
    }

    def reply(context, questions, **kwargs):
        assert list(questions) == ["level:decrease_10pct"]
        return {"level:decrease_10pct": {"type": "score", "score": 1}}

    monkeypatch.setattr("sop_ranking.score_selection.zoro_decide", reply)
    result = select_candidates(handoff, state, timeout=5, request_id="test")
    assert result["selected"][0]["to"] == 45
    assert result["coverage"] == 1
