"""Tunable numbers for the agent flows, read once from `agent-config.json` (or `PROTOPILOT_AGENT_CONFIG`).

Modules copy what they need into their own constants at import, so a test can still patch
`server.setpoint_decision.BRANCH_HOURS` and the like. Values are checked here so a bad file fails at start-up.
"""

import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("agent-config.json")


def _positive(value, where):
    if type(value) not in (int, float) or not value > 0:
        raise ValueError(f"agent config {where}: a positive number is required, got {value!r}")
    return float(value)


def _fraction(value, where):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError(f"agent config {where}: a number in [0, 1] is required, got {value!r}")
    return float(value)


def _steps(value, where):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"agent config {where}: [small, large] is required, got {value!r}")
    small, large = (_fraction(v, where) for v in value)
    if not 0 < small < large:
        raise ValueError(f"agent config {where}: 0 < small < large is required, got {value!r}")
    return small, large


def load(path=None):
    path = Path(path or os.environ.get("PROTOPILOT_AGENT_CONFIG") or DEFAULT_PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    history, decision = raw["history"], raw["setpoint_decision"]
    rule, rollout = raw["local_rule"], raw["rollout"]
    baselines = rollout["baselines"]
    if not isinstance(baselines, list) or not set(baselines) <= {"hold", "rule"} or not baselines:
        raise ValueError(f"agent config rollout.baselines: a non-empty subset of hold, rule; got {baselines!r}")
    return {
        "history_sample_hours": _positive(history["sample_hours"], "history.sample_hours"),
        "history_window_hours": _positive(history["window_hours"], "history.window_hours"),
        "trend_window_hours": _positive(history["trend_window_hours"], "history.trend_window_hours"),
        "need_threshold": _fraction(decision["need_threshold"], "setpoint_decision.need_threshold"),
        "branch_hours": _positive(decision["branch_hours"], "setpoint_decision.branch_hours"),
        "zoro_timeout_seconds": _positive(decision["zoro_timeout_seconds"], "setpoint_decision.zoro_timeout_seconds"),
        "category_steps": {name: _steps(v, f"setpoint_decision.category_steps.{name}")
                           for name, v in decision["category_steps"].items()},
        "default_category_steps": _steps(decision["default_category_steps"], "setpoint_decision.default_category_steps"),
        "rulebook_path": path.parent / decision["rulebook"],
        "alarm_rules_path": path.parent / decision["alarm_rules"],
        "cut_fraction": _fraction(rule["cut_fraction_of_initial"], "local_rule.cut_fraction_of_initial"),
        "floor_fraction": _fraction(rule["floor_fraction_of_initial"], "local_rule.floor_fraction_of_initial"),
        "pressure_watch_kpa": _positive(rule["pressure_watch_kpa"], "local_rule.pressure_watch_kpa"),
        "saturation_fraction": _fraction(rule["saturation_fraction"], "local_rule.saturation_fraction"),
        "baselines": tuple(baselines),
        "rule_interval_hours": _positive(rollout["rule_interval_hours"], "rollout.rule_interval_hours"),
        "alarm_branch_hours": _positive(raw["alarm_response"]["branch_hours"], "alarm_response.branch_hours"),
        "source": str(path),
    }


CONFIG = load()
