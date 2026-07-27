from urllib.parse import urlparse

from ..domain import RuntimeFlag, RuntimeState, ScratchpadEntry, ScratchpadEntryType, TaskState
from ..tool_contract import (
    EvidenceContentState,
    EvidenceSourceKind,
    Extractability,
    ObservedEvidence,
    ToolErrorCode,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
)
from .lifecycle_service import _append_step_record


# Execute a typed read-only capability and enforce only technical budgets and host circuit state.
def tool_gateway(state: TaskState, *, tool_gateway_service: ToolGatewayPort) -> TaskState:
    if state.current_state != RuntimeState.READY_TOOL_EXECUTION or state.current_tool_request is None:
        _append_step_record(state, node_name="tool_gateway", summary="Tool gateway skipped because no tool request was ready.")
        return state
    request = state.current_tool_request
    if _tool_call_budget_exhausted(state=state):
        result = ToolExecutionResult(request=request, success=False, summary="Tool call budget exhausted.", error_message="Configured tool-call budget exhausted.", error_code=ToolErrorCode.UNAVAILABLE)
    elif _is_exact_duplicate_request(state=state, request=request):
        result = ToolExecutionResult(request=request, success=False, summary="Exact duplicate tool request was not repeated.", error_message="The same request has already been attempted for this step.", error_code=ToolErrorCode.DUPLICATE_REQUEST)
    elif _host_circuit_open(state=state, request=request):
        result = ToolExecutionResult(request=request, success=False, summary="Equivalent fetch circuit is open.", error_message="Repeated equivalent failures for this host; fetch deferred.", error_code=ToolErrorCode.CIRCUIT_OPEN, extractability_limited=True)
    else:
        result = tool_gateway_service.execute(tool_request=request)
        state.tool_call_count += 1
    state.last_tool_result = result
    state.tool_results_history.append(result)
    state.current_tool_request = None
    state.current_state = RuntimeState.ACTING
    _append_step_record(state, node_name="tool_gateway", summary=f"Tool gateway executed '{request.tool_name.value}' with typed normalization.")
    return state


# Turn normalized source observations into explicit evidence without claim-precision inference.
def observe_tool_result(state: TaskState) -> TaskState:
    if state.last_tool_result is None:
        _append_step_record(state, node_name="observe_tool_result", summary="Observation skipped because no tool result exists.")
        return state
    evidence = _build_observed_evidence_from_tool_result(state=state, tool_result=state.last_tool_result)
    state.observed_evidence.extend(evidence)
    if evidence:
        state.consecutive_no_progress_turns = 0
    if state.last_tool_result.extractability_limited or _tool_result_indicates_extractability_limit(state.last_tool_result):
        if RuntimeFlag.EXTRACTABILITY_LIMITED not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.EXTRACTABILITY_LIMITED)
    state.current_state = RuntimeState.ACTING
    _append_step_record(state, node_name="observe_tool_result", summary="Tool result observed with concrete provenance facts.")
    return state


def _tool_call_budget_exhausted(*, state: TaskState) -> bool:
    maximum = state.execution_policy.budget.max_tool_calls
    return maximum is not None and state.tool_call_count >= maximum


# Avoid re-executing the identical request only within the same semantic step.
def _is_exact_duplicate_request(*, state: TaskState, request: ToolRequest) -> bool:
    def fingerprint(candidate: ToolRequest) -> tuple[object, ...]:
        return (candidate.plan_step_id, candidate.tool_name, candidate.query, candidate.url, candidate.text, candidate.path, candidate.expression, candidate.timezone, candidate.limit, candidate.max_results, candidate.extractive_goal)

    target = fingerprint(request)
    return any(fingerprint(result.request) == target for result in state.tool_results_history)


# Open a narrow circuit only after two failed fetches on the exact host; successful or different work is unaffected.
def _host_circuit_open(*, state: TaskState, request: ToolRequest) -> bool:
    if request.tool_name != ToolName.FETCH_URL_TEXT or not request.url:
        return False
    host = urlparse(request.url).netloc.lower()
    failures = [item for item in state.tool_results_history if item.request.tool_name == ToolName.FETCH_URL_TEXT and item.request.url and urlparse(item.request.url).netloc.lower() == host and not item.success]
    return len(failures) >= 2


def _build_observed_evidence_from_tool_result(*, state: TaskState, tool_result: ToolExecutionResult) -> list[ObservedEvidence]:
    step = state.current_plan_step
    if step is None or not tool_result.success:
        _append_observation(state=state, tool_result=tool_result, evidence_refs=[])
        return []
    evidence: list[ObservedEvidence] = []
    for index, source in enumerate(tool_result.sources, start=1):
        excerpt = (source.snippet or "").strip()
        if not excerpt:
            excerpt = f"Source metadata: {source.title}."
        evidence.append(ObservedEvidence(
            evidence_id=f"{step.step_id}:tool:{state.tool_call_count}:source:{index}", plan_step_id=step.step_id,
            title=source.title, locator=source.locator, excerpt=excerpt[:1000], source_kind=EvidenceSourceKind.TOOL_OBSERVATION,
            source_tool=tool_result.request.tool_name, content_state=source.content_state, source_authority=source.source_authority,
            document_type=source.document_type, extractability=source.extractability, citation_alignment=source.citation_alignment,
        ))
    if not evidence and tool_result.content and tool_result.content.strip():
        evidence.append(ObservedEvidence(
            evidence_id=f"{step.step_id}:tool:{state.tool_call_count}:fallback", plan_step_id=step.step_id,
            title=tool_result.request.tool_name.value, locator=tool_result.request.url or tool_result.request.query or tool_result.request.tool_name.value,
            excerpt=tool_result.content[:1000], source_kind=EvidenceSourceKind.TOOL_OBSERVATION, source_tool=tool_result.request.tool_name,
            content_state=EvidenceContentState.EXTRACTED_PARTIAL if tool_result.truncated else EvidenceContentState.EXTRACTED_FULL,
            extractability=Extractability.TRUNCATED if tool_result.truncated else Extractability.COMPLETE,
        ))
    _append_observation(state=state, tool_result=tool_result, evidence_refs=[item.evidence_id for item in evidence])
    return evidence


def _tool_result_indicates_extractability_limit(result: ToolExecutionResult) -> bool:
    if result.request.tool_name != ToolName.FETCH_URL_TEXT:
        return False
    if result.error_code in {ToolErrorCode.FORBIDDEN, ToolErrorCode.BYTE_LIMIT, ToolErrorCode.BOT_CHALLENGE, ToolErrorCode.EMPTY_EXTRACTION, ToolErrorCode.TIMEOUT, ToolErrorCode.CIRCUIT_OPEN}:
        return True
    return not result.success and "empty" in " ".join(part or "" for part in (result.summary, result.error_message)).lower()


def _append_observation(*, state: TaskState, tool_result: ToolExecutionResult, evidence_refs: list[str]) -> None:
    step = state.current_plan_step
    if step is None:
        return
    state.scratchpad.append(ScratchpadEntry(turn_index=state.actor_turn_count, plan_step_id=step.step_id, entry_type=ScratchpadEntryType.OBSERVATION, summary=f"Observed tool result from '{tool_result.request.tool_name.value}': {tool_result.summary}", evidence_refs=evidence_refs))


__all__ = ["tool_gateway", "observe_tool_result", "_tool_call_budget_exhausted", "_is_exact_duplicate_request", "_host_circuit_open", "_build_observed_evidence_from_tool_result", "_tool_result_indicates_extractability_limit"]
