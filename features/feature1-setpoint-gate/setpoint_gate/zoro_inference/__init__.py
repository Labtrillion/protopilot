"""Retrieve alarm evidence and ask Zoro-1 to choose a pair of setpoint changes."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.catalog import CHANNELS
from zoro.client import zoro_decide

RAG_ROOT = Path(__file__).resolve().parents[2] / "rag"
REQUIRED_CHANGES = 2


def retrieve_alarm(point, checkpoint, *, timeout):
    """Use the observed alarms from the captured checkpoint."""
    now = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(hours=point.time_hours)
    event = {
        "event_id": f"{point.run_id}:{point.step}",
        "timestamp": now.isoformat(),
        "snapshot_id": checkpoint,
        "source": "protopilot_ricker_simulator",
        "alarms": [
            {"alarm_id": alarm["rule"], "tag": alarm["tag"], "severity": alarm["kind"],
             "current_value": alarm["value"], "threshold": alarm["limit"],
             "direction": alarm["side"], "unit": CHANNELS.get(alarm["tag"], {}).get("unit"),
             "activated_at": (datetime(1970, 1, 1, tzinfo=timezone.utc)
                              + timedelta(hours=alarm["time_hours"])).isoformat()}
            for alarm in point.alarms
        ],
    }
    local_python = RAG_ROOT / ".venv/bin/python"
    python = os.getenv("PROTOPILOT_RAG_PYTHON") or (str(local_python) if local_python.is_file() else sys.executable)
    completed = subprocess.run(
        [python, str(RAG_ROOT / "scripts/retrieve_for_gate.py")],
        input=json.dumps(event, allow_nan=False), capture_output=True, text=True, check=True, timeout=timeout,
    )
    result = json.loads(completed.stdout)
    if result["event_id"] != event["event_id"] or result["alarm_context"]["snapshot_id"] != checkpoint:
        raise ValueError("RAG result does not match the captured event.")
    return result


def evidence_for_zoro(result):
    """Keep the extracted statements and scores bound to their original sources."""
    return {
        "event_id": result["event_id"],
        "status": result["status"],
        "evidence": [
            {
                **{key: item[key] for key in (
                    "evidence_id", "evidence_type", "statement", "source_document",
                    "source_section", "source_page", "source_version", "source_url",
                )},
                **{key: item["provenance"].get(key) for key in ("rrf_score", "reranker_score", "source_type")},
            }
            for item in result["evidence"]
        ],
        "candidate_actions": [
            {key: action[key] for key in ("evidence_id", "status", "plan", "input_review_reasons")}
            for action in result["candidate_actions"]
        ],
        "missing_evidence": result["missing_evidence"],
        "unresolved_questions": result["unresolved_questions"],
    }


def decide_pair(state, gates, choices, move_by_choice, *, threshold, timeout, request_id, result):
    table = state["setpoints"]
    for name, options in move_by_choice.items():
        row = table[name]
        allowed = {key: move for key, move in options.items()
                   if move and round(row["setpoint"] * (1 + move), 6) != row["setpoint"]}
        move_by_choice[name] = allowed
        choices[name]["criteria"] = {key: choices[name]["criteria"][key] for key in allowed}

    answers = zoro_decide(state, gates, timeout=timeout, request_id=f"{request_id}-noul")
    scores = {name: float(answers[name]["noul"]) for name in gates}
    result["gate"] = {name: round(score, 4) for name, score in scores.items()}
    eligible = [name for name in scores if scores[name] >= threshold and move_by_choice[name]]
    selected = sorted(eligible, key=scores.__getitem__, reverse=True)[:REQUIRED_CHANGES]
    if len(selected) < REQUIRED_CHANGES:
        result["reason"] = "fewer_than_two_eligible_setpoints"
        return []

    answers = zoro_decide(
        {**state, "setpoints_to_change": {name: scores[name] for name in selected}},
        {name: choices[name] for name in selected}, timeout=timeout, request_id=f"{request_id}-choice",
    )
    changes = []
    for name in selected:
        answer = answers[name]
        options = move_by_choice[name]
        move = options[answer["choice"]]
        result["moves"][name] = {
            "choice": answer["choice"], "move": move, "confidence": answer.get("confidence"),
            "probabilities": {f"{options[key] * 100:+.0f}%": round(float(value), 4)
                              for key, value in answer.get("probabilities", {}).items()},
        }
        current = table[name]["setpoint"]
        changes.append({"setpoint": name, "from": current, "to": round(current * (1 + move), 6), "move": move})
    return changes
