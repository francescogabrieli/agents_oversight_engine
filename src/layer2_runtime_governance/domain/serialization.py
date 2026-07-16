from __future__ import annotations

from typing import Any

from .review import (
    ExternalCommunicationDraft,
    HumanReviewDecision,
    HumanReviewExternalEdit,
    HumanReviewRequest,
)
from .task_state import TaskState


# Recover typed external drafts from checkpoints that restored nested dictionaries.
def coerce_external_communication_draft(
    payload: ExternalCommunicationDraft | dict[str, Any] | None,
) -> ExternalCommunicationDraft | None:
    if payload is None or isinstance(payload, ExternalCommunicationDraft):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External communication draft payload must be a dict or dataclass.")
    return ExternalCommunicationDraft(
        channel=str(payload.get("channel") or "").strip(),
        recipient=_coerce_optional_string(payload.get("recipient")),
        subject=_coerce_optional_string(payload.get("subject")),
        body=_coerce_optional_string(payload.get("body")),
        metadata=_coerce_metadata_dict(payload.get("metadata")),
    )


# Recover typed human-review edits from older checkpoint dictionaries.
def coerce_human_review_external_edit(
    payload: HumanReviewExternalEdit | dict[str, Any] | None,
) -> HumanReviewExternalEdit | None:
    if payload is None or isinstance(payload, HumanReviewExternalEdit):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("Human review external edit payload must be a dict or dataclass.")
    draft_payload = payload.get("draft", payload.get("external_communication_draft"))
    draft = coerce_external_communication_draft(draft_payload)
    if draft is None:
        raise ValueError("Human review external edit requires a draft.")
    return HumanReviewExternalEdit(
        draft=draft,
        note=_coerce_optional_string(payload.get("note")),
    )


# Normalize nested external-communication payloads after checkpoint restore.
def normalize_task_state_serialization(task_state: TaskState) -> TaskState:
    external_state = task_state.external_communication
    if external_state is not None:
        external_state.draft = coerce_external_communication_draft(external_state.draft)
        external_state.last_applied_edit = coerce_human_review_external_edit(
            external_state.last_applied_edit
        )
        external_state.edit_history = [
            edit
            for edit in (
                coerce_human_review_external_edit(edit)
                for edit in external_state.edit_history
            )
            if edit is not None
        ]
    for request_attr in ("pending_human_review_request", "last_human_review_request"):
        request = getattr(task_state, request_attr)
        if request is not None:
            setattr(task_state, request_attr, _normalize_review_request(request))
    if task_state.last_human_review_decision is not None:
        task_state.last_human_review_decision = _normalize_review_decision(
            task_state.last_human_review_decision
        )
    task_state.human_review_history = [
        _normalize_review_decision(decision)
        for decision in task_state.human_review_history
    ]
    return task_state


# Rebuild one review request with a normalized optional external draft.
def _normalize_review_request(request: HumanReviewRequest) -> HumanReviewRequest:
    return HumanReviewRequest(
        request_id=request.request_id,
        thread_id=request.thread_id,
        current_state=request.current_state,
        resume_state=request.resume_state,
        current_plan_step_id=request.current_plan_step_id,
        current_plan_step_objective=request.current_plan_step_objective,
        actor_result_type=request.actor_result_type,
        current_tool_request=request.current_tool_request,
        governance_directive=request.governance_directive,
        governance_summary=request.governance_summary,
        static_policy_summary=request.static_policy_summary,
        dynamic_policy_summary=request.dynamic_policy_summary,
        behavioral_summary=request.behavioral_summary,
        judge_summary=request.judge_summary,
        oversight_summary=request.oversight_summary,
        external_communication_target_type=request.external_communication_target_type,
        external_communication_draft=coerce_external_communication_draft(
            request.external_communication_draft
        ),
        reason_codes=request.reason_codes,
        allowed_decisions=request.allowed_decisions,
        review_summary=request.review_summary,
    )


# Rebuild one human decision with a normalized optional edit.
def _normalize_review_decision(decision: HumanReviewDecision) -> HumanReviewDecision:
    return HumanReviewDecision(
        request_id=decision.request_id,
        decision_type=decision.decision_type,
        note=decision.note,
        clarification_text=decision.clarification_text,
        external_communication_edit=coerce_human_review_external_edit(
            decision.external_communication_edit
        ),
        reviewer_id=decision.reviewer_id,
    )


# Keep restored optional text normalized to plain strings or None.
def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Keep restored metadata JSON-like and predictable.
def _coerce_metadata_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("External communication metadata must be a dict.")
    return {str(key): item for key, item in value.items()}


__all__ = [
    "coerce_external_communication_draft",
    "coerce_human_review_external_edit",
    "normalize_task_state_serialization",
]
