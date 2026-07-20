from __future__ import annotations

from typing import Any

from ..actor_contract import ActorTask
from ..tool_contract import EvidenceSourceKind


# Keep only a bounded tail of audit-safe working-memory entries in each model turn.
ACTOR_SCRATCHPAD_ENTRY_LIMIT = 8


# Build the compact, actor-specific view without mutating the canonical runtime state.
def build_actor_prompt_payload(*, actor_task: ActorTask) -> dict[str, Any]:
    relevant_step_ids = {
        actor_task.current_plan_step.step_id,
        *(step.step_id for step in actor_task.past_steps),
    }
    evidence = [
        item
        for item in actor_task.observed_evidence
        if item.source_kind == EvidenceSourceKind.TOOL_OBSERVATION
        and item.plan_step_id in relevant_step_ids
    ]
    current_validation = next(
        (
            validation
            for validation in reversed(actor_task.step_validations)
            if validation.plan_step_id == actor_task.current_plan_step.step_id
        ),
        None,
    )
    scratchpad = [
        entry
        for entry in actor_task.scratchpad
        if entry.plan_step_id in relevant_step_ids
    ][-ACTOR_SCRATCHPAD_ENTRY_LIMIT:]

    return {
        "task_spec": {
            "goal": actor_task.task_spec.goal,
            "context": actor_task.task_spec.context,
            "constraints": actor_task.task_spec.constraints,
            "domain_type": actor_task.task_spec.domain_type.value,
            "domain_label": actor_task.task_spec.domain_label,
            "sensitivity": actor_task.task_spec.sensitivity.value,
            "success_criteria": actor_task.task_spec.success_criteria,
        },
        "runtime": {
            "current_state": actor_task.current_state.value,
            "runtime_flags": [flag.value for flag in actor_task.runtime_flags],
            "read_only_mode": actor_task.execution_policy.read_only_mode,
            "actor_turn_count": actor_task.actor_turn_count,
            "tool_call_count": actor_task.tool_call_count,
            "max_tool_calls": actor_task.execution_policy.budget.max_tool_calls,
            "remaining_tool_calls": actor_task.remaining_tool_calls,
            "confidence_threshold": actor_task.execution_policy.confidence_threshold,
        },
        "plan": {
            "current_step_number": actor_task.current_plan_step_index + 1,
            "current_plan_step": _full_step(actor_task=actor_task),
            "other_steps": [
                {
                    "step_id": step.step_id,
                    "objective": step.objective,
                    "status": step.status.value,
                    "step_kind": step.step_kind.value if step.step_kind is not None else None,
                }
                for index, step in enumerate(actor_task.plan_steps)
                if index != actor_task.current_plan_step_index
            ],
        },
        "past_steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "result_type": step.result_type.value,
                "summary": step.summary,
            }
            for step in actor_task.past_steps
        ],
        "observed_evidence": [
            {
                "evidence_id": item.evidence_id,
                "plan_step_id": item.plan_step_id,
                "title": item.title,
                "locator": item.locator,
                "excerpt": item.excerpt,
                "source_tool": item.source_tool.value if item.source_tool is not None else None,
                "support_level": item.support_level.value,
            }
            for item in evidence
        ],
        "scratchpad": [
            {
                "turn_index": entry.turn_index,
                "plan_step_id": entry.plan_step_id,
                "entry_type": entry.entry_type.value,
                "summary": entry.summary,
                "evidence_refs": entry.evidence_refs,
            }
            for entry in scratchpad
        ],
        "current_step_validation": (
            {
                "plan_step_id": current_validation.plan_step_id,
                "objective_satisfied": current_validation.objective_satisfied,
                "evidence_sufficient": current_validation.evidence_sufficient,
                "confidence": current_validation.confidence,
                "next_action": current_validation.next_action.value,
                "rationale_summary": current_validation.rationale_summary,
                "residual_uncertainty": current_validation.residual_uncertainty,
            }
            if current_validation is not None
            else None
        ),
        "last_tool_result": _project_last_tool_result(
            actor_task=actor_task,
            has_equivalent_evidence=bool(
                actor_task.last_tool_result
                and any(
                    item.plan_step_id == actor_task.last_tool_result.request.plan_step_id
                    and item.source_tool == actor_task.last_tool_result.request.tool_name
                    for item in evidence
                )
            ),
        ),
        "execution_hints": {
            "required_evidence_support_level": actor_task.required_evidence_support_level.value,
            "max_supported_claim_precision": actor_task.max_supported_claim_precision.value,
            "step_kind": actor_task.step_kind.value if actor_task.step_kind is not None else None,
        },
    }


# Preserve the full active objective exactly once in the prompt payload.
def _full_step(*, actor_task: ActorTask) -> dict[str, Any]:
    step = actor_task.current_plan_step
    return {
        "step_id": step.step_id,
        "objective": step.objective,
        "rationale": step.rationale,
        "expected_outcome": step.expected_outcome,
        "step_kind": step.step_kind.value if step.step_kind is not None else None,
        "evidence_requirement": (
            step.evidence_requirement.value if step.evidence_requirement is not None else None
        ),
        "claim_precision_ceiling": (
            step.claim_precision_ceiling.value
            if step.claim_precision_ceiling is not None
            else None
        ),
        "status": step.status.value,
    }


# Retain result status while omitting content already represented as observed evidence.
def _project_last_tool_result(
    *,
    actor_task: ActorTask,
    has_equivalent_evidence: bool,
) -> dict[str, Any] | None:
    result = actor_task.last_tool_result
    if result is None:
        return None
    projected: dict[str, Any] = {
        "tool_name": result.request.tool_name.value,
        "success": result.success,
        "summary": result.summary,
        "error_message": result.error_message,
    }
    if not has_equivalent_evidence:
        projected["content"] = result.content
        projected["sources"] = [
            {
                "title": source.title,
                "locator": source.locator,
                "snippet": source.snippet,
            }
            for source in result.sources
        ]
    return projected


__all__ = ["ACTOR_SCRATCHPAD_ENTRY_LIMIT", "build_actor_prompt_payload"]
