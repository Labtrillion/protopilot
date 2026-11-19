"""Select the smallest prefix covering 90% of the candidate score mass."""

from math import fsum, isclose
from urllib.error import URLError

from zoro.client import zoro_decide

COVERAGE = 0.90
SETPOINT_MOVES = (("decrease_10pct", -0.10), ("increase_10pct", 0.10))


def build_candidates(setpoints, locked):
    candidates = {}
    for name, row in setpoints.items():
        if name in locked:
            continue
        current = row["setpoint"]
        low, high = row["limits"]
        for direction, move in SETPOINT_MOVES:
            target = round(current * (1 + move), 6)
            if low <= target <= high and target != current:
                candidate_id = f"{name}:{direction}"
                candidates[candidate_id] = {
                    "id": candidate_id, "setpoint": name, "from": current,
                    "to": target, "move": move, "unit": row["unit"],
                }
    return candidates


def select_candidates(handoff, state, *, timeout, request_id):
    locked = {change["setpoint"] for change in handoff["baseline_changes"]}
    candidates = build_candidates(state["setpoints"], locked)
    selection = {"scores": {}, "selected": [], "coverage": 0.0, "reason": None}
    if not candidates:
        return {**selection, "reason": "no_alternatives"}
    context = {
        "plant": state["plant"], "setpoints": state["setpoints"],
        "operating_procedures": state["operating_procedures"],
        "baseline_changes": handoff["baseline_changes"], "evaluation_evidence": handoff["evaluation_policy"]["evidence"],
    }
    questions = {
        name: {
            "type": "score",
            "instructions": (
                f"With the baseline changes fixed, assess the case for changing {item['setpoint']} "
                f"from {item['from']} to {item['to']} {item['unit']}. Consider SOP evidence and "
                "potential for better cost, tracking accuracy or production without breaching operating limits."
            ),
            "criteria": ["Unsupported", "Some support", "Strong support"],
        }
        for name, item in candidates.items()
    }
    try:
        answers = zoro_decide(context, questions, timeout=timeout, request_id=f"{request_id}-score")
        scores = {name: float(answers[name]["score"]) for name in candidates}
    except (AttributeError, KeyError, TypeError, ValueError, URLError, TimeoutError) as error:
        return {**selection, "reason": f"score_unavailable:{type(error).__name__}"}
    total = fsum(scores.values())
    if total == 0:
        return {**selection, "reason": "zero_scores",
                "scores": {name: {"score": score, "weight": 0.0} for name, score in scores.items()}}

    weights = {name: score / total for name, score in scores.items()}
    selected = []
    coverage = 0.0
    for name in sorted(candidates, key=lambda key: (-weights[key], key)):
        selected.append(name)
        coverage = fsum(weights[key] for key in selected)
        if coverage >= COVERAGE or isclose(coverage, COVERAGE, abs_tol=1e-12, rel_tol=0.0):
            break

    first_choice = None
    choice_error = None
    if len(selected) > 1:
        first_question = {"first": {
            "type": "choice",
            "instructions": "Choose which already shortlisted alternative to simulate first.",
            "criteria": {name: f"{candidates[name]['setpoint']} to {candidates[name]['to']} {candidates[name]['unit']}"
                         for name in selected},
        }}
        try:
            first_choice = zoro_decide(context, first_question, timeout=timeout,
                                       request_id=f"{request_id}-choice")["first"]["choice"]
            selected.remove(first_choice)
            selected.insert(0, first_choice)
        except (AttributeError, KeyError, TypeError, ValueError, URLError, TimeoutError) as error:
            choice_error = type(error).__name__
    return {
        "scores": {name: {"score": scores[name], "weight": weights[name]} for name in candidates},
        "selected": [candidates[name] for name in selected], "coverage": coverage, "reason": None,
        "first_choice": first_choice, "choice_error": choice_error,
    }
