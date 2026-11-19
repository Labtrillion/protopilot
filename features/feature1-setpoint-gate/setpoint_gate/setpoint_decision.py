"""Zoro-1 selects two in-range setpoint changes for an isolated TEP comparison."""


import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from subprocess import SubprocessError
from time import perf_counter
from uuid import uuid4

import numpy as np
from tep_studio import TEP_SCHEMA, TennesseeEastmanProcess

from server.agent_config import CONFIG
from server.branch_metrics import branch_summary, step_cost
from server.sop_lookup import find_sop
from server.assistant import append_trace
from setpoint_rag import evaluation_policy
from setpoint_rag.sop_align import match_sensor_sop
from server.catalog import PV_TAGS, SETPOINTS
from server.plant import MEASUREMENT_TAGS, control_action
from server.recording import timestamp
from server.scenario import KERNEL_REVISION, STEP_HOURS, adjustment

from . import zoro_inference

RULEBOOK = json.loads(Path(CONFIG["rulebook_path"]).read_text(encoding="utf-8"))
NEED_THRESHOLD = CONFIG["need_threshold"]
BRANCH_HOURS = CONFIG["branch_hours"]
TREND_WINDOW_HOURS = CONFIG["trend_window_hours"]
# (small, large) relative moves per decision; production follows the SOP's 5 % steps.
CATEGORY_STEPS = CONFIG["category_steps"]
DEFAULT_STEPS = CONFIG["default_category_steps"]
PRESS1_WATCH_KPA = 2800.0  # mirrors the PRESS-1 rule text in the rulebook, not a tuning knob
# (answer key, direction, size index into the (small, large) steps or None for keep, option text)
CATEGORIES = (("decrease_large", -1, 1, "decrease a lot"), ("decrease_small", -1, 0, "decrease slightly"),
              ("keep", 0, None, "keep unchanged"), ("increase_small", 1, 0, "increase slightly"),
              ("increase_large", 1, 1, "increase a lot"))
SITUATION = (
    "You supervise the Tennessee Eastman chemical process. A decentralized PI controller (Ricker 1996) runs the "
    "plant underneath you. You may change operator setpoints of its loops, or hold. production_rate is ramped at "
    "a rate limit, so it takes hours to take effect; the other setpoints act at the next control step. The plant "
    "trips if reactor pressure reaches 3000 kPa, reactor temperature 175 degC, or a reactor / separator / stripper "
    "level reaches 0 % or 100 %. Production lost to a cut or a trip is a loss. Avoid unnecessary changes. "
    "Use the documented evidence with the observed plant state. RAG scores measure retrieval relevance. "
    "Use only simulator-compatible SOP plans with confirmed inputs to support a change."
)


@dataclass(frozen=True)
class DecisionPoint:
    """Everything a decision and its branches need, copied from the live plant under its lock."""

    run_id: str
    step: int
    seed: int
    time_hours: float
    native: object
    controller: object
    measurements: np.ndarray
    manual: dict
    idv: np.ndarray
    history: tuple
    base_setpoints: object
    timeline: tuple
    alarms: tuple  # active alarms on the observed channel of `monitor`, as {kind, tag, side, value, limit, time_hours}
    limits: dict
    offsets: dict
    monitor: object  # AlarmMonitor copy: branches keep counting `for_minutes` from here, minute counters at zero


def monitor_alarms(monitor):
    """The monitor's observed alarms in the shape SOP lookup and the local rule use."""
    alarms = []
    for rule_id, hit in monitor.active["observed"].items():
        side = rule_id.rsplit(" ", 1)[-1] if rule_id.startswith("LIMIT ") else None
        alarms.append({"kind": "warning", "tag": hit["rule"].get("tag"), "side": side, "value": hit.get("value"),
                       "limit": hit.get("limit"), "time_hours": hit["since_hours"], "rule": rule_id})
    return tuple(alarms)


def capture_point(plant):
    monitor = plant.alarm_monitor.copy()
    return DecisionPoint(
        plant.recording.id, plant.step, plant.seed, float(plant.sim.time), plant.sim.snapshot(),
        deepcopy(plant.controller), plant.measurements.copy(), plant.manual.copy(), plant.idv.copy(),
        tuple(plant.history), plant.base_setpoints, tuple(plant.timeline), monitor_alarms(monitor),
        dict(plant.limits), dict(plant.offsets), monitor,
    )


def adjustable_setpoints(controller):
    return [name for name in SETPOINTS if name != "pct_g" or controller.enable_pct_g_feedback]


def _slope(times, values):
    if len(times) < 2:
        return 0.0
    t = np.asarray(times) - np.mean(times)
    denom = float(np.dot(t, t))
    return float(np.dot(t, np.asarray(values) - np.mean(values)) / denom) if denom > 0 else 0.0


def sensor_summary(point):
    times = np.array([s.time_hours for s in point.history])
    recent = times >= times[-1] - TREND_WINDOW_HOURS - 1e-9
    rows = []
    for j, var in enumerate(TEP_SCHEMA.measurements):
        series = np.array([np.nan if s.values[j] is None else s.values[j] for s in point.history], dtype=float)
        ok = np.isfinite(series)
        if not ok.any():
            continue
        trend_ok = ok & recent
        rows.append({
            "name": var.name, "unit": var.unit, "last": round(float(series[ok][-1]), 4),
            "min": round(float(series[ok].min()), 4), "max": round(float(series[ok].max()), 4),
            "trend_per_h": round(_slope(times[trend_ok], series[trend_ok]), 4),
        })
    return rows


def latest_value(point, tag):
    value = point.history[-1].values[MEASUREMENT_TAGS.index(tag)]
    return None if value is None else float(value)


def command_summary(point):
    static = set(point.controller.registry.nominal.static_mv)
    commands = np.array([s.commands for s in point.history])
    rows = []
    for i, var in enumerate(TEP_SCHEMA.manipulated_variables):
        rows.append({
            "name": var.name, "command_pct": round(float(commands[-1, i]), 3),
            "frac_at_upper": round(float(np.mean(commands[:, i] >= 100 - 1e-6)), 3),
            "frac_at_lower": round(float(np.mean(commands[:, i] <= 1e-6)), 3),
            "held_constant": var.name in static,
        })
    return rows


def protection_summary(point):
    keys = point.controller.registry.overrides
    return {ov.name: {"active_now": ov.name in point.history[-1].protection,
                      "active_fraction": round(float(np.mean([ov.name in s.protection for s in point.history])), 3)}
            for ov in keys}


def ramped_production(controller):
    targets = getattr(controller, "ramped_targets", None)
    return float(targets["production_rate"]) if targets else float(controller._ramped_production)


def setpoint_table(point):
    current = point.controller.setpoints
    rate = float(point.controller.registry.nominal.production_sp_rate_limit)
    changed_at = {}
    for change in point.timeline:
        if change.get("op") == "setpoint":
            changed_at[change["id"]] = change["step"] * STEP_HOURS
    table = {}
    for name in adjustable_setpoints(point.controller):
        label, unit, _, low, high = SETPOINTS[name]
        value, initial = float(getattr(current, name)), float(getattr(point.base_setpoints, name))
        row = {"unit": unit, "setpoint": round(value, 4), "initial": round(initial, 4), "limits": [low, high],
               "change_vs_initial": f"{(value / initial - 1) * 100:+.1f} %" if initial else "n/a",
               "last_changed": (f"{point.time_hours - changed_at[name]:.2f} h ago" if name in changed_at
                                else "not changed in this run")}
        if name in PV_TAGS and (measured := latest_value(point, f"XMEAS({PV_TAGS[name]})")) is not None:
            row["measured_now"] = round(measured, 4)
        if name == "production_rate":
            remaining = value - ramped_production(point.controller)
            row["status"] = (f"STILL RAMPING: the last change has not taken full effect; {abs(remaining):.3g} {unit} "
                             f"still to go, about {abs(remaining) / rate:.1f} h left"
                             if abs(remaining) > 1e-3 * max(1.0, abs(value)) else "settled: the setpoint has been reached")
        else:
            row["status"] = "acts at the next control step (not ramped)"
        table[name] = row
    return table


def symptoms(point):
    """Observable symptoms that select conditional SOP rules (published sensors, commands and alarms only)."""
    times = [s.time_hours for s in point.history if s.time_hours >= point.history[-1].time_hours - TREND_WINDOW_HOURS]
    pressure = [s.values[6] for s in point.history if s.time_hours >= point.history[-1].time_hours - TREND_WINDOW_HOURS]
    p_now, dp = latest_value(point, "XMEAS(7)"), _slope(times, pressure)
    pressure_alarm = any(a.get("tag") == "XMEAS(7)" for a in point.alarms)
    pressure_override = "high_pressure_to_production" in point.history[-1].protection
    a_flow, a_cmd = latest_value(point, "XMEAS(1)"), point.history[-1].commands[2]
    a_saturated = np.mean([s.commands[2] >= 100 - 1e-6 for s in point.history]) > 0.5 or a_cmd >= 100 - 1e-6
    production = point.controller.setpoints.production_rate
    return {
        "PRESS-1": bool((p_now is not None and p_now > PRESS1_WATCH_KPA and dp > 0) or pressure_alarm or pressure_override),
        "FEEDA-1": bool(a_flow is not None and a_flow < 0.05 and a_saturated),
        "RESTORE-1": bool(production < point.base_setpoints.production_rate - 1e-6),
    }


def retrieve_rules(point):
    """General rules always, symptom rules when their symptom shows, plus the alarm SOP's checks for each active alarm."""
    found = symptoms(point)
    rules = [{"id": r["id"], "title": r["title"], "text": r["text"]}
             for r in RULEBOOK["rules"] if r.get("always") or found.get(r["id"])]
    seen = set()
    for alarm in point.alarms:
        sop = match_sensor_sop(alarm) or find_sop(alarm)
        if sop and sop["id"] not in seen:
            seen.add(sop["id"])
            rules.append({"id": sop["id"], "title": sop["title"], "text": " ".join(sop.get("checks", ()))})
    return rules


def alarm_info(point):
    """Every active alarm described for the agent: which sensor, what problem, by how much, for how long.

    Taken from the alarm monitor's observed channel (live limits and enabled condition rules, one format), the
    same engine that scores branches on true values. Whether an alarm came from a sensor test is not said:
    telling them apart is part of the judgement.
    """
    return point.monitor.describe_active("observed")


def decision_state(point, rules):
    return {
        "situation": SITUATION,
        "setpoints": setpoint_table(point),
        "plant": {
            "time_h": round(point.time_hours, 4),
            "history_window_h": [point.history[0].time_hours, point.history[-1].time_hours],
            "sensors": sensor_summary(point),
            "mv_commands": command_summary(point),
            "protection": protection_summary(point),
            "alarms_active": alarm_info(point),
            "recent_setpoint_changes": [
                {"setpoint": c["id"], "value": c["value"], "time_h": round(c["step"] * STEP_HOURS, 4)}
                for c in point.timeline if c.get("op") == "setpoint"][-5:],
        },
        "operating_procedures": rules,
    }


def gate_questions(table):
    return {name: {"type": "noul", "instructions": f"Does the {name} setpoint (now {row['setpoint']} {row['unit']}) "
                                                   "need to be changed now?"}
            for name, row in table.items()}


def move_questions(table, selected):
    questions, moves = {}, {}
    for name in selected:
        row = table[name]
        steps = CATEGORY_STEPS.get(name, DEFAULT_STEPS)
        low, high = row["limits"]
        criteria, keymap = {}, {}
        for key, direction, size, words in CATEGORIES:
            move = 0.0 if size is None else direction * steps[size]
            value = row["setpoint"] * (1 + move)
            if not low <= value <= high:
                continue
            criteria[key] = words if move == 0 else f"{words} ({move * 100:+.0f} %, to {value:.4g} {row['unit']})"
            keymap[key] = move
        questions[name] = {"type": "choice", "criteria": criteria,
                           "instructions": f"How should the {name} setpoint (now {row['setpoint']} {row['unit']}) move?"}
        moves[name] = keymap
    return questions, moves


def local_rule(point, table):
    """Deterministic fallback: cut production by a fraction of its initial value under observable stress, once
    the previous cut has settled, never below the configured floor (`local_rule` in agent-config.json)."""
    times = [s.time_hours for s in point.history if s.time_hours >= point.history[-1].time_hours - TREND_WINDOW_HOURS]
    pressure = [s.values[6] for s in point.history if s.time_hours >= point.history[-1].time_hours - TREND_WINDOW_HOURS]
    p_now = latest_value(point, "XMEAS(7)")
    stressed = (p_now is not None and p_now > CONFIG["pressure_watch_kpa"] and _slope(times, pressure) > 0) or any(
        a.get("kind") == "warning" for a in point.alarms) or bool(point.history[-1].protection) or any(
        row["frac_at_upper"] >= CONFIG["saturation_fraction"] or row["frac_at_lower"] >= CONFIG["saturation_fraction"]
        for row in command_summary(point) if not row["held_constant"])
    production = table["production_rate"]
    settled = production["status"].startswith("settled")
    floor = CONFIG["floor_fraction"] * production["initial"]
    if stressed and settled and production["setpoint"] > floor + 1e-6:
        value = max(floor, production["setpoint"] - CONFIG["cut_fraction"] * production["initial"])
        return [{"setpoint": "production_rate", "from": production["setpoint"], "to": round(value, 6),
                 "move": round(value / production["setpoint"] - 1, 6)}]
    return []


def decide(point, *, timeout, checkpoint, request_id):
    started = perf_counter()
    rules = retrieve_rules(point)
    state = decision_state(point, rules)
    table = state["setpoints"]
    result = {"request_id": request_id, "retrieved_rules": [r["id"] for r in rules],
              "gate": None, "moves": {}, "reason": None, "rag_status": "no_active_alarms"}
    rag_result = None
    changes = []
    try:
        if point.alarms:
            result["rag_status"] = "error"
            rag_timeout = float(os.environ.get("PROTOPILOT_RAG_TIMEOUT_SECONDS", "150"))
            rag_result = zoro_inference.retrieve_alarm(point, checkpoint, timeout=rag_timeout)
            state["rag"] = zoro_inference.evidence_for_zoro(rag_result)
            result["rag_status"] = rag_result["status"]
            result["rag_event_id"] = rag_result["event_id"]
        if result["rag_status"] == "insufficient_evidence":
            result["reason"] = "insufficient_rag_evidence"
        elif result["rag_status"] == "error":
            result["reason"] = "rag_error"
        else:
            questions, moves = move_questions(table, table)
            changes = zoro_inference.decide_pair(
                state, gate_questions(table), questions, moves, threshold=NEED_THRESHOLD,
                timeout=timeout, request_id=request_id, result=result,
            )
    except (OSError, SubprocessError, AttributeError, KeyError, TypeError, ValueError) as error:
        if result["rag_status"] == "error":
            result["reason"] = f"rag_error:{type(error).__name__}"
        else:
            result["reason"] = type(error).__name__ if os.environ.get("ZORO_BASE_URL") else "not_configured"
    return {**result, "model": "Zoro-1", "fallback": False, "changes": changes,
            "status": "ready" if changes else "human_review",
            "decision_ms": round((perf_counter() - started) * 1000, 2)}, state, rag_result


def setpoint_alternatives(table):
    _, moves = move_questions(table, table)
    return [
        {"id": f"{name}:{category}", "setpoint": name, "from": row["setpoint"],
         "to": round(row["setpoint"] * (1 + move), 6), "move": move, "unit": row["unit"]}
        for name, row in table.items()
        for category, move in moves[name].items() if move
    ]


def run_setpoint_branch(point, label, changes):
    sim = TennesseeEastmanProcess()
    sim.restore(point.native)
    controller = deepcopy(point.controller)
    measurements = point.measurements.copy()
    reference = float(controller.setpoints.production_rate)  # same loss reference for every branch
    for change in changes:
        value = adjustment({"op": "setpoint", "id": change["setpoint"], "value": change["to"]})["value"]
        controller.setpoints = replace(controller.setpoints, **{change["setpoint"]: value})
    rows, protected_steps, shutdown, cost = [measurements], 0, None, 0.0
    monitor = point.monitor.copy()
    try:
        for _ in range(round(BRANCH_HOURS / STEP_HOURS)):
            action, control, _ = control_action(controller, measurements, sim.time, point.manual)
            result = sim.advance(action, control_interval=STEP_HOURS, disturbances=point.idv)
            measurements = result.measurements
            rows.append(measurements)
            protected_steps += int(any(control.overrides_active.values()))
            cost += step_cost(result)
            monitor.update(sim.time, measurements, point.offsets, controller.setpoints, STEP_HOURS)
            if result.shutdown_status["terminated"]:
                shutdown = result.shutdown_status
                break
    except (RuntimeError, ValueError):
        return {"id": label, "error": "이 조건에서 분기를 계산할 수 없습니다."}
    finally:
        sim.kernel.close()
    summary = branch_summary(np.asarray(rows), protected_steps, shutdown, cost=cost, reference=reference,
                             horizon_hours=BRANCH_HOURS, alarm=monitor.summary())
    return {"id": label, "changes": changes, **summary,
            "production_target": float(controller.setpoints.production_rate)}


def alternative_evaluation_state(state):
    rag = state.get("rag")
    if rag is None:
        return state

    plant = state["plant"]
    measured_tags = {f"XMEAS({number})" for number in PV_TAGS.values()}
    measured_tags.update(alarm["tag"] for alarm in plant["alarms_active"])
    sensor_names = {variable.name for tag, variable in zip(MEASUREMENT_TAGS, TEP_SCHEMA.measurements)
                    if tag in measured_tags}
    evidence = {
        "event_id": rag["event_id"],
        "evidence": [
            {key: item[key] for key in ("evidence_id", "statement", "source_document", "reranker_score")}
            for item in rag["evidence"]
        ],
        "sop_plans": [
            {"evidence_id": action["evidence_id"], "status": action["status"],
             "parameter": action["plan"].get("parameter")}
            for action in rag["candidate_actions"]
        ],
    }
    return {
        **state,
        "plant": {**plant, "sensors": [row for row in plant["sensors"] if row["name"] in sensor_names]},
        "operating_procedures": [
            *state["operating_procedures"],
            {"id": rag["event_id"], "title": "Alarm RAG evidence", "rag": evidence},
        ],
    }


def analyze_setpoints(point, trace_path, *, timeout=None, evaluate_alternatives=None):
    timeout = timeout or float(os.environ.get("ZORO_SETPOINT_TIMEOUT", CONFIG["zoro_timeout_seconds"]))
    decision_id = uuid4().hex
    checkpoint = sha256(point.native.model_bytes).hexdigest()[:12]
    decision, state, rag_result = decide(point, timeout=timeout, checkpoint=checkpoint, request_id=decision_id)
    rule_changes = local_rule(point, state["setpoints"])
    candidates = [("hold", []), ("rule", rule_changes)]
    if decision["status"] == "ready":
        candidates.append(("zoro", decision["changes"]))
    branches = [run_setpoint_branch(point, label, changes) for label, changes in candidates]
    result = {
        "decision_id": decision_id, "decision": decision, "branches": branches,
        "start": {"checkpoint": checkpoint, "run_id": point.run_id,
                  "step": point.step, "time_hours": round(point.time_hours, 8), "horizon_hours": BRANCH_HOURS},
    }
    result["handoff"] = {
        "checkpoint": result["start"],
        "baseline_changes": decision["changes"],
        "alternatives": setpoint_alternatives(state["setpoints"]),
        "evaluation_policy": evaluation_policy(),
        "rag_result": rag_result,
    }
    if evaluate_alternatives is not None:
        if decision["status"] == "ready":
            result["feature2"] = evaluate_alternatives(
                point, result["handoff"], alternative_evaluation_state(state), timeout=timeout,
            )
        else:
            result["feature2"] = {"status": "human_review", "reason": "feature1_pair_unavailable",
                                  "evaluated": [], "recommended": None}
    trace = {
        **result, "time_utc": timestamp(), "flow": "live_setpoint_decision", "kernel_revision": KERNEL_REVISION,
        "rulebook": RULEBOOK["name"], "setpoint_table": state["setpoints"],
        "alarms_active": state["plant"]["alarms_active"],
        "simulation_context": {
            "mode": "mode1", "seed": point.seed, "step_hours": STEP_HOURS,
            "setpoints": asdict(point.controller.setpoints),
            "manual_overrides": {f"XMV({i + 1})": value for i, value in point.manual.items()},
            "evaluation_faults": [f"IDV({i + 1})" for i in np.flatnonzero(point.idv)],
        },
    }
    result["logged"] = append_trace(trace_path, trace)
    return result
