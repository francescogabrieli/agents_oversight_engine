from __future__ import annotations

from typing import Any

from layer2_runtime_governance.oversight import (
    _available_evidence_support_level,
    _effective_support_check_level,
    _evidence_support_level_rank,
    _latest_relevant_validation,
    _latest_tool_result_for_evaluated_step,
    _meaningful_observed_evidence,
    _output_claim_support_level,
    _output_stays_within_intake_support,
    _required_evidence_support_level,
    evaluate_evidence_quality,
    load_oversight_policy_config,
)
from layer2_runtime_governance.state import TaskState


# Build raw evidence facts and diagnostic score components without selecting a directive.
def build_evidence_signal(*, task_state: TaskState, thresholds_version: str) -> dict[str, Any]:
    config = load_oversight_policy_config()
    validation = _latest_relevant_validation(task_state=task_state)
    meaningful_evidence = _meaningful_observed_evidence(task_state=task_state)
    last_tool_result = _latest_tool_result_for_evaluated_step(task_state=task_state)
    required_level = _required_evidence_support_level(task_state=task_state)
    available_level = _available_evidence_support_level(task_state=task_state)
    output_claim_level = _output_claim_support_level(task_state=task_state)
    effective_required_level = _effective_support_check_level(
        required_level=required_level,
        output_claim_level=output_claim_level,
    )
    precision_mismatch = (
        not _output_stays_within_intake_support(task_state=task_state)
        and _evidence_support_level_rank(available_level)
        < _evidence_support_level_rank(effective_required_level)
    )
    assessment = evaluate_evidence_quality(task_state=task_state, config=config)
    weights = config.evidence_quality_weights
    raw_facts = {
        "actor_evidence_sufficient": bool(validation and validation.evidence_sufficient),
        "actor_objective_satisfied": bool(validation and validation.objective_satisfied),
        "actor_confidence": float(validation.confidence if validation is not None else 0.0),
        "meaningful_observed_evidence_count": len(meaningful_evidence),
        "observed_evidence_present": bool(meaningful_evidence),
        "source_locator_present": any(item.locator.strip() for item in meaningful_evidence),
        "last_tool_succeeded": bool(last_tool_result and last_tool_result.success),
        "support_level_required": effective_required_level.value,
        "support_level_available": available_level.value,
        "support_rank_required": _evidence_support_level_rank(effective_required_level),
        "support_rank_available": _evidence_support_level_rank(available_level),
        "precision_mismatch": precision_mismatch,
        "output_claim_support_level": output_claim_level.value,
    }
    score_components = {
        "evidence_sufficient": weights.evidence_sufficient if raw_facts["actor_evidence_sufficient"] else 0.0,
        "objective_satisfied": weights.objective_satisfied if raw_facts["actor_objective_satisfied"] else 0.0,
        "actor_confidence": weights.actor_confidence * raw_facts["actor_confidence"],
        "observed_evidence_present": weights.observed_evidence_present if raw_facts["observed_evidence_present"] else 0.0,
        "source_locator_present": weights.source_locator_present if raw_facts["source_locator_present"] else 0.0,
        "last_tool_success": weights.last_tool_success if raw_facts["last_tool_succeeded"] else 0.0,
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": assessment.score,
        "diagnostic_level": assessment.level.value,
        "score_components": score_components,
        "reason_facts": [code.value for code in assessment.reason_codes],
        "summary": f"Evidence diagnostic score {assessment.score:.2f} ({assessment.level.value}).",
        "score_formula_version": "evidence_quality_v2_raw_facts",
        "thresholds_version": thresholds_version,
    }
