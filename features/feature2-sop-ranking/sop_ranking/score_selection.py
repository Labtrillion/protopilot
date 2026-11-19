"""Score individual setpoints, then generate trials for the two highest-ranked."""

from itertools import product

from zoro.client import zoro_decide

SCORE_BATCH_SIZE = 12
SETPOINT_MOVES = (("decrease_10pct", -0.10), ("increase_10pct", 0.10))
TRIAL_CRITERIA = (
    "Poor trial: an expected operating-limit violation, material harm, or no plausible benefit given available evidence.",
    "Reasonable trial: a plausible way to maintain or improve operation; any net benefit is still uncertain.",
    "Promising trial: specific evidence supports a meaningful gain in at least one metric with acceptable trade-offs.",
)


def allowed_moves(name, row):
    current = row["setpoint"]
    low, high = row["limits"]
    moves = []
    for direction, move in SETPOINT_MOVES:
        target = round(current * (1 + move), 6)
        if low <= target <= high and target != current:
            moves.append((f"{name}:{direction}", {
                "setpoint": name, "from": current, "to": target,
                "move": move, "unit": row["unit"],
            }))
    return moves


def build_candidates(first_moves, second_moves):
    candidates = {}
    for (first_id, first), (second_id, second) in product(first_moves, second_moves):
        candidate_id = f"{first_id}+{second_id}"
        candidates[candidate_id] = {"id": candidate_id, "changes": [first, second]}
    return candidates


def score_trials(context, descriptions, *, comparison, timeout, request_id, criteria=TRIAL_CRITERIA):
    questions = {
        name: {
            "type": "score",
            "instructions": (
                f"{comparison} Assess this candidate: {description}. "
                "Rate its suitability for a simulator trial. "
                "Use current observations, operating procedures and alarm_rag when available. "
                "Favour lower cost per unit produced, higher tracking accuracy and lower cumulative production loss "
                "while respecting operating limits. A plausible trial with uncertain benefit fits the middle level; "
                "the available evidence can support the direction without specifying these exact target values."
            ),
            "criteria": list(criteria),
        }
        for name, description in descriptions.items()
    }
    scores = {}
    names = sorted(questions)
    for start in range(0, len(names), SCORE_BATCH_SIZE):
        batch = {name: questions[name] for name in names[start:start + SCORE_BATCH_SIZE]}
        batch_id = start // SCORE_BATCH_SIZE + 1
        answers = zoro_decide(context, batch, timeout=timeout, request_id=f"{request_id}-score-{batch_id}")
        scores.update({name: float(answers[name]["score"]) for name in batch})
    return scores


def trial_context(handoff, state):
    procedures = state["operating_procedures"]
    alarm_rag = next((procedure["rag"] for procedure in procedures if "rag" in procedure), None)
    context = {
        "plant": state["plant"], "setpoints": state["setpoints"],
        "operating_procedures": [procedure for procedure in procedures if "rag" not in procedure],
        "baseline_changes": handoff["baseline_changes"], "evaluation_evidence": handoff["evaluation_policy"]["evidence"],
    }
    if alarm_rag is not None:
        context["alarm_rag"] = alarm_rag
    return context


def select_candidates(handoff, state, *, timeout, request_id):
    locked = {change["setpoint"] for change in handoff["baseline_changes"]}
    remaining = {name: allowed_moves(name, row) for name, row in state["setpoints"].items() if name not in locked}
    selection = {"scores": {}, "selected_setpoints": [], "selected": [], "reason": None}
    if not remaining:
        return {**selection, "reason": "no_alternatives"}
    context = trial_context(handoff, state)
    descriptions = {
        name: f"{name} at {state['setpoints'][name]['setpoint']} {state['setpoints'][name]['unit']}; valid changes: "
        + (", ".join(f"{move['move']:+.0%} to {move['to']}" for _, move in moves) or "none")
        for name, moves in remaining.items()
    }
    try:
        scores = score_trials(
            context, descriptions, comparison=(
                "Keep the Feature 1 baseline changes fixed. Score how useful it would be to test changing "
                "this individual setpoint by up to 10%. Consider only the valid changes listed for this setpoint."
            ),
            timeout=timeout, request_id=request_id,
        )
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as error:
        return {**selection, "reason": f"score_unavailable:{type(error).__name__}"}
    selection["scores"] = {name: {"score": score} for name, score in scores.items()}
    changeable = (name for name, moves in remaining.items() if moves)
    ranked = sorted(changeable, key=lambda name: (-scores[name], name))
    if len(ranked) < 2:
        return {**selection, "reason": "no_alternatives"}
    first, second = ranked[:2]
    candidates = build_candidates(remaining[first], remaining[second])
    selected = list(candidates)

    first_choice = None
    choice_error = None
    if len(selected) > 1:
        first_question = {"first": {
            "type": "choice",
            "instructions": "Order the trials by choosing which change to simulate first using the alarm RAG and SOP evidence.",
            "criteria": {
                name: ", ".join(f"{move['setpoint']} {move['move']:+.0%} to {move['to']} {move['unit']}"
                                for move in candidate["changes"])
                for name, candidate in candidates.items()
            },
        }}
        try:
            first_choice = zoro_decide(context, first_question, timeout=timeout,
                                       request_id=f"{request_id}-choice")["first"]["choice"]
            selected.remove(first_choice)
            selected.insert(0, first_choice)
        except (AttributeError, KeyError, TypeError, ValueError, OSError) as error:
            choice_error = type(error).__name__
    return {
        **selection, "selected_setpoints": [first, second], "selected": [candidates[name] for name in selected],
        "first_choice": first_choice, "choice_error": choice_error,
    }
