"""One alarm engine for the agent and for scoring: an `AlarmMonitor` updated at every control step.

Each monitor evaluates the same rules on two channels at once:

* `observed` -- what the operator sees (sensor offsets included); this is what the agent is told;
* `true` -- the plant's own values; this is what branch and rollout `alarm_minutes` score.

Rules are the live alarm limits (`plant.limits`, Downs & Vogel Table 6 by default) as threshold rules, plus the
enabled condition rules of `alarm-rules.json`. The console's own alarm list (`Plant.check_alarms`) is separate
and unchanged. A condition rule:

    {"id": "P-RISE", "enabled": true, "tag": "XMEAS(7)", "label": "...",
     "condition": {"type": "rate", "above": 30}, "for_minutes": 15}

`for_minutes` (optional) requires the condition continuously for that long. Condition types are registered with
`@condition("name")`: a function of (rule, condition, view) returning None when clear, else a dict describing the
problem (`problem`, `value`, optional `limit`). A view gives `latest(tag)`, `trend(tag)` (units per hour, least
squares over `trend_window_hours` of 3-minute samples) and `setpoints`.
"""

import json
from collections import deque
from copy import deepcopy
from pathlib import Path

import numpy as np
from tep_studio import TEP_SCHEMA

from .agent_config import CONFIG
from .catalog import CHANNELS, MEASUREMENT_TAGS, PV_TAGS

RULES = json.loads(Path(CONFIG["alarm_rules_path"]).read_text(encoding="utf-8"))["rules"]
CONDITIONS = {}
CHANNEL_NAMES = ("observed", "true")
SENSOR_NAMES = {tag: var.name for tag, var in zip(MEASUREMENT_TAGS, TEP_SCHEMA.measurements)}
SENSOR_UNITS = {tag: var.unit for tag, var in zip(MEASUREMENT_TAGS, TEP_SCHEMA.measurements)}
SETPOINT_OF = {f"XMEAS({index})": name for name, index in PV_TAGS.items()}
INDEX = {tag: i for i, tag in enumerate(MEASUREMENT_TAGS)}


def condition(name):
    def register(function):
        CONDITIONS[name] = function
        return function
    return register


class View:
    """One channel at one instant: the current values plus that channel's recent 3-minute samples."""

    def __init__(self, values, samples, setpoints, trend_window_hours):
        self.values, self.samples, self.setpoints = values, samples, setpoints
        self.trend_window_hours = trend_window_hours

    def latest(self, tag):
        value = float(self.values[INDEX[tag]])
        return value if np.isfinite(value) else None

    def trend(self, tag):
        if not self.samples:
            return 0.0
        now = self.samples[-1][0]
        rows = [(t, v[INDEX[tag]]) for t, v in self.samples if t >= now - self.trend_window_hours - 1e-9]
        times = np.array([t for t, v in rows if np.isfinite(v)])
        values = np.array([v for t, v in rows if np.isfinite(v)])
        if len(times) < 2:
            return 0.0
        centered = times - times.mean()
        return float(np.dot(centered, values - values.mean()) / np.dot(centered, centered))


@condition("threshold")
def _threshold(rule, cond, view):
    value = view.latest(rule["tag"])
    if value is None:
        return None
    if "above" in cond and value > cond["above"]:
        return {"problem": "above upper limit", "value": value, "limit": cond["above"]}
    if "below" in cond and value < cond["below"]:
        return {"problem": "below lower limit", "value": value, "limit": cond["below"]}
    return None


@condition("rate")
def _rate(rule, cond, view):
    rate = view.trend(rule["tag"])
    if "above" in cond and rate > cond["above"]:
        return {"problem": "rising faster than allowed", "value": rate, "limit": cond["above"], "value_unit": "per h"}
    if "below" in cond and rate < cond["below"]:
        return {"problem": "falling faster than allowed", "value": rate, "limit": cond["below"], "value_unit": "per h"}
    return None


@condition("deviation")
def _deviation(rule, cond, view):
    name = cond.get("setpoint") or SETPOINT_OF.get(rule["tag"])
    value = view.latest(rule["tag"])
    if name is None or value is None:
        return None
    target = float(getattr(view.setpoints, name))
    if abs(value - target) > cond["max_abs"]:
        return {"problem": "away from its setpoint", "value": value, "limit": cond["max_abs"],
                "setpoint": target, "deviation": value - target}
    return None


@condition("all")
def _all(rule, cond, view):
    parts = []
    for part in cond["conditions"]:
        tag = part.get("tag", rule.get("tag"))
        hit = CONDITIONS[part["type"]]({**rule, "tag": tag}, part, view)
        if hit is None:
            return None
        parts.append({"tag": tag, **hit})
    return {"problem": "all conditions met", "value": None, "parts": parts}


def limit_rules(limits):
    """The live alarm limits as threshold rules (only the 41 published sensors are monitored)."""
    rules = []
    for tag, (low, high) in limits.items():
        if tag not in INDEX:
            continue
        for side, bound, key in (("high", high, "above"), ("low", low, "below")):
            if bound is not None:
                rules.append({"id": f"LIMIT {tag} {side}", "tag": tag, "origin": "operating limit",
                              "condition": {"type": "threshold", key: bound}})
    return rules


class AlarmMonitor:
    """Stateful alarm worker: call `update` once per control step; read `active`, `summary`, `describe_active`."""

    def __init__(self, limits, rules=None, *, sample_hours=CONFIG["history_sample_hours"],
                 trend_window_hours=CONFIG["trend_window_hours"]):
        self.sample_hours, self.trend_window_hours = sample_hours, trend_window_hours
        self.condition_rules = [dict(r, origin=f"condition rule {r['id']}")
                                for r in (RULES if rules is None else rules) if r.get("enabled", True)]
        self.set_limits(limits)
        self.samples = {name: deque() for name in CHANNEL_NAMES}
        self.since = {name: {} for name in CHANNEL_NAMES}  # rule id -> time the condition started holding
        self.active = {name: {} for name in CHANNEL_NAMES}  # rule id -> hit (with "since_hours")
        self.time_hours = None
        self.reset_counters()

    def set_limits(self, limits):
        self.rules = limit_rules(limits) + self.condition_rules

    def reset_counters(self):
        """Start the minute counters from zero (a branch or rollout scores only its own span)."""
        self.alarm_minutes = {name: 0.0 for name in CHANNEL_NAMES}
        self.rule_minutes = {name: {} for name in CHANNEL_NAMES}

    def copy(self, *, keep_counters=False):
        twin = deepcopy(self)
        if not keep_counters:
            twin.reset_counters()
        return twin

    def update(self, time_hours, true_values, offsets, setpoints, dt_hours):
        observed = np.array(true_values, dtype=float)
        for tag, offset in offsets.items():
            if tag in INDEX:
                observed[INDEX[tag]] += offset
        self.time_hours = float(time_hours)
        for name, values in (("observed", observed), ("true", np.asarray(true_values, dtype=float))):
            samples = self.samples[name]
            if not samples or time_hours - samples[-1][0] >= self.sample_hours - 1e-9:
                samples.append((float(time_hours), values.copy()))
                while time_hours - samples[0][0] > self.trend_window_hours + self.sample_hours + 1e-9:
                    samples.popleft()
            view = View(values, samples, setpoints, self.trend_window_hours)
            since, active = self.since[name], {}
            for rule in self.rules:
                hit = CONDITIONS[rule["condition"]["type"]](rule, rule["condition"], view)
                if hit is None:
                    since.pop(rule["id"], None)
                    continue
                start = since.setdefault(rule["id"], float(time_hours))
                if time_hours - start >= float(rule.get("for_minutes", 0)) / 60 - 1e-9:
                    active[rule["id"]] = {**hit, "since_hours": start, "rule": rule, "trend": view.trend(rule["tag"])
                                          if rule.get("tag") in INDEX else None}
            self.active[name] = active
            if active:
                self.alarm_minutes[name] += dt_hours * 60
                for rule_id in active:
                    self.rule_minutes[name][rule_id] = self.rule_minutes[name].get(rule_id, 0.0) + dt_hours * 60

    def summary(self):
        return {"alarm_minutes": round(self.alarm_minutes["true"], 6),
                "observed_alarm_minutes": round(self.alarm_minutes["observed"], 6),
                "alarm_minutes_by_rule": {k: round(v, 4) for k, v in self.rule_minutes["true"].items()},
                "observed_alarm_minutes_by_rule": {k: round(v, 4) for k, v in self.rule_minutes["observed"].items()}}

    def describe_active(self, channel="observed"):
        """Active alarms as the agent reads them: which sensor, what problem, by how much, for how long."""
        return [describe(hit["rule"], hit, self.time_hours) for hit in self.active[channel].values()]


def describe(rule, hit, now_hours):
    tag = rule.get("tag")
    entry = {"origin": "operating limit (Downs & Vogel Table 6)" if rule["origin"] == "operating limit" else rule["origin"],
             "tag": tag, "sensor": SENSOR_NAMES.get(tag), "label": rule.get("label") or CHANNELS.get(tag, {}).get("label"),
             "unit": SENSOR_UNITS.get(tag, CHANNELS.get(tag, {}).get("unit"))}
    entry.update({k: v for k, v in hit.items() if k not in {"rule", "since_hours", "trend"} and v is not None})
    if isinstance(entry.get("value"), float) and isinstance(hit.get("limit"), (int, float)) and "value_unit" not in hit:
        entry["exceeded_by"] = round(abs(entry["value"] - hit["limit"]), 4)
    entry["active_for_minutes"] = round((now_hours - hit["since_hours"]) * 60, 1)
    if hit.get("trend") is not None:
        entry["trend_per_h"] = round(hit["trend"], 4)
    if tag in SETPOINT_OF:
        entry["related_setpoint"] = SETPOINT_OF[tag]
    for key in ("value", "limit", "deviation", "setpoint"):
        if isinstance(entry.get(key), float):
            entry[key] = round(entry[key], 4)
    return entry
