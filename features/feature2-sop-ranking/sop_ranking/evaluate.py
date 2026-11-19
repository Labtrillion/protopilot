"""Compare alternatives against Feature 1's setpoints on the same simulation checkpoint."""

from dataclasses import asdict
from uuid import uuid4

from server.catalog import CHANNELS, SETPOINTS

from .benchmark import eligible
from .cases import ResolvedSetpointChange, SimulatorCase
from .efficiency import improvement, score_branch
from .protopilot_cases import SETPOINT_PV, build_benchmark
from .score_selection import score_trials, select_candidates, trial_context
from .sim_tools import SimulationError
from .zoro_bridge import CheckpointRunner


def run_branch(runner, snapshot, case_id, changes, policy):
    resolved = []
    for change in changes:
        key = change["setpoint"]
        _, unit, _, low, high = SETPOINTS[key]
        resolved.append(ResolvedSetpointChange(
            setpoint_id=key, scheme_index=None, loop_id=f"Ricker:{key}",
            controlled_tag=SETPOINT_PV.get(key, key), from_value=change["from"],
            from_source="active_setpoint", target_value=change["to"], unit=unit,
            apply_at_s=0, ramp_rate_per_h=None, allowed_min=low, allowed_max=high,
        ))
    case = SimulatorCase(case_id=case_id, policy="propose_setpoint" if changes else "hold",
                         setpoint_changes=resolved, values_status="example")
    report = {"id": case_id, "changes": changes}
    try:
        outcome = runner.run(snapshot, case, policy.horizon_s)
        score = score_branch(case_id, outcome, policy)
    except (SimulationError, KeyError, ValueError) as error:
        return {**report, "error": str(error)}, None
    report.update(
        cost_per_m3=score.usd_per_m3,
        tracking_accuracy=score.tracking_accuracy if score.per_variable_accuracy else None,
        production_loss_m3=outcome.production_loss_m3,
        produced_m3=score.product_m3,
        failed_gates=list(score.failed_gates),
    )
    return report, score


def score_response_plans(handoff, state, result, *, timeout):
    plans = [result["baseline"], *result["evaluated"]]
    descriptions = {
        plan["id"]: ", ".join(
            f"{change['setpoint']} from {change['from']} to {change['to']} {SETPOINTS[change['setpoint']][1]}"
            for change in plan["changes"]
        ) or "Hold all current setpoints"
        for plan in plans
    }
    try:
        return score_trials(
            trial_context(handoff, state), descriptions,
            comparison="Compare complete alarm response plans, including the Feature 1 plan on its own.",
            timeout=timeout, request_id=f"{result['request_id']}-plans",
        ), None
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as error:
        return {}, type(error).__name__


def evaluate_alternatives(point, handoff, state, *, timeout):
    request_id = uuid4().hex
    selection = select_candidates(handoff, state, timeout=timeout, request_id=request_id)
    checkpoint = handoff["checkpoint"]
    snapshot = checkpoint["checkpoint"]
    horizon_s = checkpoint["horizon_hours"] * 3600
    units = {tag: channel["unit"] for tag, channel in CHANNELS.items()}
    policy = build_benchmark(asdict(point.controller.setpoints), point.limits, units, horizon_s)
    evaluation = handoff["evaluation_policy"]
    policy.final_window_s = min(evaluation["final_window_hours"] * 3600, horizon_s)
    policy.production_ratio_min = evaluation["production_ratio_min"]
    policy.g_mass_pct_range = tuple(evaluation["g_mass_pct_range"])
    policy.max_def_mol_pct = evaluation["max_def_mol_pct"]

    baseline_changes = handoff["baseline_changes"]
    runner = CheckpointRunner({snapshot: point})
    baseline, baseline_score = run_branch(runner, snapshot, "feature1", baseline_changes, policy)
    result = {"request_id": request_id, "policy_id": evaluation["id"], "selection": selection,
              "baseline": baseline, "evaluated": [], "recommended": None, "response_scores": {}}
    if baseline_score is None:
        return {**result, "status": "human_review", "reason": "baseline_unavailable"}

    baseline_eligible, failed_gates = eligible(baseline_score, baseline_score, policy)
    baseline.update(eligible=baseline_eligible, failed_gates=failed_gates)
    best = baseline
    best_score = baseline_score
    for alternative in selection["selected"]:
        changes = [*baseline_changes, *alternative["changes"]]
        report, score = run_branch(runner, snapshot, alternative["id"], changes, policy)
        report["moves"] = alternative["changes"]
        result["evaluated"].append(report)
        if score is None:
            continue
        report["eligible"], report["failed_gates"] = eligible(score, baseline_score, policy)
        better, report["reason"] = improvement(
            score, best_score, baseline_score, report["production_loss_m3"], best["production_loss_m3"], policy,
        )
        if better:
            best, best_score = report, score

    if result["evaluated"]:
        result["response_scores"], result["response_score_reason"] = score_response_plans(
            handoff, state, result, timeout=timeout,
        )
    if best["eligible"]:
        return {**result, "status": "improved" if best is not baseline else "baseline",
                "recommended": best}
    return {**result, "status": "human_review", "reason": "no_eligible_branch"}
