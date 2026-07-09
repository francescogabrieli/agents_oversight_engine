from __future__ import annotations

from typing import Any

from layer2_runtime_governance.actor_contract import CapabilityAccessMode
from layer2_runtime_governance.governance import PolicyEvaluationInput
from layer2_runtime_governance.oversight import (
    evaluate_operational_risk,
    load_oversight_policy_config,
    resolve_runtime_action_risk_context,
)
from layer2_runtime_governance.tool_gateway import (
    build_default_tool_gateway_config,
    get_default_tool_gateway_config_path,
    load_tool_gateway_config,
)


# Build operational-risk facts while leaving risk interpretation to OPA.
def build_risk_signal(*, policy_input: PolicyEvaluationInput, thresholds_version: str) -> dict[str, Any]:
    config = load_oversight_policy_config()
    try:
        tool_config = load_tool_gateway_config(config_path=get_default_tool_gateway_config_path())
    except Exception:
        tool_config = build_default_tool_gateway_config()
    context = resolve_runtime_action_risk_context(
        policy_input=policy_input,
        tool_gateway_config=tool_config,
    )
    assessment = evaluate_operational_risk(
        policy_input=policy_input,
        config=config,
        tool_gateway_config=tool_config,
    )
    base = config.risk_scores.tool_risk.get(context.tool_risk_level, 0.45)
    multiplier = config.risk_scores.sensitivity_multiplier.get(
        policy_input.task_sensitivity.value,
        1.0,
    )
    raw_facts = {
        "tool_risk_level": context.tool_risk_level,
        "tool_risk_base": base,
        "sensitivity_level": policy_input.task_sensitivity.value,
        "sensitivity_multiplier": multiplier,
        "external_impact": policy_input.external_impact,
        "write_capability": policy_input.capability_mode == CapabilityAccessMode.WRITE,
        "tool_requires_human_review": bool(
            context.tool_definition and context.tool_definition.requires_human_review
        ),
    }
    score_components = {
        "tool_risk_base_x_sensitivity": base * multiplier,
        "external_impact_additive": config.risk_scores.external_impact_additive if raw_facts["external_impact"] else 0.0,
        "write_capability_additive": config.risk_scores.write_capability_additive if raw_facts["write_capability"] else 0.0,
        "requires_human_review_additive": config.risk_scores.requires_human_review_additive if raw_facts["tool_requires_human_review"] else 0.0,
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": assessment.score,
        "diagnostic_level": assessment.level.value,
        "score_components": score_components,
        "reason_facts": [code.value for code in assessment.reason_codes],
        "summary": f"Operational risk diagnostic score {assessment.score:.2f} ({assessment.level.value}).",
        "score_formula_version": "operational_risk_v2_raw_facts",
        "thresholds_version": thresholds_version,
    }
