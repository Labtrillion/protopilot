import json
from collections import Counter
from math import isclose
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

from server.app import ConsoleHandler
from server.plant import Plant
from server.scenario import STEP_HOURS
from sop_ranking.score_selection import build_candidates, select_candidates


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
    score_batches = []
    locked = {"production_rate", "reactor_pressure"}

    def reply(url, payload, *, timeout, request_id=None):
        assert url.endswith("/v1/systemone")
        questions = payload["questions"]
        qtype = next(iter(questions.values()))["type"]
        if qtype == "noul":
            scores = {"production_rate": 0.98, "reactor_pressure": 0.95, "reactor_temperature": 0.90}
            answers = {name: {"type": "noul", "noul": scores.get(name, 0.1)} for name in questions}
        elif qtype == "score":
            score_batches.append(len(questions))
            score_questions.extend(sorted(questions))
            for name, question in questions.items():
                moves = [part.split(":") for part in name.split("+")]
                setpoints = {setpoint for setpoint, _ in moves}
                assert len(moves) == len(setpoints) == 2
                assert locked.isdisjoint(setpoints)
                assert all(direction in {"decrease_10pct", "increase_10pct"} for _, direction in moves)
                assert all(setpoint in question["instructions"] for setpoint in setpoints)
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
    assert score_batches == [12] * 5
    assert feature2["selection"]["coverage"] == 0.9
    assert [case["id"] for case in feature2["evaluated"]] == [score_questions[i] for i in (2, 0, 1)]
    for case in feature2["evaluated"]:
        assert {change["setpoint"] for change in case["changes"][:2]} == locked
        additions = case["changes"][2:]
        assert len(additions) == 2
        assert len({change["setpoint"] for change in additions}) == 2
        for change in additions:
            assert change["setpoint"] not in locked
            assert abs(change["move"]) == 0.10
            assert isclose(change["to"], change["from"] * (1 + change["move"]), abs_tol=1e-6)
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
        "setpoints": {
            "pressure": {"setpoint": 100, "limits": [0, 200], "unit": "kPa"},
            "level": {"setpoint": 50, "limits": [0, 100], "unit": "%"},
        },
    }

    def reply(context, questions, **kwargs):
        assert "alarm_rag" not in context
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
            "temperature": {"setpoint": 100, "limits": [90, 109], "unit": "C"},
            "zero": {"setpoint": 0, "limits": [0, 100], "unit": "%"},
        },
    }

    def reply(context, questions, **kwargs):
        candidate_id = "level:decrease_10pct+temperature:decrease_10pct"
        assert list(questions) == [candidate_id]
        return {candidate_id: {"type": "score", "score": 1}}

    monkeypatch.setattr("sop_ranking.score_selection.zoro_decide", reply)
    result = select_candidates(handoff, state, timeout=5, request_id="test")
    assert [change["to"] for change in result["selected"][0]["changes"]] == [45, 90]
    assert result["coverage"] == 1


def test_six_setpoints_produce_sixty_directional_pairs():
    setpoints = {f"setpoint_{i}": {"setpoint": 50, "limits": [0, 100], "unit": "%"} for i in range(6)}
    candidates = build_candidates(setpoints, set())
    directions = Counter()
    for candidate in candidates.values():
        first, second = candidate["changes"]
        assert first["setpoint"] != second["setpoint"]
        directions[(first["setpoint"], second["setpoint"], first["move"], second["move"])] += 1
    assert len(candidates) == len(directions) == 60


def test_feature1_alarm_rag_reaches_every_model_request(monkeypatch):
    expected_alarm_rag = {
        "event_id": "alarm-rag-test",
        "evidence": [{
            "evidence_id": "PRESSURE", "statement": "Check reactor pressure.",
            "source_document": "TEP SOP", "reranker_score": 0.95,
        }],
        "sop_plans": [{"evidence_id": "PRESSURE", "status": "review_required", "parameter": "reactor_pressure"}],
    }
    rule = {"id": "static-sop", "text": "Observe the operating envelope."}
    handoff = {"baseline_changes": [], "evaluation_policy": {"evidence": []}}
    state = {
        "plant": {},
        "operating_procedures": [rule, {"id": "alarm-rag-test", "title": "Alarm RAG evidence", "rag": expected_alarm_rag}],
        "setpoints": {f"setpoint_{i}": {"setpoint": 50, "limits": [0, 100], "unit": "%"} for i in range(6)},
    }
    requests = []

    def reply(context, questions, **kwargs):
        assert context["alarm_rag"] == expected_alarm_rag
        assert context["operating_procedures"] == [rule]
        if "first" in questions:
            requests.append("choice")
            return {"first": {"type": "choice", "choice": next(iter(questions["first"]["criteria"]))}}
        requests.append("score")
        return {name: {"type": "score", "score": 1} for name in questions}

    monkeypatch.setattr("sop_ranking.score_selection.zoro_decide", reply)
    result = select_candidates(handoff, state, timeout=5, request_id="test")
    assert requests == ["score"] * 5 + ["choice"]
    assert len(result["selected"]) == 54
