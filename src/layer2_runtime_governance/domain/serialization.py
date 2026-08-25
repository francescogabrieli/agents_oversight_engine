from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .task_state import TaskState

from layer1_intake import ExecutionIntent

from layer3_tool_bus_external_capabilities.external_operations.contracts import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
    DatabaseOperationParameter,
    DestinationClass,
    ExternalExecutionAuthorizationSource,
    ExternalExecutionErrorCode,
    ExternalExecutionReceipt,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    ExternalOperationEffect,
    ExternalOperationFamily,
    ExternalOperationPayloadEdit,
    ExternalOperationProposal,
    ExternalOperationTarget,
    GenericExternalOperationPayload,
    GenericPayloadField,
    OperationBoundary,
    OperationFactsProvenance,
    SQLStatementClass,
)
from .lifecycle import RuntimeState, TerminationReason
from .review import (
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    ExternalCommunicationState,
    HumanReviewDecision,
    HumanReviewDecisionType,
    HumanReviewExternalEdit,
    HumanReviewRequest,
)
from .runtime_records import (
    AgentWorkspace,
    ToolAttempt,
    ToolDisposition,
    ToolInvocation,
)


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
        cc=tuple(str(item) for item in payload.get("cc", [])),
        bcc=tuple(str(item) for item in payload.get("bcc", [])),
        metadata=_coerce_metadata_dict(payload.get("metadata")),
    )


# Recover typed human-review communication edits from checkpoint dictionaries.
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


# Return a normalized copy so trace and dashboard builders never mutate canonical state.
def normalize_task_state_serialization(task_state: TaskState) -> TaskState:
    normalized = deepcopy(task_state)
    # Materialize the v6 planner marker when recovering older checkpoint objects.
    for plan_step in normalized.plan_steps:
        if "produces_governed_external_effect_proposal" not in vars(plan_step):
            plan_step.produces_governed_external_effect_proposal = False
    # Older checkpoints predate operational workspace and progress fields.
    if not hasattr(normalized, "workspace"):
        normalized.workspace = AgentWorkspace()
    if not hasattr(normalized, "termination_reason"):
        normalized.termination_reason = None
    else:
        normalized.termination_reason = _coerce_optional_enum(
            TerminationReason,
            normalized.termination_reason,
        )
    if not hasattr(normalized, "progress_epoch"):
        normalized.progress_epoch = 0
    if not hasattr(normalized, "state_revision"):
        normalized.state_revision = 0
    if not hasattr(normalized, "no_progress_turns"):
        normalized.no_progress_turns = 0
    if not hasattr(normalized, "evidence_request_count"):
        normalized.evidence_request_count = 0
    # Legacy checkpoints used the whole-run counter as the active ceiling.
    if not hasattr(normalized, "evidence_request_episode_count"):
        normalized.evidence_request_episode_count = normalized.evidence_request_count
    if not hasattr(normalized, "evidence_request_episode_id"):
        current_step = normalized.current_plan_step
        normalized.evidence_request_episode_id = (
            current_step.step_id
            if current_step is not None and normalized.evidence_request_episode_count
            else None
        )
    if not hasattr(normalized, "actor_protocol_retry_count"):
        normalized.actor_protocol_retry_count = 0
    if not hasattr(normalized, "actor_protocol_retry_progress_epoch"):
        normalized.actor_protocol_retry_progress_epoch = 0
    if not hasattr(normalized, "actor_protocol_retry_step_id"):
        normalized.actor_protocol_retry_step_id = None
    if not hasattr(normalized, "actor_protocol_feedback"):
        normalized.actor_protocol_feedback = None
    normalized.tool_attempts = [
        item
        for item in (
            _coerce_tool_attempt(attempt)
            for attempt in getattr(normalized, "tool_attempts", [])
        )
        if item is not None
    ]
    normalized.tool_invocations = [
        item
        for item in (
            _coerce_tool_invocation(invocation)
            for invocation in getattr(normalized, "tool_invocations", [])
        )
        if item is not None
    ]
    if not hasattr(normalized, "pending_tool_requests"):
        normalized.pending_tool_requests = []
    normalized.pending_tool_requests = [
        request for request in (
            _coerce_tool_request(item)
            for item in normalized.pending_tool_requests
        ) if request is not None
    ]
    normalized.execution_intent = _coerce_enum(
        ExecutionIntent,
        getattr(
            normalized,
            "execution_intent",
            getattr(
                normalized.task_spec,
                "execution_intent",
                ExecutionIntent.PROPOSAL_ONLY,
            ),
        ),
    )
    normalized.current_external_execution_request = _coerce_external_execution_request(
        getattr(normalized, "current_external_execution_request", None)
    )
    normalized.last_external_execution_receipt = _coerce_external_execution_receipt(
        getattr(normalized, "last_external_execution_receipt", None)
    )
    normalized.external_execution_history = [
        receipt
        for receipt in (
            _coerce_external_execution_receipt(item)
            for item in getattr(normalized, "external_execution_history", [])
        )
        if receipt is not None
    ]
    normalized.external_execution_attempt_count = int(
        getattr(normalized, "external_execution_attempt_count", 0)
    )
    normalized.external_communication = _coerce_external_communication_state(
        normalized.external_communication
    )
    for request_attr in ("pending_human_review_request", "last_human_review_request"):
        request = getattr(normalized, request_attr)
        if request is not None:
            setattr(normalized, request_attr, _normalize_review_request(request))
    if normalized.last_human_review_decision is not None:
        normalized.last_human_review_decision = _normalize_review_decision(
            normalized.last_human_review_decision
        )
    normalized.human_review_history = [
        _normalize_review_decision(decision)
        for decision in normalized.human_review_history
    ]
    return normalized


# Recover the mutable external-communication branch without sharing nested lists.
def _coerce_external_communication_state(
    payload: ExternalCommunicationState | dict[str, Any] | None,
) -> ExternalCommunicationState | None:
    if payload is None:
        return None
    if isinstance(payload, ExternalCommunicationState):
        payload.draft = coerce_external_communication_draft(payload.draft)
        payload.last_applied_edit = coerce_human_review_external_edit(
            payload.last_applied_edit
        )
        payload.edit_history = [
            edit
            for edit in (
                coerce_human_review_external_edit(edit)
                for edit in payload.edit_history
            )
            if edit is not None
        ]
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External communication state must be a dict or dataclass.")
    return ExternalCommunicationState(
        phase=_coerce_enum(
            ExternalCommunicationPhase,
            payload.get("phase", ExternalCommunicationPhase.EVIDENCE_COLLECTION.value),
        ),
        review_ready=bool(payload.get("review_ready", False)),
        draft=coerce_external_communication_draft(payload.get("draft")),
        last_applied_edit=coerce_human_review_external_edit(
            payload.get("last_applied_edit")
        ),
        edit_history=[
            edit
            for edit in (
                coerce_human_review_external_edit(item)
                for item in payload.get("edit_history", [])
            )
            if edit is not None
        ],
        last_transition_summary=_coerce_optional_string(
            payload.get("last_transition_summary")
        ),
    )


# Rebuild one request from either a typed checkpoint or a legacy dictionary.
def _normalize_review_request(
    request: HumanReviewRequest | dict[str, Any],
) -> HumanReviewRequest:
    if isinstance(request, HumanReviewRequest):
        return replace(
            request,
            current_tool_request=_coerce_tool_request(request.current_tool_request),
            external_communication_draft=coerce_external_communication_draft(
                request.external_communication_draft
            ),
            external_operation_proposal=coerce_external_operation_proposal(
                request.external_operation_proposal
            ),
        )
    if not isinstance(request, dict):
        raise ValueError("Human review request must be a dict or dataclass.")

    # Local imports avoid making the framework-independent domain package cyclic.
    from ..actor_contract import ActorResultType
    from ..governance_components.contracts import GovernanceDirective

    return HumanReviewRequest(
        request_id=_required_text(request.get("request_id"), "request_id"),
        thread_id=_required_text(request.get("thread_id"), "thread_id"),
        current_state=_coerce_enum(RuntimeState, request.get("current_state")),
        resume_state=_coerce_enum(RuntimeState, request.get("resume_state")),
        current_plan_step_id=_coerce_optional_string(
            request.get("current_plan_step_id")
        ),
        current_plan_step_objective=_coerce_optional_string(
            request.get("current_plan_step_objective")
        ),
        actor_result_type=_coerce_optional_enum(
            ActorResultType, request.get("actor_result_type")
        ),
        current_tool_request=_coerce_tool_request(request.get("current_tool_request")),
        governance_directive=_coerce_optional_enum(
            GovernanceDirective, request.get("governance_directive")
        ),
        governance_summary=_coerce_optional_string(request.get("governance_summary")),
        static_policy_summary=_coerce_optional_string(
            request.get("static_policy_summary")
        ),
        dynamic_policy_summary=_coerce_optional_string(
            request.get("dynamic_policy_summary")
        ),
        behavioral_summary=_coerce_optional_string(
            request.get("behavioral_summary")
        ),
        judge_summary=_coerce_optional_string(request.get("judge_summary")),
        oversight_summary=_coerce_optional_string(request.get("oversight_summary")),
        external_communication_target_type=_coerce_optional_string(
            request.get("external_communication_target_type")
        ),
        external_communication_draft=coerce_external_communication_draft(
            request.get("external_communication_draft")
        ),
        reason_codes=tuple(str(item) for item in request.get("reason_codes", [])),
        allowed_decisions=tuple(
            _coerce_enum(HumanReviewDecisionType, item)
            for item in request.get("allowed_decisions", [])
        ),
        review_summary=_coerce_optional_string(request.get("review_summary")),
        operation_id=_coerce_optional_string(request.get("operation_id")),
        operation_payload_digest=_coerce_optional_string(
            request.get("operation_payload_digest")
        ),
        operation_proposal_summary=_coerce_optional_string(
            request.get("operation_proposal_summary")
        ),
        operation_target=_coerce_optional_string(request.get("operation_target")),
        external_operation_proposal=coerce_external_operation_proposal(
            request.get("external_operation_proposal")
        ),
        judge_findings=tuple(
            str(item) for item in request.get("judge_findings", [])
        ),
    )


# Rebuild one human decision while preserving digest-bound operation edits.
def _normalize_review_decision(
    decision: HumanReviewDecision | dict[str, Any],
) -> HumanReviewDecision:
    if isinstance(decision, HumanReviewDecision):
        return replace(
            decision,
            external_communication_edit=coerce_human_review_external_edit(
                decision.external_communication_edit
            ),
            external_operation_edit=_coerce_external_operation_edit(
                decision.external_operation_edit
            ),
        )
    if not isinstance(decision, dict):
        raise ValueError("Human review decision must be a dict or dataclass.")
    return HumanReviewDecision(
        request_id=_required_text(decision.get("request_id"), "request_id"),
        decision_type=_coerce_enum(
            HumanReviewDecisionType, decision.get("decision_type")
        ),
        note=_coerce_optional_string(decision.get("note")),
        clarification_text=_coerce_optional_string(
            decision.get("clarification_text")
        ),
        external_communication_edit=coerce_human_review_external_edit(
            decision.get("external_communication_edit")
        ),
        reviewer_id=_coerce_optional_string(decision.get("reviewer_id")),
        approved_operation_digest=_coerce_optional_string(
            decision.get("approved_operation_digest")
        ),
        external_operation_edit=_coerce_external_operation_edit(
            decision.get("external_operation_edit")
        ),
    )


# Recover one immutable proposal and revalidate its canonical payload digest.
def coerce_external_operation_proposal(
    payload: ExternalOperationProposal | dict[str, Any] | None,
) -> ExternalOperationProposal | None:
    if payload is None or isinstance(payload, ExternalOperationProposal):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External operation proposal must be a dict or dataclass.")
    family = _coerce_enum(
        ExternalOperationFamily, payload.get("operation_family")
    )
    operation_payload = _coerce_external_operation_payload(
        payload.get("payload"), family=family
    )
    target_payload = payload.get("target")
    provenance_payload = payload.get("facts_provenance")
    if not isinstance(target_payload, dict) or not isinstance(provenance_payload, dict):
        raise ValueError("External operation proposal is missing target or provenance.")
    return ExternalOperationProposal(
        operation_id=_required_text(payload.get("operation_id"), "operation_id"),
        plan_step_id=_required_text(payload.get("plan_step_id"), "plan_step_id"),
        capability_id=_required_text(payload.get("capability_id"), "capability_id"),
        boundary=_coerce_enum(OperationBoundary, payload.get("boundary")),
        operation_family=family,
        normalized_effect=_coerce_enum(
            ExternalOperationEffect, payload.get("normalized_effect")
        ),
        execution_candidate=bool(payload.get("execution_candidate", False)),
        target=ExternalOperationTarget(
            kind=_required_text(target_payload.get("kind"), "target.kind"),
            value=_required_text(target_payload.get("value"), "target.value"),
            destination_class=_coerce_enum(
                DestinationClass,
                target_payload.get(
                    "destination_class", DestinationClass.UNKNOWN.value
                ),
            ),
        ),
        payload=operation_payload,
        payload_preview=str(payload.get("payload_preview") or ""),
        payload_digest=_required_text(
            payload.get("payload_digest"), "payload_digest"
        ),
        facts_provenance=OperationFactsProvenance(
            normalizer_id=_required_text(
                provenance_payload.get("normalizer_id"),
                "facts_provenance.normalizer_id",
            ),
            normalizer_version=_required_text(
                provenance_payload.get("normalizer_version"),
                "facts_provenance.normalizer_version",
            ),
            source_kind=_required_text(
                provenance_payload.get("source_kind"),
                "facts_provenance.source_kind",
            ),
            source_id=_coerce_optional_string(provenance_payload.get("source_id")),
            registry_entry_id=_coerce_optional_string(
                provenance_payload.get("registry_entry_id")
            ),
            fact_notes=tuple(
                str(item) for item in provenance_payload.get("fact_notes", [])
            ),
        ),
        capability_profile_id=_coerce_optional_string(
            payload.get("capability_profile_id")
        ),
        capability_profile_version=_coerce_optional_string(
            payload.get("capability_profile_version")
        ),
        modifiable=bool(payload.get("modifiable", True)),
    )


# Recover one family-discriminated external operation payload.
def _coerce_external_operation_payload(
    payload: Any,
    *,
    family: ExternalOperationFamily | None = None,
):
    if isinstance(
        payload,
        (
            CommunicationOperationPayload,
            DatabaseOperationPayload,
            GenericExternalOperationPayload,
        ),
    ):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External operation payload must be a dict or dataclass.")
    resolved_family = family or _payload_family(payload)
    if resolved_family == ExternalOperationFamily.COMMUNICATION:
        return CommunicationOperationPayload(
            channel=_required_text(payload.get("channel"), "payload.channel"),
            recipient=_coerce_optional_string(payload.get("recipient")),
            subject=_coerce_optional_string(payload.get("subject")),
            body=str(payload.get("body") or ""),
            destination_class=_coerce_enum(
                DestinationClass,
                payload.get("destination_class", DestinationClass.UNKNOWN.value),
            ),
            cc=tuple(str(item) for item in payload.get("cc", [])),
            bcc=tuple(str(item) for item in payload.get("bcc", [])),
        )
    if resolved_family == ExternalOperationFamily.DATABASE:
        return DatabaseOperationPayload(
            sql=str(payload.get("sql") or "").strip(),
            normalized_sql=_coerce_optional_string(payload.get("normalized_sql")),
            statement_class=_coerce_enum(
                SQLStatementClass,
                payload.get("statement_class", SQLStatementClass.UNKNOWN.value),
            ),
            statement_count=int(payload.get("statement_count", 0)),
            parse_valid=bool(payload.get("parse_valid", False)),
            ambiguous=bool(payload.get("ambiguous", True)),
            database_profile_id=_required_text(
                payload.get("database_profile_id"), "payload.database_profile_id"
            ),
            parameters=tuple(
                DatabaseOperationParameter(
                    name=_required_text(item.get("name"), "payload.parameters.name"),
                    value=item.get("value"),
                )
                for item in payload.get("parameters", [])
                if isinstance(item, dict)
            ),
        )
    fields = payload.get("fields", [])
    return GenericExternalOperationPayload(
        action=_required_text(payload.get("action"), "payload.action"),
        target=_required_text(payload.get("target"), "payload.target"),
        fields=tuple(
            GenericPayloadField(
                name=_required_text(item.get("name"), "payload.fields.name"),
                value=str(item.get("value") or ""),
            )
            for item in fields
            if isinstance(item, dict)
        ),
    )


# Recover a full-payload HITL edit from a checkpoint dictionary.
def _coerce_external_operation_edit(
    payload: ExternalOperationPayloadEdit | dict[str, Any] | None,
) -> ExternalOperationPayloadEdit | None:
    if payload is None or isinstance(payload, ExternalOperationPayloadEdit):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External operation edit must be a dict or dataclass.")
    operation_payload = payload.get("payload")
    return ExternalOperationPayloadEdit(
        payload=_coerce_external_operation_payload(operation_payload),
        note=_coerce_optional_string(payload.get("note")),
    )


# Recover one digest-bound execution request from checkpoint dictionaries.
def _coerce_external_execution_request(
    payload: ExternalExecutionRequest | dict[str, Any] | None,
) -> ExternalExecutionRequest | None:
    if payload is None or isinstance(payload, ExternalExecutionRequest):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External execution request must be a dict or dataclass.")
    proposal = coerce_external_operation_proposal(payload.get("proposal"))
    if proposal is None:
        raise ValueError("External execution request requires a proposal.")
    return ExternalExecutionRequest(
        operation_id=_required_text(payload.get("operation_id"), "execution.operation_id"),
        capability_id=_required_text(payload.get("capability_id"), "execution.capability_id"),
        proposal=proposal,
        current_payload_digest=_required_text(
            payload.get("current_payload_digest"), "execution.current_payload_digest"
        ),
        authorized_payload_digest=_required_text(
            payload.get("authorized_payload_digest"), "execution.authorized_payload_digest"
        ),
        execution_intent=_coerce_enum(
            ExecutionIntent,
            payload.get("execution_intent", ExecutionIntent.PROPOSAL_ONLY.value),
        ),
        idempotency_key=_required_text(
            payload.get("idempotency_key"), "execution.idempotency_key"
        ),
        authorization_source=_coerce_enum(
            ExternalExecutionAuthorizationSource,
            payload.get("authorization_source"),
        ),
        reviewer_id=_coerce_optional_string(payload.get("reviewer_id")),
        governance_decision_reference=_coerce_optional_string(
            payload.get("governance_decision_reference")
        ),
        attempt_scope=str(payload.get("attempt_scope") or "initial"),
    )


# Recover one audit-safe execution receipt from checkpoint dictionaries.
def _coerce_external_execution_receipt(
    payload: ExternalExecutionReceipt | dict[str, Any] | None,
) -> ExternalExecutionReceipt | None:
    if payload is None or isinstance(payload, ExternalExecutionReceipt):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("External execution receipt must be a dict or dataclass.")
    return ExternalExecutionReceipt(
        operation_id=_required_text(payload.get("operation_id"), "receipt.operation_id"),
        capability_id=_required_text(payload.get("capability_id"), "receipt.capability_id"),
        payload_digest=_required_text(payload.get("payload_digest"), "receipt.payload_digest"),
        status=_coerce_enum(ExternalExecutionStatus, payload.get("status")),
        idempotency_key=_required_text(
            payload.get("idempotency_key"), "receipt.idempotency_key"
        ),
        started_at=_coerce_datetime(payload.get("started_at"), "receipt.started_at"),
        completed_at=_coerce_datetime(payload.get("completed_at"), "receipt.completed_at"),
        external_reference=_coerce_optional_string(payload.get("external_reference")),
        summary=str(payload.get("summary") or ""),
        error_code=_coerce_enum(
            ExternalExecutionErrorCode,
            payload.get("error_code", ExternalExecutionErrorCode.NONE.value),
        ),
        retryable=bool(payload.get("retryable", False)),
        connector_kind=_coerce_optional_string(payload.get("connector_kind")),
        connector_id=_coerce_optional_string(payload.get("connector_id")),
        server_id=_coerce_optional_string(payload.get("server_id")),
        tool_name=_coerce_optional_string(payload.get("tool_name")),
        connector_metadata=tuple(
            GenericPayloadField(
                name=_required_text(item.get("name"), "receipt.metadata.name"),
                value=str(item.get("value") or ""),
            )
            for item in payload.get("connector_metadata", [])
            if isinstance(item, dict)
        ),
        attempt_number=int(payload.get("attempt_number", 1)),
    )


# Recover one typed tool request from JSON-safe checkpoint material.
def _coerce_tool_request(payload):
    from layer3_tool_bus_external_capabilities.tool_contract import ToolName, ToolRequest

    if payload is None or isinstance(payload, ToolRequest):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("Tool request must be a dict or dataclass.")
    return ToolRequest(
        plan_step_id=_required_text(payload.get("plan_step_id"), "tool.plan_step_id"),
        tool_name=_coerce_enum(ToolName, payload.get("tool_name")),
        reason=_required_text(payload.get("reason"), "tool.reason"),
        query=_coerce_optional_string(payload.get("query")),
        url=_coerce_optional_string(payload.get("url")),
        text=_coerce_optional_string(payload.get("text")),
        path=_coerce_optional_string(payload.get("path")),
        expression=_coerce_optional_string(payload.get("expression")),
        timezone=_coerce_optional_string(payload.get("timezone")),
        limit=payload.get("limit") if isinstance(payload.get("limit"), int) else None,
        max_results=(
            payload.get("max_results")
            if isinstance(payload.get("max_results"), int)
            else 5
        ),
        extractive_goal=_coerce_optional_string(payload.get("extractive_goal")),
        document_id=_coerce_optional_string(payload.get("document_id")),
        dataset_id=_coerce_optional_string(payload.get("dataset_id")),
    )


# Recover one typed tool attempt from JSON-safe checkpoint material.
def _coerce_tool_attempt(payload: ToolAttempt | dict[str, Any] | None) -> ToolAttempt | None:
    if payload is None or isinstance(payload, ToolAttempt):
        return payload
    if not isinstance(payload, dict):
        return None
    return ToolAttempt(
        action_key=_required_text(payload.get("action_key"), "tool_attempt.action_key"),
        plan_step_id=_required_text(payload.get("plan_step_id"), "tool_attempt.plan_step_id"),
        progress_epoch=int(payload.get("progress_epoch", 0)),
        disposition=_coerce_enum(
            ToolDisposition,
            payload.get("disposition", ToolDisposition.EXECUTED_SUCCESS.value),
        ),
    )


# Recover one typed tool invocation from JSON-safe checkpoint material.
def _coerce_tool_invocation(payload: ToolInvocation | dict[str, Any] | None) -> ToolInvocation | None:
    if payload is None or isinstance(payload, ToolInvocation):
        return payload
    if not isinstance(payload, dict):
        return None
    return ToolInvocation(
        action_key=_required_text(payload.get("action_key"), "tool_invocation.action_key"),
        plan_step_id=_required_text(payload.get("plan_step_id"), "tool_invocation.plan_step_id"),
        progress_epoch=int(payload.get("progress_epoch", 0)),
        retryable_failure=bool(payload.get("retryable_failure", False)),
    )


# Infer the family discriminator emitted by dataclass and dashboard serializers.
def _payload_family(payload: dict[str, Any]) -> ExternalOperationFamily:
    raw = str(payload.get("payload_type") or "").lower()
    if "communication" in raw:
        return ExternalOperationFamily.COMMUNICATION
    if "database" in raw:
        return ExternalOperationFamily.DATABASE
    if "generic" in raw:
        return ExternalOperationFamily.GENERIC
    if "sql" in payload or "statement_class" in payload:
        return ExternalOperationFamily.DATABASE
    if "channel" in payload or "recipient" in payload:
        return ExternalOperationFamily.COMMUNICATION
    return ExternalOperationFamily.GENERIC


# Convert enum instances and restored string values through one closed path.
def _coerce_enum(enum_type, value):
    if isinstance(value, enum_type):
        return value
    if isinstance(value, Enum):
        value = value.value
    return enum_type(value)


# Preserve optional enum fields without inventing defaults.
def _coerce_optional_enum(enum_type, value):
    if value is None:
        return None
    return _coerce_enum(enum_type, value)


# Keep restored optional text normalized to plain strings or None.
def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Reject missing identity fields instead of producing partially typed contracts.
def _required_text(value: Any, field_name: str) -> str:
    text = _coerce_optional_string(value)
    if text is None:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return text


# Keep restored metadata JSON-like and predictable.
def _coerce_metadata_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("External communication metadata must be a dict.")
    return {str(key): item for key, item in value.items()}


# Parse a persisted ISO timestamp without accepting ambiguous values.
def _coerce_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    text = _required_text(value, field_name)
    return datetime.fromisoformat(text)


__all__ = [
    "coerce_external_communication_draft",
    "coerce_external_operation_proposal",
    "coerce_human_review_external_edit",
    "normalize_task_state_serialization",
]
