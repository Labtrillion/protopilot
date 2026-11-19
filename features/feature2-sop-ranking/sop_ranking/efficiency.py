"""Evaluate paired branches using the RAG operating criteria and benchmark metrics."""

from math import isclose

from .benchmark import beats, eligible, score_timeseries


def score_branch(branch_id, outcome, policy):
    score = score_timeseries(
        branch_id, outcome.times_s, outcome.samples, policy.tracked, policy, outcome.tripped,
        operating_cost=outcome.operating_cost_usd, produced_m3=outcome.produced_m3,
    )
    if score.quality_ok is None:
        score.failed_gates.append("quality_unavailable")
    if outcome.times_s[-1] < policy.horizon_s - 1e-6:
        score.failed_gates.append("incomplete_horizon")
    return score


def improvement(candidate, incumbent, baseline, candidate_loss, incumbent_loss, policy):
    if not eligible(candidate, baseline, policy)[0]:
        return False, "ineligible"
    better, reason = beats(candidate, incumbent, baseline, policy, policy.vs_baseline)
    if better:
        return True, reason
    cost_equal = isclose(candidate.usd_per_m3, incumbent.usd_per_m3, rel_tol=1e-9)
    accuracy_equal = isclose(candidate.tracking_accuracy, incumbent.tracking_accuracy, abs_tol=1e-6)
    if cost_equal and accuracy_equal and candidate_loss < incumbent_loss:
        return True, "lower_production_loss"
    return False, reason
