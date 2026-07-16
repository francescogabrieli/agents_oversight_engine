from __future__ import annotations

from typing import Any

from layer2_runtime_governance.actor_contract import CapabilityAccessMode
from layer2_runtime_governance.governance_components import PolicyEvaluationInput
from layer2_runtime_governance.tools import (
    build_default_tool_gateway_config,
    get_default_tool_gateway_config_path,
    load_tool_gateway_config,
)


# Build operational-risk facts while leaving risk interpretation to OPA.
def build_risk_signal(*, policy_input: PolicyEvaluationInput, thresholds_version: str) -> dict[str, Any]:
    try:
        tool_config = load_tool_gateway_config(config_path=get_default_tool_gateway_config_path())
    except Exception:
        tool_config = build_default_tool_gateway_config()
    tool_name = policy_input.tool_name.value if policy_input.tool_name is not None else None
    tool_definition = tool_config.tools.get(tool_name) if tool_name else None
    tool_risk_level = tool_definition.risk_level.value if tool_definition is not None else "none"
    raw_facts = {
        "tool_risk_level": tool_risk_level,
        "sensitivity_level": policy_input.task_sensitivity.value,
        "external_impact": policy_input.external_impact,
        "write_capability": policy_input.capability_mode == CapabilityAccessMode.WRITE,
        "tool_requires_human_review": bool(
            tool_definition and tool_definition.requires_human_review
        ),
    }
    score_components = {
        "tool_risk_level": tool_risk_level,
        "sensitivity_level": raw_facts["sensitivity_level"],
        "external_impact": raw_facts["external_impact"],
        "write_capability": raw_facts["write_capability"],
        "tool_requires_human_review": raw_facts["tool_requires_human_review"],
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": score_components,
        "reason_facts": [],
        "summary": "Operational risk facts collected; OPA owns score and threshold interpretation.",
        "score_formula_version": "operational_risk_v2_raw_facts",
        "thresholds_version": thresholds_version,
    }
