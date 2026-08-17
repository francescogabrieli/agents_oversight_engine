from __future__ import annotations

from typing import Any

from .contracts import PolicyEvaluationInput
from ..domain import TaskState
from layer3_tool_bus_external_capabilities.external_operations.contracts import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    ExternalOperationProposal,
    GenericExternalOperationPayload,
)
from layer3_tool_bus_external_capabilities.external_operations.profiles import load_capability_consequence_profiles


# Build the base facts document sent to OPA before optional pillar selection.
def build_governance_base_facts(
    *,
    task_state: TaskState,
    policy_input: PolicyEvaluationInput,
) -> dict[str, Any]:
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
            "observation_count": policy_input.observation_count,
        },
        "step": {
            "id": policy_input.current_plan_step_id,
            "objective": policy_input.current_plan_step_objective,
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
            "is_read_only": (
                task_state.external_operation_proposal.normalized_effect.value == "read"
                if current_tool_request is not None
                and task_state.external_operation_proposal is not None
                else None
            ),
        },
        "external_operation": _external_operation_facts(
            task_state.external_operation_proposal
        ),
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
                "actor_self_confidence": validation.actor_self_confidence,
                "evidence_refs": list(validation.evidence_refs),
            }
            if validation is not None
            else None
        ),
        # Point confidence is contextual uncertainty, never a trajectory fact.
        "uncertainty": {
            "actor_self_confidence": validation.actor_self_confidence if validation is not None else None,
            "configured_confidence_threshold": task_state.execution_policy.confidence_threshold,
        },
    }


# Serialize the canonical proposal into a closed, policy-oriented fact shape.
def _external_operation_facts(
    proposal: ExternalOperationProposal | None,
) -> dict[str, Any]:
    if proposal is None:
        return {
            "operation_id": None,
            "plan_step_id": None,
            "capability_id": None,
            "boundary": "local",
            "operation_family": "generic",
            "normalized_effect": "read",
            "execution_candidate": False,
            "target": {
                "kind": "local",
                "value": "runtime",
                "destination_class": "internal",
            },
            "payload_digest": None,
            "payload_preview": None,
            "capability_profile_id": None,
            "capability_profile_version": None,
            "approval_mode": "never",
            "facts_provenance": None,
            "payload": {"payload_type": "none"},
        }
    profile = load_capability_consequence_profiles().get(
        proposal.capability_profile_id or ""
    )
    return {
        "operation_id": proposal.operation_id,
        "plan_step_id": proposal.plan_step_id,
        "capability_id": proposal.capability_id,
        "boundary": proposal.boundary.value,
        "operation_family": proposal.operation_family.value,
        "normalized_effect": proposal.normalized_effect.value,
        "execution_candidate": proposal.execution_candidate,
        "target": {
            "kind": proposal.target.kind,
            "value": proposal.target.value,
            "destination_class": proposal.target.destination_class.value,
        },
        "payload_digest": proposal.payload_digest,
        "payload_preview": proposal.payload_preview,
        "capability_profile_id": proposal.capability_profile_id,
        "capability_profile_version": proposal.capability_profile_version,
        # Unknown profiles are conservative facts; OPA remains the authority deciding the gate.
        "approval_mode": profile.approval_mode if profile is not None else "conditional",
        "facts_provenance": {
            "normalizer_id": proposal.facts_provenance.normalizer_id,
            "normalizer_version": proposal.facts_provenance.normalizer_version,
            "source_kind": proposal.facts_provenance.source_kind,
            "source_id": proposal.facts_provenance.source_id,
            "registry_entry_id": proposal.facts_provenance.registry_entry_id,
            "fact_notes": list(proposal.facts_provenance.fact_notes),
        },
        "payload": _external_payload_facts(proposal),
    }


# Serialize only capability-specific deterministic payload facts.
def _external_payload_facts(
    proposal: ExternalOperationProposal,
) -> dict[str, Any]:
    payload = proposal.payload
    if isinstance(payload, CommunicationOperationPayload):
        return {
            "payload_type": "communication",
            "channel": payload.channel,
            "recipient": payload.recipient,
            "cc": list(payload.cc),
            "bcc": list(payload.bcc),
            "subject": payload.subject,
            "destination_class": payload.destination_class.value,
        }
    if isinstance(payload, DatabaseOperationPayload):
        return {
            "payload_type": "database",
            "statement_class": payload.statement_class.value,
            "statement_count": payload.statement_count,
            "parse_valid": payload.parse_valid,
            "ambiguous": payload.ambiguous,
            "database_profile_id": payload.database_profile_id,
            "is_read_only_profile": payload.is_read_only_profile,
        }
    if isinstance(payload, GenericExternalOperationPayload):
        return {
            "payload_type": "generic",
            "action": payload.action,
            "target": payload.target,
            "field_names": [item.name for item in payload.fields],
        }
    raise ValueError("Unsupported external operation payload.")


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
    for pillar_name in ("evidence", "risk", "trajectory", "behavioral", "judge"):
        pillar = payload["pillars"].get(pillar_name)
        if not isinstance(pillar, dict):
            raise ValueError(f"GovernanceDecisionInput missing pillar '{pillar_name}'.")
        for field_name in ("raw_facts", "score_components", "score_formula_version", "thresholds_version"):
            if field_name not in pillar:
                raise ValueError(f"GovernanceDecisionInput pillar '{pillar_name}' missing '{field_name}'.")
