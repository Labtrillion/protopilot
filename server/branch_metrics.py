"""Meter simulated branches against a common evaluation horizon."""

import numpy as np

from .catalog import OPERATING_LIMITS
from .plant import finite_metrics
from .scenario import STEP_HOURS

FINAL_WINDOW_HOURS = 1


def step_cost(result):
    """TEP's internal operating cost ($/h) over one control step, as MetricsAccumulator integrates it."""
    return float(result.objective_terms.get("operating_cost_internal_per_hour", 0.0)) * STEP_HOURS


def branch_summary(matrix, protected_steps, shutdown, *, cost=0.0, reference=None, horizon_hours=None, alarm=None):
    """Outcome of one branch: `matrix` is the starting measurements followed by one row per control step.

    Production loss = `reference` target x the full horizon - produced volume, so a branch that trips
    early is charged for the rest of the horizon. `alarm` is the branch's AlarmMonitor summary (minutes on
    true values, plus what the operator would have seen).
    """
    produced = float(np.sum(matrix[1:, 16]) * STEP_HOURS)
    horizon = horizon_hours if horizon_hours is not None else (len(matrix) - 1) * STEP_HOURS
    return finite_metrics({
        **_base_summary(matrix, protected_steps, shutdown),
        **(alarm or {}),
        "operating_cost": float(cost),
        "produced_m3": produced,
        "production_loss_m3": None if reference is None else float(reference) * horizon - produced,
        "cost_per_m3": float(cost) / produced if produced > 0 else None,
    })


def _base_summary(matrix, protected_steps, shutdown):
    violations = np.zeros(len(matrix) - 1, dtype=bool)
    for tag, (low, high) in OPERATING_LIMITS.items():
        series = matrix[1:, int(tag[6:-1]) - 1]
        violations |= ~np.isfinite(series)
        if low is not None:
            violations |= series < low
        if high is not None:
            violations |= series > high
    final_steps = min(len(violations), round(FINAL_WINDOW_HOURS / STEP_HOURS))
    return {
        "shutdown": shutdown,
        "window_hours": round((len(matrix) - 1) * STEP_HOURS, 6),
        "violation_minutes": float(violations.sum() * STEP_HOURS * 60),
        "final_window_inside": not bool(np.any(violations[-final_steps:])),
        "protection_minutes": protected_steps * STEP_HOURS * 60,
        "peak_pressure": float(np.max(matrix[:, 6])),
        "peak_temperature": float(np.max(matrix[:, 8])),
        "temperature_sd": float(np.std(matrix[1:, 8])),
        "peak_separator_level": float(np.max(matrix[:, 11])),
        "mean_production": float(np.mean(matrix[1:, 16])),
    }
