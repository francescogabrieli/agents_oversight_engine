from dataclasses import replace
import re
from urllib.parse import urlparse

from ..actor_contract import ActorResult, ActorResultType, CapabilityAccessMode
from ..domain import RuntimeFlag, RuntimeState, ScratchpadEntry, ScratchpadEntryType, TaskState
from ..domain.runtime_records import ToolAttempt, ToolDisposition, ToolInvocation
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolObservation,
    ToolRequest,
    canonical_tool_request_identity,
    canonical_web_locator,
)
from .lifecycle_service import _append_step_record


# Execute a capability through a progress-aware local guard before any backend or OPA boundary.
def tool_gateway(state: TaskState, *, tool_gateway_service: ToolGatewayPort) -> TaskState:
    if state.current_state != RuntimeState.READY_TOOL_EXECUTION or state.current_tool_request is None:
        _append_step_record(state, node_name="tool_gateway", summary="Tool gateway skipped because no tool request was ready.")
        return state
    request = state.current_tool_request
    action_key = canonical_tool_request_identity(request)
    result, disposition, invoked = _guarded_tool_result(
        state=state, request=request, action_key=action_key, tool_gateway_service=tool_gateway_service
    )
    state.tool_attempts.append(ToolAttempt(action_key, request.plan_step_id, state.progress_epoch, disposition))
    if invoked:
        retryable = _is_retryable_backend_failure(result)
        state.tool_invocations.append(ToolInvocation(action_key, request.plan_step_id, state.progress_epoch, retryable))
        state.tool_call_count += 1
    state.last_tool_result = result
    state.tool_results_history.append(result)
    state.current_tool_request = None
    if disposition in {ToolDisposition.REJECTED_DUPLICATE, ToolDisposition.CACHE_HIT} and _same_epoch_repeat_count(state, action_key) >= 2:
        _request_protocol_replan(state, action_key=action_key)
    else:
        state.current_state = RuntimeState.ACTING
    _append_step_record(state, node_name="tool_gateway", summary=f"Tool gateway recorded {disposition.value} for '{request.tool_name.value}'.")
    return state


# Return cache/rejection results without allowing them to become backend invocations.
def _guarded_tool_result(*, state: TaskState, request: ToolRequest, action_key: str, tool_gateway_service: ToolGatewayPort) -> tuple[ToolExecutionResult, ToolDisposition, bool]:
    cached = _successful_result_for_action(state, action_key)
    if cached is not None:
        return ToolExecutionResult(
            request=request, success=False, summary="Cached result is already available in workspace observations.",
            content=cached.content, observations=(), error_message="Equivalent capability result was reused locally.",
            error_code=ToolErrorCode.DUPLICATE_REQUEST,
        ), ToolDisposition.CACHE_HIT, False
    if _terminal_failure_for_action(state, action_key):
        return _synthetic_result(request, "Equivalent capability failed terminally in this progress epoch.", ToolErrorCode.DUPLICATE_REQUEST), ToolDisposition.REJECTED_DUPLICATE, False
    if _retryable_failure_for_action(state, action_key):
        return _synthetic_result(request, "Equivalent capability already exhausted its local retry for this progress epoch.", ToolErrorCode.DUPLICATE_REQUEST), ToolDisposition.REJECTED_DUPLICATE, False
    if _fetch_target_is_unanchored(state=state, request=request):
        return (
            _synthetic_result(
                request,
                "The requested URL is not anchored to a task or observed locator.",
                ToolErrorCode.UNANCHORED_TARGET,
            ),
            ToolDisposition.REJECTED_UNANCHORED_TARGET,
            False,
        )
    if _circuit_open(state, request, action_key):
        return _synthetic_result(request, "Capability circuit is open after retryable backend failures.", ToolErrorCode.CIRCUIT_OPEN), ToolDisposition.REJECTED_CIRCUIT, False
    result = tool_gateway_service.execute(tool_request=request)
    if result.success:
        return result, ToolDisposition.EXECUTED_SUCCESS, True
    if _is_retryable_backend_failure(result):
        return result, ToolDisposition.EXECUTED_RETRYABLE_FAILURE, True
    return result, ToolDisposition.EXECUTED_TERMINAL_FAILURE, True


# Build a typed runtime-only result; synthetic outcomes never count as backend failures.
def _synthetic_result(request: ToolRequest, summary: str, error_code: ToolErrorCode) -> ToolExecutionResult:
    return ToolExecutionResult(request=request, success=False, summary=summary, error_message=summary, error_code=error_code)


# Identify retryability only from an executed backend response.
def _is_retryable_backend_failure(result: ToolExecutionResult) -> bool:
    # Only explicitly transient backend outcomes may consume retry budget.
    return not result.success and result.error_code in {
        ToolErrorCode.TIMEOUT,
        ToolErrorCode.UNAVAILABLE,
    }


# Count same-epoch attempts after adding the current rejected duplicate.
def _same_epoch_repeat_count(state: TaskState, action_key: str) -> int:
    return sum(1 for item in state.tool_attempts if item.action_key == action_key and item.progress_epoch == state.progress_epoch)


# Escalate local protocol churn to replanning without asking OPA to supervise technical loops.
def _request_protocol_replan(state: TaskState, *, action_key: str) -> None:
    step = state.current_plan_step
    if step is None:
        return
    state.actor_result = ActorResult(
        plan_step_id=step.step_id, result_type=ActorResultType.NEEDS_REPLAN,
        summary="Repeated capability request made no progress.",
        blocking_reason=f"Capability action '{action_key}' was repeated in progress epoch {state.progress_epoch} without a state delta.",
        capability_mode=CapabilityAccessMode.NONE,
    )
    state.current_state = RuntimeState.REPLANNING


# A cached success is valid only for the current execution epoch.
def _successful_result_for_action(state: TaskState, action_key: str) -> ToolExecutionResult | None:
    for result in reversed(state.tool_results_history):
        if result.success and canonical_tool_request_identity(result.request) == action_key:
            return result
    return None


# Terminal failures are not retried until progress changes.
def _terminal_failure_for_action(state: TaskState, action_key: str) -> bool:
    terminal_target_codes = {
        ToolErrorCode.TARGET_NOT_FOUND,
        ToolErrorCode.FORBIDDEN,
    }
    if any(
        canonical_tool_request_identity(result.request) == action_key
        and not result.success
        and result.error_code in terminal_target_codes
        for result in state.tool_results_history
    ):
        return True
    return any(
        item.action_key == action_key
        and item.progress_epoch == state.progress_epoch
        and item.disposition == ToolDisposition.EXECUTED_TERMINAL_FAILURE
        for item in state.tool_attempts
    )


# Permit one backend retryable failure, then route equivalent work to finite recovery.
def _retryable_failure_for_action(state: TaskState, action_key: str) -> bool:
    return any(
        item.action_key == action_key and item.progress_epoch == state.progress_epoch
        and item.disposition == ToolDisposition.EXECUTED_RETRYABLE_FAILURE
        for item in state.tool_attempts
    )


# The circuit counts only backend retryable failures in the current reset window.
def _circuit_open(state: TaskState, request: ToolRequest, action_key: str) -> bool:
    if request.tool_name != ToolName.FETCH_URL_TEXT:
        return False
    host = urlparse(request.url or "").netloc.casefold()
    failures = 0
    for invocation in state.tool_invocations:
        if not invocation.retryable_failure or invocation.progress_epoch != state.progress_epoch:
            continue
        if not invocation.action_key.startswith(f"{ToolName.FETCH_URL_TEXT.value}:"):
            continue
        candidate = next(
            (
                result.request
                for result in state.tool_results_history
                if canonical_tool_request_identity(result.request) == invocation.action_key
            ),
            None,
        )
        if candidate and urlparse(candidate.url or "").netloc.casefold() == host:
            failures += 1
    return failures >= 2


# Turn adapter observations into evidence and advance progress only for concrete new content.
def observe_tool_result(state: TaskState) -> TaskState:
    if state.last_tool_result is None:
        _append_step_record(state, node_name="observe_tool_result", summary="Observation skipped because no tool result exists.")
        return state
    cached = bool(state.tool_attempts and state.tool_attempts[-1].disposition == ToolDisposition.CACHE_HIT)
    observations = [] if cached else _validated_tool_observations(state=state, tool_result=state.last_tool_result)
    if not cached and state.last_tool_result.success:
        _replace_tool_result_observations(
            state=state,
            observations=tuple(observations),
        )
    if observations:
        state.observations.extend(observations)
        state.progress_epoch += 1
        state.state_revision += 1
        state.no_progress_turns = 0
    if state.last_tool_result.extractability_limited or _tool_result_indicates_extractability_limit(state.last_tool_result):
        if RuntimeFlag.EXTRACTABILITY_LIMITED not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.EXTRACTABILITY_LIMITED)
    if state.current_state != RuntimeState.REPLANNING:
        if state.pending_tool_requests:
            state.current_tool_request = state.pending_tool_requests.pop(0)
            state.current_state = RuntimeState.READY_GOVERNANCE
        else:
            state.current_state = RuntimeState.ACTING
    _append_step_record(state, node_name="observe_tool_result", summary="Tool result observations recorded without semantic classification.")
    return state


# Validate adapter-produced observations without assigning semantic quality.
def _validated_tool_observations(*, state: TaskState, tool_result: ToolExecutionResult) -> list[ToolObservation]:
    if not tool_result.success:
        _append_observation(state=state, tool_result=tool_result, evidence_refs=[])
        return []
    existing_ids = {item.observation_id for item in state.observations}
    accepted: list[ToolObservation] = []
    for observation in tool_result.observations:
        if not observation.content.strip():
            continue
        if observation.observation_id in existing_ids:
            observation = replace(observation, observation_id=_next_available_observation_id(base_id=observation.observation_id, existing_ids=existing_ids))
        accepted.append(observation)
        existing_ids.add(observation.observation_id)
    _append_observation(state=state, tool_result=tool_result, evidence_refs=[item.observation_id for item in accepted])
    return accepted


# Keep persisted tool-result lineage aligned with collision-normalized observation IDs.
def _replace_tool_result_observations(
    *,
    state: TaskState,
    observations: tuple[ToolObservation, ...],
) -> None:
    result = state.last_tool_result
    if result is None or result.observations == observations:
        return
    canonical_result = replace(result, observations=observations)
    state.last_tool_result = canonical_result
    for index in range(len(state.tool_results_history) - 1, -1, -1):
        candidate = state.tool_results_history[index]
        if candidate.request.request_id == result.request.request_id:
            state.tool_results_history[index] = canonical_result
            break


# Create a stable request-scoped suffix instead of failing the whole execution.
def _next_available_observation_id(*, base_id: str, existing_ids: set[str]) -> str:
    suffix = 2
    while f"{base_id}:normalized:{suffix}" in existing_ids:
        suffix += 1
    return f"{base_id}:normalized:{suffix}"


# Preserve extractability warnings as operational facts, never quality classifications.
def _tool_result_indicates_extractability_limit(result: ToolExecutionResult) -> bool:
    if result.request.tool_name != ToolName.FETCH_URL_TEXT:
        return False
    if result.error_code in {ToolErrorCode.FORBIDDEN, ToolErrorCode.BYTE_LIMIT, ToolErrorCode.BOT_CHALLENGE, ToolErrorCode.EMPTY_EXTRACTION, ToolErrorCode.TIMEOUT}:
        return True
    return not result.success and "empty" in " ".join(part or "" for part in (result.summary, result.error_message)).casefold()


_TASK_URL_PATTERN = re.compile(r"https?://[^\s<>\"'()]+", re.IGNORECASE)


# Enforce URL provenance before the Tool Gateway can reach a backend.
def _fetch_target_is_unanchored(*, state: TaskState, request: ToolRequest) -> bool:
    if request.tool_name != ToolName.FETCH_URL_TEXT or not request.url:
        return False
    requested_locator = canonical_web_locator(request.url)
    observed_locators = {
        canonical_web_locator(observation.locator)
        for observation in state.observations
        if observation.locator
    }
    if requested_locator in observed_locators:
        return False
    task_spec = state.task_spec
    task_text = " ".join(
        [task_spec.goal, task_spec.context, *task_spec.constraints, *task_spec.success_criteria]
    )
    return requested_locator not in {
        canonical_web_locator(match.group(0).rstrip(".,"))
        for match in _TASK_URL_PATTERN.finditer(task_text)
    }


# Add only request-local evidence references to the audit trail.
def _append_observation(*, state: TaskState, tool_result: ToolExecutionResult, evidence_refs: list[str]) -> None:
    step = state.current_plan_step
    if step is not None:
        state.scratchpad.append(ScratchpadEntry(state.actor_turn_count, step.step_id, ScratchpadEntryType.OBSERVATION, f"Observed tool result from '{tool_result.request.tool_name.value}': {tool_result.summary}", evidence_refs))


__all__ = ["observe_tool_result", "tool_gateway"]
