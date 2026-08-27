from __future__ import annotations

from typing import Any

from layer3_tool_bus_external_capabilities.external_operations.contracts import (
    DatabaseOperationPayload,
    ExternalOperationEffect,
    ExternalOperationProposal,
    OperationBoundary,
)
from layer3_tool_bus_external_capabilities.external_operations.profiles import (
    load_capability_consequence_profiles,
)
from layer2_runtime_governance.governance_components import PolicyEvaluationInput


# Build shadow-only consequence facts without weighted decision scoring.
def build_risk_signal(
    *,
    policy_input: PolicyEvaluationInput,
    external_operation: ExternalOperationProposal | None,
    thresholds_version: str,
    tool_gateway_config=None,
) -> dict[str, Any]:
    del tool_gateway_config
    if (
        external_operation is None
        or external_operation.boundary != OperationBoundary.GOVERNED_EXTERNAL
    ):
        return {
            "raw_facts": {
                "mode": "shadow",
                "decision_influence": False,
                "applicability": "not_applicable",
                "pillar_unavailable": False,
            },
            "diagnostic_score": None,
            "diagnostic_level": "not_applicable",
            "score_components": {},
            "reason_facts": [],
            "summary": "Operational consequence assessment is not applicable outside governed external operations.",
            "score_formula_version": "operational_consequence_shadow_v1",
            "thresholds_version": thresholds_version,
            "mode": "shadow",
            "decision_influence": False,
        }

    profiles = load_capability_consequence_profiles()
    profile = profiles.get(external_operation.capability_profile_id or "")
    recovery_mechanism = profile.compensation_mechanism if profile else None
    recoverability = (
        "compensable"
        if recovery_mechanism
        else (
            "irreversible"
            if external_operation.normalized_effect
            in {
                ExternalOperationEffect.DELETE,
                ExternalOperationEffect.TRUNCATE,
                ExternalOperationEffect.SEND,
                ExternalOperationEffect.PUBLISH,
            }
            else "unknown"
        )
    )
    unknown_facts: list[str] = []
    if profile is None:
        unknown_facts.append("capability_profile")
    if recovery_mechanism is None and recoverability == "unknown":
        unknown_facts.append("concrete_recovery_mechanism")
    if external_operation.target.destination_class.value == "unknown":
        unknown_facts.append("destination_class")

    statement_class = (
        external_operation.payload.statement_class.value
        if isinstance(external_operation.payload, DatabaseOperationPayload)
        else None
    )
    impact_level = _shadow_impact_level(
        effect=external_operation.normalized_effect,
        sensitivity=policy_input.task_sensitivity.value,
        target_criticality=profile.target_criticality if profile else "unknown",
    )
    raw_facts = {
        "mode": "shadow",
        "decision_influence": False,
        "applicability": "applicable",
        "pillar_unavailable": False,
        "operation_id": external_operation.operation_id,
        "operation_digest": external_operation.payload_digest,
        "recoverability": recoverability,
        "blast_radius": profile.scope if profile else "unknown",
        "sensitivity": policy_input.task_sensitivity.value,
        "exposure": external_operation.target.destination_class.value,
        "target_criticality": profile.target_criticality if profile else "unknown",
        "impact_level": impact_level,
        "effect": external_operation.normalized_effect.value,
        "statement_class": statement_class,
        "dominant_factors": [
            external_operation.normalized_effect.value,
            policy_input.task_sensitivity.value,
            profile.target_criticality if profile else "unknown_target_criticality",
        ],
        "unknown_facts": unknown_facts,
        "fact_provenance": [
            external_operation.facts_provenance.normalizer_id,
            external_operation.facts_provenance.normalizer_version,
            external_operation.capability_profile_id or "profile_unknown",
        ],
        "profile_version": profile.profile_version if profile else None,
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": impact_level,
        "score_components": {
            "effect": raw_facts["effect"],
            "sensitivity": raw_facts["sensitivity"],
            "target_criticality": raw_facts["target_criticality"],
            "recoverability": raw_facts["recoverability"],
        },
        "reason_facts": raw_facts["dominant_factors"],
        "summary": (
            "Shadow operational consequence assessment: "
            f"impact={impact_level}, recoverability={recoverability}, "
            f"scope={raw_facts['blast_radius']}."
        ),
        "score_formula_version": "operational_consequence_shadow_v1",
        "thresholds_version": thresholds_version,
        "mode": "shadow",
        "decision_influence": False,
    }


# Derive a non-authoritative impact label for observability only.
def _shadow_impact_level(
    *,
    effect: ExternalOperationEffect,
    sensitivity: str,
    target_criticality: str,
) -> str:
    if effect in {ExternalOperationEffect.DELETE, ExternalOperationEffect.TRUNCATE}:
        return "critical"
    if sensitivity in {"high", "critical"} or target_criticality == "business_system":
        return "high"
    if effect in {
        ExternalOperationEffect.CREATE,
        ExternalOperationEffect.UPDATE,
        ExternalOperationEffect.SEND,
        ExternalOperationEffect.PUBLISH,
        ExternalOperationEffect.EXECUTE,
    }:
        return "medium"
    if effect == ExternalOperationEffect.READ:
        return "low"
    return "unknown"


__all__ = ["build_risk_signal"]
