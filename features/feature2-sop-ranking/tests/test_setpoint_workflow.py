import json
from itertools import product
from math import isclose
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

from server.alarm_studies import AlarmStudies

from server.app import ConsoleHandler, analyze_alarm_setpoints
from server.plant import MEASUREMENT_TAGS, Plant
from server.scenario import STEP_HOURS
from sop_ranking.score_selection import select_candidates


def test_console_evaluates_four_combinations_of_the_top_two_setpoints(tmp_path, monkeypatch):
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
    top = ["reactor_temperature", "separator_level"]
    first_choice = None
    tested_branches = []

    def reply(url, payload, *, timeout, request_id=None):
        nonlocal first_choice
        assert url.endswith("/v1/systemone")
        questions = payload["questions"]
        qtype = next(iter(questions.values()))["type"]
        if qtype == "noul":
            scores = {"production_rate": 0.98, "reactor_pressure": 0.95, "reactor_temperature": 0.90}
            answers = {name: {"type": "noul", "noul": scores.get(name, 0.1)} for name in questions}
        elif qtype == "score" and "feature1" in questions:
            assert len(questions) == 5
            assert request_id.endswith("-plans-score-1")
            for name, question in questions.items():
                assert all(key in question["instructions"] for key in locked)
                if name != "feature1":
                    assert all(key in question["instructions"] for key in top)
            answers = {name: {"type": "score", "score": 0.8 if name == "feature1" else 1.2} for name in questions}
        elif qtype == "score":
            score_batches.append(len(questions))
            score_questions.extend(sorted(questions))
            for name, question in questions.items():
                assert name not in locked
                assert name in question["instructions"]
                assert all(metric in question["instructions"] for metric in
                           ("cost per unit produced", "tracking accuracy", "production loss"))
                assert [level.split(":", 1)[0] for level in question["criteria"]] == [
                    "Poor trial", "Reasonable trial", "Promising trial",
                ]
            ratings = dict(zip(top, [1.8, 1.2]))
            answers = {name: {"type": "score", "score": ratings.get(name, 0.5)} for name in questions}
        elif "first" in questions:
            criteria = questions["first"]["criteria"]
            assert len(criteria) == 4
            assert "feature1" not in criteria
            first_choice = next(iter(criteria))
            answers = {"first": {"type": "choice", "choice": first_choice}}
        else:
            assert set(questions) == locked
            answers = {name: {"type": "choice", "choice": "decrease_small"} for name in questions}
        return {"answers": answers}

    from sop_ranking import evaluate

    run_branch = evaluate.run_branch

    def capture_branch(runner, snapshot, case_id, changes, policy):
        tested_branches.append(case_id)
        return run_branch(runner, snapshot, case_id, changes, policy)

    monkeypatch.setattr(evaluate, "run_branch", capture_branch)
    monkeypatch.setattr("zoro.client._request", reply)
    ConsoleHandler.setpoint_decision(handler, {"run": plant.recording.id})
    assert handler.respond.call_count == 1
    data = handler.respond.call_args.args[0]
    assert {item["setpoint"] for item in data["handoff"]["baseline_changes"]} == locked
    feature2 = data["feature2"]
    selection = feature2["selection"]
    assert score_batches == [6]
    assert len(set(score_questions)) == len(selection["scores"]) == 6
    assert selection["selected_setpoints"] == top
    assert selection["first_choice"] == feature2["evaluated"][0]["id"] == first_choice
    assert len(tested_branches) == len(set(tested_branches)) == 5
    assert tested_branches[0] == "feature1"
    cases = feature2["evaluated"]
    assert len(cases) == 4
    assert feature2["response_scores"] == {"feature1": 0.8, **{case["id"]: 1.2 for case in cases}}
    assert feature2["response_score_reason"] is None
    assert {tuple(change["move"] for change in case["moves"]) for case in cases} == set(product((-0.1, 0.1), repeat=2))
    for case in cases:
        assert [move["setpoint"] for move in case["moves"]] == top
        assert {change["setpoint"] for change in case["changes"][:2]} == locked
        assert case["changes"][2:] == case["moves"]
        for move in case["moves"]:
            assert isclose(move["to"], move["from"] * (1 + move["move"]), abs_tol=1e-6)
        assert {"cost_per_m3", "tracking_accuracy", "production_loss_m3"} <= case.keys()
    assert plant.sim.snapshot().model_bytes == before
    trace = json.loads((tmp_path / "agent-trace.jsonl").read_text())
    assert trace["feature2"]["selection"] == feature2["selection"]


def test_equal_zero_scores_still_select_two_setpoints(monkeypatch):
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
        if "first" in questions:
            return {"first": {"choice": next(iter(questions["first"]["criteria"]))}}
        return {name: {"type": "score", "score": 0} for name in questions}

    monkeypatch.setattr("sop_ranking.score_selection.zoro_decide", reply)
    result = select_candidates(handoff, state, timeout=5, request_id="test")
    assert result["selected_setpoints"] == ["level", "pressure"]
    assert len(result["selected"]) == 4


def test_candidate_grid_omits_out_of_range_moves(monkeypatch):
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
        if "first" in questions:
            return {"first": {"choice": next(iter(questions["first"]["criteria"]))}}
        assert set(questions) == {"level", "temperature", "zero"}
        return {key: {"type": "score", "score": 2 if key == "zero" else 1} for key in questions}

    monkeypatch.setattr("sop_ranking.score_selection.zoro_decide", reply)
    result = select_candidates(handoff, state, timeout=5, request_id="test")
    assert result["scores"]["zero"]["score"] == 2
    assert result["selected_setpoints"] == ["level", "temperature"]
    assert {tuple(move["to"] for move in case["changes"]) for case in result["selected"]} == {(45, 90)}


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
    assert requests == ["score", "choice"]
    assert len(result["scores"]) == 6
    assert len(result["selected"]) == 4


def test_alarm_endpoint_evaluates_the_checkpoint_even_after_live_changes(tmp_path, monkeypatch):
    plant = Plant(tmp_path)
    plant.command({"op": "cooling_demo"})
    while not plant.alarm_checkpoints:
        plant.tick()
    checkpoint = next(iter(plant.alarm_checkpoints.values()))
    alarm_id = checkpoint.alarm["id"]
    trace_path = tmp_path / "agent-trace.jsonl"
    plant.command({"op": "setpoint", "id": "reactor_pressure", "value": 2550})

    def analyze(point, path, *, evaluate_alternatives):
        assert point.step == checkpoint.step
        assert point.native.model_bytes == checkpoint.native.model_bytes
        assert point.controller.setpoints.reactor_pressure == checkpoint.controller.setpoints.reactor_pressure
        assert point.controller.setpoints.reactor_pressure != plant.controller.setpoints.reactor_pressure
        assert any(alarm["tag"] == checkpoint.alarm["tag"] for alarm in point.alarms)
        assert point.history[-1].time_hours == checkpoint.alarm["time_hours"]
        tag_index = MEASUREMENT_TAGS.index(checkpoint.alarm["tag"])
        assert point.history[-1].values[tag_index] == checkpoint.alarm["value"]
        assert path == trace_path
        return {"decision": {"status": "human_review"}, "feature2": {}, "branches": []}

    monkeypatch.setattr("server.app.analyze_setpoints", analyze)
    studies = AlarmStudies(trace_path, workers=1, analyze=lambda point: analyze_alarm_setpoints(point, trace_path))
    handler = SimpleNamespace(plant=plant, lock=RLock(), setpoint_studies=studies, respond=Mock())
    handler.alarm_setpoint_decision = lambda body: ConsoleHandler.alarm_setpoint_decision(handler, body)
    handler.alarm_study_status = lambda *args: ConsoleHandler.alarm_study_status(handler, *args)
    try:
        ConsoleHandler.setpoint_decision(handler, {"run": plant.recording.id, "alarm_id": alarm_id})
        _, job = studies.submit(checkpoint)
        result = job.result(timeout=5)
        assert result["decision"]["status"] == "human_review"
        assert plant.controller.setpoints.reactor_pressure == 2550
    finally:
        studies.close()
