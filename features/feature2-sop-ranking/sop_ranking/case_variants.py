"""Build simulation candidates from the single SOP response chosen for this event."""
from math import isfinite

from .policy import AgentPolicy
from .rag import NormalizedRagResult
from .registry import ActionCatalog, ActionKind


def case1_search(catalog: ActionCatalog, policy: AgentPolicy, rag: NormalizedRagResult,
                 action_id: str) -> tuple[ActionCatalog, AgentPolicy, NormalizedRagResult]:
    action = catalog.get(action_id)
    case = catalog.case(action.case_id)
    assert case is not None
    actions = [a for a in catalog.actions if a.kind != ActionKind.SIMULATION]
    actions.append(action.model_copy(update={"offer_without_rag": True}))
    cases = list(catalog.cases)
    source = rag.candidates_for(action_id)
    candidates = [c for c in rag.candidates if c.action_id is None or c.action_id == action_id
                  or any(a.action_id == c.action_id and a.kind != ActionKind.SIMULATION for a in actions)]
    specs = catalog.setpoint_map()
    seen = {(ch.setpoint_id, ch.target_value) for ch in case.setpoint_changes}
    for step in policy.setpoint_variant_steps_pct:
        for index, change in enumerate(case.setpoint_changes):
            target = round(change.target_value + abs(change.target_value) * step / 100, 9)
            spec = specs[change.setpoint_id]
            key = (change.setpoint_id, target)
            if not isfinite(target) or key in seen or not spec.allowed_min <= target <= spec.allowed_max:
                continue
            seen.add(key)
            number = len(cases) - len(catalog.cases) + 2
            variant_id = f"{action_id}:VARIANT:{number}"
            case_id = f"{case.case_id}:VARIANT:{number}"
            changes = list(case.setpoint_changes)
            changes[index] = change.model_copy(update={"target_value": target})
            description = f"{case.case_id} 변형 {number}: {change.setpoint_id} → {target:g} {change.unit}"
            cases.append(case.model_copy(update={"case_id": case_id, "description": description,
                                               "setpoint_changes": changes,
                                               "expected_cost_direction": {}, "expected_direction_basis": None}))
            actions.append(action.model_copy(update={"action_id": variant_id, "case_id": case_id,
                                                   "description": description, "recommendable": True}))
            candidates.extend(c.model_copy(update={"raw_ref": variant_id, "action_id": variant_id,
                                                   "mapping": "exact", "purpose": description}) for c in source)
    ids = {a.action_id for a in actions}
    search_catalog = ActionCatalog.model_validate({**catalog.model_dump(), "actions": actions, "cases": cases,
                                                   "aliases": {k: v for k, v in catalog.aliases.items() if v in ids}})
    search_policy = policy.model_copy(update={"recommendation": policy.recommendation.model_copy(
        update={"baseline_action_id": action_id})})
    return search_catalog, search_policy, rag.model_copy(update={"candidates": candidates})
