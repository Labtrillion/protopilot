"""Align a live alarm with tep-sops SOPs by evaluating the SOPs' own observation predicates.

Input  : the alarm event, the observed history kept by Plant (time_hours, {tag: value}) and the
         currently active alarms. The injected IDV vector is never read.
Output : the sensor-alarm SOP matching the alarm's tag/side/severity (or a reported gap) and a
         ranking of IDV SOPs with the predicates that held, were false, or could not be evaluated.

Scoring (designed on dev seeds, frozen before the held-out seed; see docs/SOP_ALIGNMENT.md):
  * a condition "holds" when >= HOLD_FRACTION of the samples in the last PERSIST_HOURS violate it
    (noise on a narrow nominal envelope alone does not trigger it);
  * a rule holds by its own all/any logic;
  * a held rule scores SPECIFIC_WEIGHT if it contains an envelope, valve-position or non-alarm-tag
    condition, otherwise LIMIT_WEIGHT (rules that only restate the Downs & Vogel operating limits);
  * -TYPE_WEIGHT when the history shape of the SOP's first envelope signal contradicts its stated
    disturbance type (step-like: shift dominates spread; random: spread dominates shift);
  * + MECHANISM_WEIGHT x fraction of the SOP's mechanism signals outside their live envelope;
  * + a capped excess term as a tie-breaker.
An IDV SOP is selected only if it has a held specific rule and a strictly higher score than the next.
"""

import json
import math
from pathlib import Path

from .catalog import OPERATING_LIMITS

INDEX = json.loads(Path(__file__).with_name("sop-index.json").read_text(encoding="utf-8"))
ALARM_TAGS = frozenset(("XMEAS(7)", "XMEAS(8)", "XMEAS(9)", "XMEAS(12)", "XMEAS(15)"))
PERSIST_HOURS = 0.25
WINDOW_HOURS = 1.0
HOLD_FRACTION = 0.5
SPECIFIC_WEIGHT = 2.0
LIMIT_WEIGHT = 1.0
TYPE_WEIGHT = 1.0
MECHANISM_WEIGHT = 1.0  # [ADDED v4] x fraction of the SOP mechanism signals outside their live envelope
# [CHANGED v4] "sticking" removed: valve stiction produces oscillation, so it has no step/random shape.
STEP_TYPES = frozenset(("step", "slow drift"))
RANDOM_TYPES = frozenset(("random variation",))


# [ADDED] Signals derived from observed tags (used by conditions added in scripts/build_sop_index.py).
DERIVED = {"XMEAS(7)-XMEAS(13)": ("XMEAS(7)", "XMEAS(13)")}


def observed_value(observed, tag):
    if tag in DERIVED:
        left, right = (observed.get(t) for t in DERIVED[tag])
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left - right
        return None
    return observed.get(tag)


def _values(history, tag, since):
    out = []
    for time_hours, observed in history:
        value = observed_value(observed, tag)
        if time_hours >= since and isinstance(value, (int, float)) and math.isfinite(value):
            out.append(float(value))
    return out


def _envelope(condition):
    """[CHANGED] Prefer the envelope re-measured on this Plant (scripts/calibrate_envelopes.py);
    fall back to the SOP's own measured envelope when no live calibration exists for the tag."""
    live = INDEX.get("live_envelopes", {}).get("by_tag", {}).get(condition["tag"])
    if live:
        return live["minimum"], live["maximum"]
    if condition["minimum"] is None:  # [ADDED] added condition without a live calibration: cannot evaluate
        raise KeyError(f"no envelope for {condition['tag']}")
    return condition["minimum"], condition["maximum"]


def _violation(condition, value):
    """Return (violated, normalized excess) for one sample."""
    if condition["kind"] == "envelope":
        low, high = _envelope(condition)
        half = max((high - low) / 2, 1e-9)
        op = condition["operator"]
        above = value - high if op in ("outside_reference_range", "greater_than_reference_maximum") else 0.0
        below = low - value if op in ("outside_reference_range", "less_than_reference_minimum") else 0.0
        excess = max(above, below, 0.0) / half
        return excess > 0, excess
    limit, op = condition["value"], condition["operator"]
    violated = {"gt": value > limit, "gte": value >= limit, "lt": value < limit, "lte": value <= limit}[op]
    return violated, 1.0 if violated else 0.0


def _condition_state(condition, history, now):
    values = _values(history, condition["tag"], now - PERSIST_HOURS)
    if not values:
        return {"tag": condition["tag"], "status": "missing"}
    checks = [_violation(condition, v) for v in values]
    fraction = sum(v for v, _ in checks) / len(checks)
    excess = sum(e for _, e in checks) / len(checks)
    return {"tag": condition["tag"], "operator": condition["operator"],
            "status": "holds" if fraction >= HOLD_FRACTION else "false",
            "fraction": round(fraction, 3), "excess": round(excess, 3), "latest": values[-1]}


def _in_limit_alarm(tag, observed):
    """True when tag is beyond its Downs & Vogel operating limit, i.e. the alarm already reports it."""
    low, high = OPERATING_LIMITS.get(tag, (None, None))
    value = observed.get(tag)
    if not isinstance(value, (int, float)):
        return False
    return (low is not None and value < low) or (high is not None and value > high)


def _specific(rule, observed=None):
    """[CHANGED v3] A rule is specific evidence only through a condition that the active operating-limit
    alarm does not already imply: an envelope/threshold on another signal, a valve position, or an
    envelope on an alarm tag while that tag is still inside its limits. (v2 counted e.g. IDV-19's
    XMEAS(15) envelope as independent evidence during a XMEAS(15) low alarm.)"""
    observed = observed or {}
    for c in rule["conditions"]:
        if c["tag"].endswith(":actual") or c["tag"] not in ALARM_TAGS:
            return True
        if c["kind"] == "envelope" and not _in_limit_alarm(c["tag"], observed):
            return True
    return False


def _type_check(entry, history, now):
    kind = entry["disturbance_type"]
    envelope = next((c for r in entry["rules"] for c in r["conditions"] if c["kind"] == "envelope"), None)
    if envelope is None or kind not in STEP_TYPES | RANDOM_TYPES:
        return None
    values = _values(history, envelope["tag"], now - WINDOW_HOURS)
    if len(values) < 5:
        return None
    low, high = _envelope(envelope)  # [CHANGED] same envelope source as the predicates
    half = max((high - low) / 2, 1e-9)
    center = (high + low) / 2
    mean = sum(values) / len(values)
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) / half
    shift = abs(mean - center) / half
    shape = "step" if shift > spread else "random"
    return {"signal": envelope["tag"], "shift": round(shift, 3), "spread": round(spread, 3), "shape": shape,
            "consistent": (shape == "step") == (kind in STEP_TYPES)}


def _mechanism_signature(entry, history, now):
    """[ADDED v4] Fraction of the SOP's own mechanism signals that persistently leave their live
    fault-free envelope. Signals without a live envelope are not scored."""
    by_tag = INDEX.get("live_envelopes", {}).get("by_tag", {})
    deviating, scored = [], 0
    for tag in entry["mechanism_tags"]:
        env = by_tag.get(tag)
        values = _values(history, tag, now - PERSIST_HOURS)
        if not env or not values:
            continue
        scored += 1
        outside = sum(v < env["minimum"] or v > env["maximum"] for v in values) / len(values)
        if outside >= HOLD_FRACTION:
            deviating.append(tag)
    if not scored:
        return None
    return {"fraction": round(len(deviating) / scored, 3), "deviating": deviating, "scored": scored}


def rank_idv(history):
    if not history:
        return []
    now = history[-1][0]
    current = history[-1][1]
    ranking = []
    for entry in INDEX["idv"]:
        score, held, rules = 0.0, [], []
        excess = 0.0
        for rule in entry["rules"]:
            states = [_condition_state(c, history, now) for c in rule["conditions"]]
            known = [s for s in states if s["status"] != "missing"]
            if rule["logic"] == "all":
                holds = bool(known) and len(known) == len(states) and all(s["status"] == "holds" for s in states)
            else:
                holds = any(s["status"] == "holds" for s in known)
            specific = _specific(rule, current)
            weight = SPECIFIC_WEIGHT if specific else LIMIT_WEIGHT
            if holds:
                score += weight
                held.append(rule["rule_id"])
                excess += sum(s.get("excess", 0.0) for s in known)
            rules.append({"rule_id": rule["rule_id"], "holds": holds, "specific": specific,
                          "conditions": states})
        mechanism = _mechanism_signature(entry, history, now) if held else None
        if mechanism:
            score += MECHANISM_WEIGHT * mechanism["fraction"]
        type_check = _type_check(entry, history, now) if held else None
        # [CHANGED v3] penalty for a contradicted disturbance type instead of a bonus for a matching one,
        # so an SOP with an unstated type ("Unknown") is not ranked below an identical-predicate SOP.
        if type_check and not type_check["consistent"]:
            score -= TYPE_WEIGHT
        score += min(excess, 10.0) / 100.0
        ranking.append({"id": entry["id"], "protocol_id": entry["protocol_id"], "title": entry["title"],
                        "score": round(score, 4), "held_rules": held,
                        "specific_rule_held": any(r["holds"] and r["specific"] for r in rules),
                        "type_check": type_check, "mechanism": mechanism, "rules": rules})
    ranking.sort(key=lambda r: (-r["score"], r["id"]))
    return ranking


def match_sensor_sop(alarm):
    """Sensor SOP whose tag, side and severity band contain the alarm value."""
    tag, side, value = alarm.get("tag"), alarm.get("side"), alarm.get("value")
    if not isinstance(value, (int, float)):
        return None
    best = None
    for sop in INDEX["sensor"]:
        if tag not in sop["tags"] or sop.get("side") != side:
            continue
        threshold = sop["activation_threshold"]
        inside = value >= threshold if side == "high" else value <= threshold
        if inside and (best is None or (threshold > best["activation_threshold"] if side == "high"
                                        else threshold < best["activation_threshold"])):
            best = sop  # the most severe band the value has reached (H vs HH, L vs LL)
    return best


def align(alarm, history):
    sensor = match_sensor_sop(alarm) if alarm.get("kind") != "trip" else None
    ranking = rank_idv(history)
    top = ranking[0] if ranking else None
    runner = ranking[1] if len(ranking) > 1 else None
    confident = bool(top and top["specific_rule_held"] and (runner is None or top["score"] > runner["score"]))
    tied = [r["protocol_id"] for r in ranking if top and r["score"] == top["score"] and r["specific_rule_held"]]
    return {
        "sensor_sop": sensor["id"] if sensor else None,
        "sensor_gap": None if sensor or alarm.get("kind") == "trip" else
        f"tep-sops has no sensor SOP for {alarm.get('tag')} {alarm.get('side')}",
        "idv_selected": top["protocol_id"] if confident else None,
        "idv_tied": tied if not confident and len(tied) > 1 else [],
        "ranking": [{k: r[k] for k in ("protocol_id", "score", "held_rules", "type_check", "mechanism")}
                    for r in ranking[:5]],
        "top_detail": top["rules"] if top else [],
        "history_hours": round(history[-1][0] - history[0][0], 4) if history else 0.0,
        "index_commit": INDEX["source"]["commit"],
    }


def compact_for(alignment):
    """The compact record to send to Zoro: confident IDV SOP, else the matched sensor SOP."""
    if alignment["idv_selected"]:
        return next(e["compact"] for e in INDEX["idv"] if e["protocol_id"] == alignment["idv_selected"])
    if alignment["sensor_sop"]:
        return next(s for s in INDEX["sensor"] if s["id"] == alignment["sensor_sop"])
    return None
