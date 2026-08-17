# Live transport validation uses ValueError so the HTTP boundary returns a client error.
# ruff: noqa: TRY004
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .review import (
    ExternalCommunicationDraft,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
)
from .review_operation_payload import coerce_external_operation_edit
from .review_payload_normalization import (
    normalize_communication_body as _required_communication_body,
    normalize_copy_recipients as _coerce_recipient_tuple,
    normalize_optional_text as _optional_text,
    require_text as _required_text,
)


# Convert one untrusted live review payload into the exact canonical decision contract.
def coerce_human_review_decision_payload(
    payload: Any,
    *,
    pending_request: HumanReviewRequest | None = None,
    expected_request_id: str | None = None,
    expected_operation_digest: str | None = None,
) -> HumanReviewDecision:
    resolved_request_id, resolved_digest = _resolve_expected_review_scope(
        pending_request=pending_request,
        expected_request_id=expected_request_id,
        expected_operation_digest=expected_operation_digest,
    )
    current_draft = (
        pending_request.external_communication_draft
        if pending_request is not None
        else None
    )
    decision = _coerce_decision(
        payload,
        current_draft=current_draft,
    )

    if decision.request_id != resolved_request_id:
        raise ValueError(
            "Human review resume payload request_id does not match the pending review."
        )
    if (
        pending_request is not None
        and decision.decision_type not in pending_request.allowed_decisions
    ):
        raise ValueError(
            "Human review decision is not permitted for the current review request."
        )

    decision = _validate_decision_semantics(decision)
    return _bind_approval_digest(
        decision,
        expected_operation_digest=resolved_digest,
    )


# Resolve immutable request and proposal scope from the pending review when available.
def _resolve_expected_review_scope(
    *,
    pending_request: HumanReviewRequest | None,
    expected_request_id: str | None,
    expected_operation_digest: str | None,
) -> tuple[str, str | None]:
    if pending_request is not None:
        if (
            expected_request_id is not None
            and expected_request_id != pending_request.request_id
        ):
            raise ValueError("Conflicting expected human review request identifiers.")
        if (
            expected_operation_digest is not None
            and expected_operation_digest != pending_request.operation_payload_digest
        ):
            raise ValueError("Conflicting expected external operation digests.")
        expected_request_id = pending_request.request_id
        expected_operation_digest = pending_request.operation_payload_digest

    if expected_request_id is None or not expected_request_id.strip():
        raise ValueError("An expected human review request_id is required.")
    return expected_request_id.strip(), expected_operation_digest


# Parse either a typed decision or its JSON-safe live transport representation.
def _coerce_decision(
    payload: Any,
    *,
    current_draft: ExternalCommunicationDraft | None,
) -> HumanReviewDecision:
    if isinstance(payload, HumanReviewDecision):
        decision_type = _coerce_decision_type(payload.decision_type)
        external_edit = (
            _coerce_external_communication_edit(
                payload.external_communication_edit,
                current_draft=None,
                root_payload=None,
            )
            if payload.external_communication_edit is not None
            else None
        )
        operation_edit = (
            coerce_external_operation_edit(payload.external_operation_edit)
            if payload.external_operation_edit is not None
            else None
        )
        return HumanReviewDecision(
            request_id=_required_text(payload.request_id, field_name="request_id"),
            decision_type=decision_type,
            note=_optional_text(payload.note, field_name="note"),
            clarification_text=_optional_text(
                payload.clarification_text,
                field_name="clarification_text",
            ),
            external_communication_edit=external_edit,
            reviewer_id=_optional_text(payload.reviewer_id, field_name="reviewer_id"),
            approved_operation_digest=_optional_text(
                payload.approved_operation_digest,
                field_name="approved_operation_digest",
            ),
            external_operation_edit=operation_edit,
        )
    if not isinstance(payload, dict):
        raise ValueError("Human review resume payload must be a decision dict or dataclass.")

    decision_type_value = payload.get("decision_type")
    if decision_type_value is None:
        raise ValueError("Human review resume payload is missing decision_type.")
    decision_type = _coerce_decision_type(decision_type_value)

    communication_edit_payload = payload.get("external_communication_edit")
    operation_edit_payload = payload.get("external_operation_edit")
    external_edit = None
    if communication_edit_payload is not None:
        external_edit = _coerce_external_communication_edit(
            communication_edit_payload,
            current_draft=current_draft,
            root_payload=payload,
        )
    elif (
        decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE
        and operation_edit_payload is None
        and current_draft is not None
    ):
        # The dashboard may submit only changed form fields; materialize one full draft.
        external_edit = _coerce_external_communication_edit(
            {},
            current_draft=current_draft,
            root_payload=payload,
        )

    operation_edit = (
        coerce_external_operation_edit(operation_edit_payload)
        if operation_edit_payload is not None
        else None
    )
    return HumanReviewDecision(
        request_id=_required_text(payload.get("request_id"), field_name="request_id"),
        decision_type=decision_type,
        note=_optional_text(payload.get("note"), field_name="note"),
        clarification_text=_optional_text(
            payload.get("clarification_text"),
            field_name="clarification_text",
        ),
        external_communication_edit=external_edit,
        reviewer_id=_optional_text(
            payload.get("reviewer_id"),
            field_name="reviewer_id",
        ),
        approved_operation_digest=_optional_text(
            payload.get("approved_operation_digest"),
            field_name="approved_operation_digest",
        ),
        external_operation_edit=operation_edit,
    )


# Enforce decision-specific invariants identically for dashboard and runtime entry points.
def _validate_decision_semantics(
    decision: HumanReviewDecision,
) -> HumanReviewDecision:
    has_communication_edit = decision.external_communication_edit is not None
    has_operation_edit = decision.external_operation_edit is not None
    if has_communication_edit and has_operation_edit:
        raise ValueError(
            "modify_and_continue accepts exactly one typed external operation edit."
        )

    if decision.decision_type == HumanReviewDecisionType.MODIFY_AND_CONTINUE:
        if not has_communication_edit and not has_operation_edit:
            raise ValueError(
                "modify_and_continue requires a typed external operation edit."
            )
    elif has_communication_edit or has_operation_edit:
        raise ValueError(
            "External operation edits are valid only for modify_and_continue."
        )

    if decision.decision_type == HumanReviewDecisionType.ASK_CLARIFICATION:
        if decision.clarification_text is None:
            raise ValueError(
                "Clarification text is required when asking for clarification."
            )
    elif decision.clarification_text is not None:
        raise ValueError(
            "Clarification text is valid only for ask_clarification."
        )
    return decision


# Bind approvals to the pending digest and reject a caller-supplied conflicting digest.
def _bind_approval_digest(
    decision: HumanReviewDecision,
    *,
    expected_operation_digest: str | None,
) -> HumanReviewDecision:
    approval_types = {
        HumanReviewDecisionType.APPROVE_CONTINUE,
        HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
    }
    if decision.decision_type not in approval_types:
        if decision.approved_operation_digest is not None:
            raise ValueError(
                "An approved operation digest is valid only for an approval decision."
            )
        return decision
    if expected_operation_digest is None:
        return decision
    if (
        decision.approved_operation_digest is not None
        and decision.approved_operation_digest != expected_operation_digest
    ):
        raise ValueError(
            "Human review approval digest does not match the pending operation."
        )
    return replace(
        decision,
        approved_operation_digest=expected_operation_digest,
    )


# Parse the closed decision enum without accepting legacy aliases.
def _coerce_decision_type(value: Any) -> HumanReviewDecisionType:
    if isinstance(value, HumanReviewDecisionType):
        return value
    if not isinstance(value, str):
        raise ValueError("Human review decision_type must be a string.")
    try:
        return HumanReviewDecisionType(value.strip())
    except ValueError as exc:
        raise ValueError(f"Unknown human review decision_type: {value}") from exc


# Materialize a full communication edit while preserving unchanged bounded draft fields.
def _coerce_external_communication_edit(
    payload: Any,
    *,
    current_draft: ExternalCommunicationDraft | None,
    root_payload: dict[str, Any] | None,
) -> HumanReviewExternalEdit:
    if isinstance(payload, HumanReviewExternalEdit):
        return HumanReviewExternalEdit(
            draft=_coerce_external_communication_draft(
                payload.draft,
                current_draft=None,
                root_payload=None,
            ),
            note=_optional_text(payload.note, field_name="external_communication_edit.note"),
        )
    if not isinstance(payload, dict):
        raise ValueError("External communication edit must be a dict or dataclass.")

    draft_payload = payload.get("draft")
    if draft_payload is None:
        draft_payload = {}
    if not isinstance(draft_payload, (dict, ExternalCommunicationDraft)):
        raise ValueError("External communication edit draft must be an object.")
    return HumanReviewExternalEdit(
        draft=_coerce_external_communication_draft(
            draft_payload,
            current_draft=current_draft,
            root_payload=root_payload,
        ),
        note=(
            _optional_text(payload.get("note"), field_name="external_communication_edit.note")
            or _optional_text(
                root_payload.get("note") if root_payload is not None else None,
                field_name="note",
            )
        ),
    )


# Build and validate the complete outbound communication draft from one live edit.
def _coerce_external_communication_draft(
    payload: ExternalCommunicationDraft | dict[str, Any],
    *,
    current_draft: ExternalCommunicationDraft | None,
    root_payload: dict[str, Any] | None,
) -> ExternalCommunicationDraft:
    if isinstance(payload, ExternalCommunicationDraft):
        channel_value = payload.channel
        recipient_value = payload.recipient
        subject_value = payload.subject
        body_value = payload.body
        cc_value: Any = payload.cc
        bcc_value: Any = payload.bcc
        metadata_value: Any = payload.metadata
    else:
        channel_value = _draft_value(
            payload,
            "channel",
            root_payload=root_payload,
            root_key="external_communication_channel",
            fallback=current_draft.channel if current_draft is not None else None,
        )
        recipient_value = _draft_value(
            payload,
            "recipient",
            root_payload=None,
            root_key=None,
            fallback=current_draft.recipient if current_draft is not None else None,
        )
        subject_value = _draft_value(
            payload,
            "subject",
            root_payload=None,
            root_key=None,
            fallback=current_draft.subject if current_draft is not None else None,
        )
        body_value = _draft_value(
            payload,
            "body",
            root_payload=root_payload,
            root_key="external_communication_body",
            fallback=current_draft.body if current_draft is not None else None,
        )
        cc_value = payload.get(
            "cc",
            current_draft.cc if current_draft is not None else (),
        )
        bcc_value = payload.get(
            "bcc",
            current_draft.bcc if current_draft is not None else (),
        )
        if "metadata" in payload:
            metadata_value = payload["metadata"]
        elif root_payload is not None and "external_communication_metadata" in root_payload:
            metadata_value = root_payload["external_communication_metadata"]
        else:
            metadata_value = current_draft.metadata if current_draft is not None else {}

    return ExternalCommunicationDraft(
        channel=_required_text(
            channel_value,
            field_name="External communication channel",
        ),
        recipient=_optional_text(
            recipient_value,
            field_name="External communication recipient",
        ),
        subject=_optional_text(
            subject_value,
            field_name="External communication subject",
        ),
        body=_required_communication_body(
            body_value,
        ),
        cc=_coerce_recipient_tuple(cc_value, field_name="cc"),
        bcc=_coerce_recipient_tuple(bcc_value, field_name="bcc"),
        metadata=_coerce_metadata(metadata_value),
    )


# Resolve a nested, legacy-flat, or unchanged draft field without mutating its source.
def _draft_value(
    payload: dict[str, Any],
    key: str,
    *,
    root_payload: dict[str, Any] | None,
    root_key: str | None,
    fallback: Any,
) -> Any:
    if key in payload:
        return payload[key]
    if root_payload is not None and root_key is not None and root_key in root_payload:
        return root_payload[root_key]
    return fallback


# Normalize metadata while retaining the dashboard's bounded JSON-text form input.
def _coerce_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "External communication metadata must be valid JSON."
            ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "External communication metadata must be a JSON object."
        )
    return {str(key): item for key, item in value.items()}


__all__ = ["coerce_human_review_decision_payload"]
