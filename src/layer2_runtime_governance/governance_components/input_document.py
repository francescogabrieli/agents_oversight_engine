from __future__ import annotations

from typing import Any

from .contracts import PolicyEvaluationInput
from ..domain import TaskState


# Build the base facts document sent to OPA before optional pillar selection.
def build_governance_base_facts(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
) -> dict[str, Any]:
    current_step = task_state.current_plan_step
    validation = (
        task_state.actor_result.step_validation
        if task_state.actor_result is not None
        else None
    )
    current_tool_request = task_state.current_tool_request or (
        task_state.actor_result.tool_request
        if task_state.actor_result is not None
        else None
    )
    return {
        "task": {
            "domain_type": policy_input.task_domain_type.value,
            "domain_label": policy_input.task_domain_label,
            "sensitivity": policy_input.task_sensitivity.value,
        },
        "policy": {
            "profile_name": policy_input.policy_profile_name,
            "confidence_threshold": policy_input.confidence_threshold,
            "read_only_mode": policy_input.read_only_mode,
            "budget": {
                "max_steps": policy_input.budget_max_steps,
                "max_tool_calls": policy_input.budget_max_tool_calls,
            },
        },
        "runtime": {
            "state": policy_input.runtime_state.value,
            "flags": [flag.value for flag in policy_input.runtime_flags],
            "actor_turn_count": policy_input.actor_turn_count,
            "tool_call_count": policy_input.tool_call_count,
            "observed_evidence_count": policy_input.observed_evidence_count,
        },
        "step": {
            "id": policy_input.current_plan_step_id,
            "objective": policy_input.current_plan_step_objective,
            "step_kind": current_step.step_kind.value if current_step and current_step.step_kind else None,
            "evidence_requirement": (
                current_step.evidence_requirement.value
                if current_step and current_step.evidence_requirement
                else None
            ),
            "claim_precision_ceiling": (
                current_step.claim_precision_ceiling.value
                if current_step and current_step.claim_precision_ceiling
                else None
            ),
        },
        "actor": {
            "result_type": policy_input.actor_result_type.value,
            "capability_mode": policy_input.capability_mode.value,
            "external_impact": policy_input.external_impact,
            "blocking_reason": policy_input.blocking_reason,
            "has_output_text": policy_input.actor_has_output_text,
        },
        "tool": {
            "name": policy_input.tool_name.value if policy_input.tool_name is not None else None,
            "has_query": policy_input.tool_has_query,
            "has_url": policy_input.tool_has_url,
            "is_read_only": True if current_tool_request is not None else None,
        },
        "external_communication": {
            "phase": (
                policy_input.external_communication_phase.value
                if policy_input.external_communication_phase is not None
                else None
            ),
            "review_ready": policy_input.external_communication_review_ready,
        },
        "step_validation": (
            {
                "objective_satisfied": validation.objective_satisfied,
                "evidence_sufficient": validation.evidence_sufficient,
                "confidence": validation.confidence,
                "next_action": validation.next_action.value,
            }
            if validation is not None
            else None
        ),
    }


# Build the final versioned OPA decision input from base facts and pillar outputs.
def build_governance_decision_input(
    *,
    base_facts: dict[str, Any],
    policy_bundle_version: str,
    policy_config_version: str,
    pillar_requirements: dict[str, bool],
    pillars: dict[str, Any],
    pillar_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "governance_decision_input.v1",
        "policy_bundle_version": policy_bundle_version,
        "policy_config_version": policy_config_version,
        "base_facts": base_facts,
        "pillar_requirements": pillar_requirements,
        "pillars": pillars,
        "pillar_unavailable": {
            name: bool(payload.get("raw_facts", {}).get("pillar_unavailable", False))
            for name, payload in pillars.items()
            if isinstance(payload, dict)
        },
        "pillar_errors": pillar_errors or {},
    }


# Validate the versioned GovernanceDecisionInput JSON Schema before the PEP calls OPA.
def validate_governance_decision_input(payload: dict[str, Any]) -> None:
    from .schema_validation import validate_json_schema

    validate_json_schema(schema_name="governance_decision_input", payload=payload)
    for pillar_name in ("evidence", "risk", "trajectory"):
        pillar = payload["pillars"].get(pillar_name)
        if not isinstance(pillar, dict):
            raise ValueError(f"GovernanceDecisionInput missing pillar '{pillar_name}'.")
        for field_name in ("raw_facts", "score_components", "score_formula_version", "thresholds_version"):
            if field_name not in pillar:
                raise ValueError(f"GovernanceDecisionInput pillar '{pillar_name}' missing '{field_name}'.")
