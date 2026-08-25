from __future__ import annotations

from dataclasses import replace

from layer2_runtime_governance.actor_contract import CapabilityAccessMode
from layer2_runtime_governance.domain import TaskState
from ..tool_contract import tool_request_execution_arguments
from ..tools import ToolGatewayConfig, build_default_tool_gateway_config
from .contracts import (
    ExternalOperationEffect,
    ExternalOperationProposal,
    OperationBoundary,
)
from .normalizers import (
    normalize_communication_operation,
    normalize_generic_external_operation,
)


# Derive the canonical proposal from runtime state and registry-owned capability metadata.
def prepare_external_operation_proposal(
    *,
    task_state: TaskState,
    tool_gateway_config: ToolGatewayConfig | None,
) -> ExternalOperationProposal | None:
    actor_result = task_state.actor_result
    if actor_result is None:
        return None

    # A queued tool request is the exact candidate at this governance boundary.
    # It must supersede the prior request's proposal even within the same actor turn.
    proposal = _proposal_from_tool_request(
        task_state=task_state,
        tool_gateway_config=tool_gateway_config,
    )
    if proposal is None:
        proposal = _proposal_from_current_canonical_state(task_state=task_state)
    if proposal is None:
        proposal = _proposal_from_review_ready_communication(task_state=task_state)
    if proposal is None:
        proposal = _proposal_from_legacy_external_action(task_state=task_state)

    task_state.external_operation_proposal = proposal
    if proposal is not None:
        # History records proposal occurrences; repeated digests remain audit-relevant.
        task_state.external_operation_history.append(proposal)

    # Compatibility flags are projections of the normalized proposal, not policy inputs.
    task_state.actor_result = replace(
        actor_result,
        external_operation_proposal=proposal,
        capability_mode=_compatibility_capability_mode(proposal),
        external_impact=bool(
            proposal and proposal.boundary == OperationBoundary.GOVERNED_EXTERNAL
        ),
    )
    return proposal


# Reuse only a runtime-owned canonical proposal for the actor turn being evaluated.
def _proposal_from_current_canonical_state(
    *,
    task_state: TaskState,
) -> ExternalOperationProposal | None:
    proposal = task_state.external_operation_proposal
    actor_result = task_state.actor_result
    if (
        proposal is None
        or actor_result is None
        or proposal.plan_step_id != actor_result.plan_step_id
        or actor_result.external_operation_proposal != proposal
    ):
        return None
    return proposal


# Project the existing communication draft only once it is a true execution candidate.
def _proposal_from_review_ready_communication(
    *,
    task_state: TaskState,
) -> ExternalOperationProposal | None:
    external = task_state.external_communication
    actor_result = task_state.actor_result
    if (
        external is None
        or external.draft is None
        or not external.review_ready
        or actor_result is None
    ):
        return None
    return normalize_communication_operation(
        operation_id=_operation_id(task_state=task_state, suffix="communication"),
        plan_step_id=actor_result.plan_step_id,
        capability_id=(
            "communication.email.send"
            if external.draft.channel.strip().lower() == "email"
            else f"communication.{external.draft.channel.strip().lower()}.send"
        ),
        draft=external.draft,
        execution_candidate=True,
    )


# Classify tool actions from the selected registry entry, defaulting unknowns conservatively.
def _proposal_from_tool_request(
    *,
    task_state: TaskState,
    tool_gateway_config: ToolGatewayConfig | None,
) -> ExternalOperationProposal | None:
    actor_result = task_state.actor_result
    request = task_state.current_tool_request
    if actor_result is None or request is None:
        return None

    config = tool_gateway_config or build_default_tool_gateway_config()
    definition = config.tools.get(request.tool_name)
    capability_id = request.tool_name.value
    execution_arguments = tool_request_execution_arguments(request)
    proposal = normalize_generic_external_operation(
        operation_id=_operation_id(task_state=task_state, suffix=capability_id),
        plan_step_id=actor_result.plan_step_id,
        capability_id=capability_id,
        action=capability_id,
        target=_tool_request_target(
            capability_id=capability_id,
            execution_arguments=execution_arguments,
        ),
        fields=execution_arguments,
        effect=(
            definition.normalized_effect
            if definition is not None
            else ExternalOperationEffect.UNKNOWN
        ),
        execution_candidate=True,
        capability_profile_id=(
            definition.capability_profile_id
            if definition is not None and definition.capability_profile_id
            else f"unknown.{capability_id}"
        ),
        capability_profile_version=(
            definition.capability_profile_version
            if definition is not None and definition.capability_profile_version
            else "1"
        ),
    )
    if definition is None:
        return proposal
    return replace(
        proposal,
        boundary=definition.operation_boundary,
        operation_family=definition.operation_family,
        target=replace(
            proposal.target,
            kind="public_evidence_source"
            if definition.operation_boundary == OperationBoundary.EVIDENCE_READ
            else proposal.target.kind,
        ),
        facts_provenance=replace(
            proposal.facts_provenance,
            source_kind="tool_registry",
            registry_entry_id=capability_id,
        ),
    )


# Select a bounded operational target without treating actor rationale as executable data.
def _tool_request_target(
    *,
    capability_id: str,
    execution_arguments: tuple[tuple[str, str], ...],
) -> str:
    values = dict(execution_arguments)
    for name in ("url", "query", "document_id", "dataset_id", "path"):
        value = values.get(name)
        if value:
            return value
    return capability_id


# Normalize compatibility-only legacy external signals as unknown governed operations.
def _proposal_from_legacy_external_action(
    *,
    task_state: TaskState,
) -> ExternalOperationProposal | None:
    actor_result = task_state.actor_result
    if actor_result is None or not (
        actor_result.external_impact
        or actor_result.capability_mode == CapabilityAccessMode.WRITE
    ):
        return None
    return normalize_generic_external_operation(
        operation_id=_operation_id(task_state=task_state, suffix="legacy_external"),
        plan_step_id=actor_result.plan_step_id,
        capability_id="legacy_external_action",
        action=actor_result.summary,
        target="unknown_external_target",
        fields=(("output_preview", (actor_result.output_text or "")[:1000]),),
        effect=(
            ExternalOperationEffect.UNKNOWN
            if actor_result.capability_mode != CapabilityAccessMode.WRITE
            else ExternalOperationEffect.EXECUTE
        ),
        execution_candidate=True,
    )


# Derive old actor fields from the canonical normalized proposal.
def _compatibility_capability_mode(
    proposal: ExternalOperationProposal | None,
) -> CapabilityAccessMode:
    if proposal is None or proposal.boundary == OperationBoundary.LOCAL:
        return CapabilityAccessMode.NONE
    if proposal.normalized_effect == ExternalOperationEffect.READ:
        return CapabilityAccessMode.READ_ONLY
    return CapabilityAccessMode.WRITE


# Build a stable operation identity while allowing digest changes across revisions.
def _operation_id(*, task_state: TaskState, suffix: str) -> str:
    actor_result = task_state.actor_result
    step_id = actor_result.plan_step_id if actor_result is not None else "no_step"
    thread_id = (
        task_state.runtime_thread_id
        or task_state.intake_metadata.request_id
        or "runtime"
    )
    return f"{thread_id}:{step_id}:{suffix}"


__all__ = ["prepare_external_operation_proposal"]
