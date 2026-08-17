from __future__ import annotations

from typing import Any

from ..actor_contract import ActorTask
from ..domain import ExternalCommunicationDraft
from ..prompt_projection import (
    project_prompt_observation,
    project_prompt_plan_step,
    project_prompt_scratchpad_entry,
)

# Keep only a bounded tail of audit-safe working-memory entries in each model turn.
ACTOR_SCRATCHPAD_ENTRY_LIMIT = 8


# Build the compact, actor-specific view without mutating the canonical runtime state.
def build_actor_prompt_payload(*, actor_task: ActorTask) -> dict[str, Any]:
    relevant_step_ids = {
        actor_task.current_plan_step.step_id,
        *(step.step_id for step in actor_task.past_steps),
    }
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
            "sensitivity": actor_task.task_spec.sensitivity.value,
            "success_criteria": actor_task.task_spec.success_criteria,
            "execution_intent": actor_task.task_spec.execution_intent.value,
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
            "progress_epoch": actor_task.progress_epoch,
            "attempted_action_keys": list(actor_task.attempted_action_keys[-12:]),
        },
        "plan": {
            "current_step_number": actor_task.current_plan_step_index + 1,
            "current_plan_step": project_prompt_plan_step(
                step=actor_task.current_plan_step,
            ),
            "other_steps": [
                project_prompt_plan_step(step=step)
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
                "output_text": step.output_text,
            }
            for step in actor_task.past_steps
        ],
        "observations": [
            project_prompt_observation(observation=item)
            for item in actor_task.observations
        ],
        "scratchpad": [
            project_prompt_scratchpad_entry(entry=entry)
            for entry in scratchpad
        ],
        "current_step_validation": (
            {
                "plan_step_id": current_validation.plan_step_id,
                "actor_self_confidence": current_validation.actor_self_confidence,
                "evidence_refs": list(current_validation.evidence_refs),
                "rationale_summary": current_validation.rationale_summary,
                "residual_uncertainty": current_validation.residual_uncertainty,
            }
            if current_validation is not None
            else None
        ),
        "last_tool_result": _project_last_tool_result(
            actor_task=actor_task,
            has_equivalent_observations=bool(
                actor_task.last_tool_result
                and actor_task.last_tool_result.observations
            ),
        ),
        "workspace": {
            "revision": actor_task.workspace.revision if actor_task.workspace else 0,
            "artifacts": [
                {"artifact_id": item.artifact_id, "step_id": item.step_id, "content": item.content,
                 "evidence_refs": list(item.evidence_refs), "revision": item.revision, "provenance": item.provenance}
                for item in (actor_task.workspace.artifacts if actor_task.workspace else [])
            ],
            "blockers": list(actor_task.workspace.blockers if actor_task.workspace else []),
            "decisions": list(actor_task.workspace.decisions if actor_task.workspace else []),
        },
        "governance_feedback": [
            {"operation_id": item.operation_id, "invalidated_payload_digest": item.invalidated_payload_digest,
             "finding_codes": list(item.finding_codes), "summary": item.summary, "revision_index": item.revision_index,
             "judge_summary": item.judge_summary,
             "judge_rationale": getattr(item, "judge_rationale", None),
             "findings": [
                 {"category": finding.category, "code": finding.code, "severity": finding.severity,
                  "summary": finding.summary, "evidence_refs": list(finding.evidence_refs),
                  "task_requirement": {
                      "kind": finding.task_requirement_kind,
                      "index": finding.task_requirement_index,
                      "quote": finding.task_requirement_quote,
                  },
                  "payload_field": finding.payload_field,
                  "payload_excerpt": finding.payload_excerpt}
                 for finding in item.findings
             ]}
            for item in actor_task.governance_feedback
        ],
        "external_communication_draft": _project_communication_draft(
            draft=actor_task.external_communication_draft,
        ),
        "previous_rejected_draft": _project_communication_draft(
            draft=actor_task.previous_rejected_draft,
        ),
        "protocol_correction": _project_protocol_correction(actor_task=actor_task),
    }


# Serialize either typed draft without changing which enclosing prompt key supplied it.
def _project_communication_draft(
    *,
    draft: ExternalCommunicationDraft | None,
) -> dict[str, Any] | None:
    if draft is None:
        return None
    return {
        "channel": draft.channel,
        "recipient": draft.recipient,
        "subject": draft.subject,
        "body": draft.body,
        "cc": list(draft.cc),
        "bcc": list(draft.bcc),
    }


# Surface the exact protocol violation that discarded the previous provider output.
def _project_protocol_correction(*, actor_task: ActorTask) -> dict[str, Any] | None:
    feedback = actor_task.protocol_retry_feedback
    if not feedback:
        return None
    if isinstance(feedback, str):
        return {
            "previous_protocol_error": feedback,
            "instructions": [
                "Your previous output violated the actor protocol and was discarded.",
                "Correct exactly that error and emit exactly one valid runtime action for the current step.",
                "Use only observation IDs listed in the observations section as evidence_refs.",
                "Do not mix capability requests with control outcomes in one turn.",
            ],
        }
    return {
        "error_code": feedback.error_code,
        "category": feedback.category,
        "summary": feedback.summary,
        "unknown_evidence_ids": list(feedback.unknown_evidence_ids),
        "valid_evidence_ids": list(feedback.valid_evidence_ids),
        "validation_errors": list(feedback.validation_errors),
        "expected_action_schema": feedback.expected_action_schema,
        "instructions": [
            "Your previous output violated the actor protocol and was discarded.",
            "Correct exactly that error and emit exactly one valid runtime action for the current step.",
            "Use only observation IDs listed in the observations section as evidence_refs.",
            "Do not mix capability requests with control outcomes in one turn.",
        ],
    }


# Retain result status while omitting content already represented as observed evidence.
def _project_last_tool_result(
    *,
    actor_task: ActorTask,
    has_equivalent_observations: bool,
) -> dict[str, Any] | None:
    result = actor_task.last_tool_result
    if result is None:
        return None
    projected: dict[str, Any] = {
        "tool_name": result.request.tool_name.value,
        "success": result.success,
        "summary": result.summary,
        "error_message": result.error_message,
        "error_code": result.error_code.value if result.error_code else None,
        "retryable": result.error_code is not None and result.error_code.value in {"timeout", "unavailable"},
    }
    if not has_equivalent_observations:
        projected["content"] = result.content
        projected["observations"] = [
            project_prompt_observation(observation=observation)
            for observation in result.observations
        ]
    return projected


__all__ = ["ACTOR_SCRATCHPAD_ENTRY_LIMIT", "build_actor_prompt_payload"]
