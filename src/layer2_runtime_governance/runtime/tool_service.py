from ..domain import (
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    TaskState,
)
from ..tool_contract import (
    EvidenceSourceKind,
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
)

# Nodes.py contains the executable Layer 2 business logic.
# At this stage the runtime performs bootstrap, planning, and the actor/tool loop.
from .lifecycle_service import _append_step_record

# Enforce governed tool execution and evidence observation invariants.
def tool_gateway(state: TaskState, *, tool_gateway_service: ToolGatewayPort) -> TaskState:
    if state.current_state != RuntimeState.READY_TOOL_EXECUTION or state.current_tool_request is None:
        _append_step_record(
            state,
            node_name="tool_gateway",
            summary="Tool gateway skipped because no tool request was ready for execution.",
        )
        return state

    # Convert budget exhaustion into a typed failed tool result instead of crashing the loop.
    if _tool_call_budget_exhausted(state=state):
        state.last_tool_result = ToolExecutionResult(
            request=state.current_tool_request,
            success=False,
            summary="Tool call budget exhausted before executing the requested tool.",
            error_message="The runtime exhausted the configured tool call budget.",
        )
        state.tool_results_history.append(state.last_tool_result)
        state.current_tool_request = None
        state.current_state = RuntimeState.ACTING

        _append_step_record(
            state,
            node_name="tool_gateway",
            summary="Tool gateway did not execute the request because the tool budget was exhausted.",
        )
        return state

    duplicate_error = _duplicate_tool_request_error(state=state)
    if duplicate_error is not None:
        state.last_tool_result = ToolExecutionResult(
            request=state.current_tool_request,
            success=False,
            summary="Duplicate tool request blocked before execution.",
            error_message=duplicate_error,
        )
        state.tool_results_history.append(state.last_tool_result)
        state.current_tool_request = None
        state.current_state = RuntimeState.ACTING

        _append_step_record(
            state,
            node_name="tool_gateway",
            summary="Tool gateway blocked a duplicate request for the current plan step.",
        )
        return state

    tool_result = tool_gateway_service.execute(tool_request=state.current_tool_request)
    state.last_tool_result = tool_result
    state.tool_results_history.append(tool_result)
    state.tool_call_count += 1
    state.current_tool_request = None
    state.current_state = RuntimeState.ACTING

    _append_step_record(
        state,
        node_name="tool_gateway",
        summary=f"Tool gateway executed '{tool_result.request.tool_name.value}'.",
    )
    return state

# Enforce governed tool execution and evidence observation invariants.
def observe_tool_result(state: TaskState) -> TaskState:
    if state.last_tool_result is None:
        _append_step_record(
            state,
            node_name="observe_tool_result",
            summary="Observation skipped because there was no tool result to observe.",
        )
        return state

    # Observation should preserve the failed result too, so the actor can react to it explicitly.
    new_evidence = _build_observed_evidence_from_tool_result(
        state=state,
        tool_result=state.last_tool_result,
    )
    state.observed_evidence.extend(new_evidence)

    # Promote repeated empty URL extraction failures into an explicit runtime flag.
    if _tool_result_indicates_extractability_limit(state.last_tool_result):
        if RuntimeFlag.EXTRACTABILITY_LIMITED not in state.runtime_flags:
            state.runtime_flags.append(RuntimeFlag.EXTRACTABILITY_LIMITED)
    state.current_state = RuntimeState.ACTING

    _append_step_record(
        state,
        node_name="observe_tool_result",
        summary="Tool result observed and converted into explicit runtime evidence.",
    )
    return state

# Enforce governed tool execution and evidence observation invariants.
def _tool_call_budget_exhausted(*, state: TaskState) -> bool:
    max_tool_calls = state.execution_policy.budget.max_tool_calls
    if max_tool_calls is None:
        return False

    return state.tool_call_count >= max_tool_calls

# Enforce governed tool execution and evidence observation invariants.
def _duplicate_tool_request_error(*, state: TaskState) -> str | None:
    request = state.current_tool_request
    if request is None:
        return None

    fingerprint = _tool_request_fingerprint(request)
    if fingerprint is None:
        return None

    for prior_result in state.tool_results_history:
        prior_request = prior_result.request
        if prior_request.plan_step_id != request.plan_step_id:
            continue
        if _tool_request_fingerprint(prior_request) == fingerprint:
            return (
                "The same tool request was already executed for this plan step; "
                "the actor must use existing evidence, choose a different source, or finish/replan."
            )
    return None

# Enforce governed tool execution and evidence observation invariants.
def _tool_request_fingerprint(tool_request: ToolRequest) -> tuple[str, str] | None:
    if tool_request.url:
        return (tool_request.tool_name.value, tool_request.url.strip().lower())
    if tool_request.query:
        return (tool_request.tool_name.value, tool_request.query.strip().lower())
    return None

# Enforce governed tool execution and evidence observation invariants.
def _build_observed_evidence_from_tool_result(
    *,
    state: TaskState,
    tool_result: ToolExecutionResult,
) -> list[ObservedEvidence]:
    current_step = state.current_plan_step
    if current_step is None:
        return []

    evidence_items: list[ObservedEvidence] = []
    if not tool_result.success:
        state.scratchpad.append(
            _build_observation_scratchpad_entry(
                state=state,
                tool_result=tool_result,
                evidence_refs=[],
            )
        )
        return evidence_items

    # Preserve source-level provenance even when search backends return URL metadata without snippets.
    for index, source in enumerate(tool_result.sources, start=1):
        excerpt = source.snippet
        if not _is_meaningful_evidence_excerpt(excerpt) and source.locator.strip():
            excerpt = f"Discovered source metadata: {source.title}. Content has not been extracted."
        if not _is_meaningful_evidence_excerpt(excerpt) or not source.locator.strip():
            continue
        support_level = (
            EvidenceSupportLevel.SOURCE_DISCOVERY
            if not _is_meaningful_evidence_excerpt(source.snippet)
            else source.support_level_hint
            if source.support_level_hint is not None
            else _classify_observed_evidence_support_level(
                tool_name=tool_result.request.tool_name,
                locator=source.locator,
                title=source.title,
                excerpt=source.snippet or "",
            )
        )
        evidence_items.append(
            ObservedEvidence(
                evidence_id=f"{current_step.step_id}:tool:{state.tool_call_count}:source:{index}",
                plan_step_id=current_step.step_id,
                title=source.title,
                locator=source.locator,
                excerpt=(excerpt or "")[:1000],
                source_kind=EvidenceSourceKind.TOOL_OBSERVATION,
                source_tool=tool_result.request.tool_name,
                support_level=support_level,
            )
        )

    # Keep a bounded fallback only when the backend returned no usable source metadata.
    if not evidence_items:
        locator = tool_result.request.url or tool_result.request.query or tool_result.request.tool_name.value
        excerpt = tool_result.content or tool_result.error_message or tool_result.summary
        if not _is_meaningful_evidence_excerpt(excerpt):
            state.scratchpad.append(
                _build_observation_scratchpad_entry(
                    state=state,
                    tool_result=tool_result,
                    evidence_refs=[],
                )
            )
            return evidence_items
        evidence_items.append(
            ObservedEvidence(
                evidence_id=f"{current_step.step_id}:tool:{state.tool_call_count}:fallback",
                plan_step_id=current_step.step_id,
                title=tool_result.request.tool_name.value,
                locator=locator,
                excerpt=(excerpt or "")[:1000],
                source_kind=EvidenceSourceKind.TOOL_OBSERVATION,
                source_tool=tool_result.request.tool_name,
                support_level=(
                    EvidenceSupportLevel.SOURCE_DISCOVERY
                    if tool_result.request.tool_name == ToolName.WEB_SEARCH
                    else _classify_observed_evidence_support_level(
                        tool_name=tool_result.request.tool_name,
                        locator=locator,
                        title=tool_result.request.tool_name.value,
                        excerpt=excerpt or "",
                    )
                ),
            )
        )

    state.scratchpad.append(
        _build_observation_scratchpad_entry(
            state=state,
            tool_result=tool_result,
            evidence_refs=[item.evidence_id for item in evidence_items],
        )
    )

    return evidence_items

# Enforce governed tool execution and evidence observation invariants.
def _is_meaningful_evidence_excerpt(value: str | None) -> bool:
    if value is None:
        return False
    normalized = " ".join(value.split())
    if not normalized:
        return False

    lowered = normalized.lower()
    if lowered in {
        "url text fetched successfully.",
        "web search executed successfully.",
        "tool succeeded.",
    }:
        return False

    word_count = len(normalized.split())
    if word_count >= 8:
        return True

    if "http://" in lowered or "https://" in lowered:
        return True

    if any(
        marker in lowered
        for marker in (
            "official source",
            "official sources",
            "source shortlist",
            "portal",
            "legislation page",
            "purpose",
            "scope",
            "overview",
            "summary",
            "obligation",
            "criteria",
            "threshold",
            "deadline",
            "article ",
            "must ",
            "shall ",
            "required to",
            "interoperability",
            "data portability",
            "entered into force",
            "applies from",
        )
    ):
        return len(normalized) >= 30

    return len(normalized) >= 80

# Enforce governed tool execution and evidence observation invariants.
def _tool_result_indicates_extractability_limit(tool_result: ToolExecutionResult) -> bool:
    if tool_result.request.tool_name != ToolName.FETCH_URL_TEXT:
        return False
    if tool_result.success:
        return False

    failure_text = " ".join(
        part.strip().lower()
        for part in (tool_result.summary, tool_result.error_message)
        if part and part.strip()
    )
    return "extracted readable text was empty" in failure_text

# Enforce governed tool execution and evidence observation invariants.
def _classify_observed_evidence_support_level(
    *,
    tool_name: ToolName,
    locator: str,
    title: str,
    excerpt: str,
) -> EvidenceSupportLevel:
    lowered_locator = locator.lower()
    lowered_title = title.lower()
    lowered_excerpt = excerpt.lower()
    timeline_markers = (
        "entered into force",
        "comes into force",
        "coming into force",
        "effective date",
        "effective from",
        "applies from",
        "applies on",
        "entry into force",
        "adopted on",
        "published on",
        "implementation date",
    )

    if any(marker in lowered_excerpt for marker in timeline_markers):
        # This branch is reached only for a source-level snippet or fetched content.
        if tool_name in {ToolName.WEB_SEARCH, ToolName.FETCH_URL_TEXT}:
            return EvidenceSupportLevel.DETAILED_EXTRACTED

    if tool_name == ToolName.WEB_SEARCH:
        high_level_markers = (
            "official pages",
            "official website",
            "overview",
            "what the ",
            "purpose",
            "core platform services",
            "obligations",
            "prohibitions",
            "legislation",
            "website",
            "summary",
            "aim",
        )
        if any(marker in lowered_excerpt for marker in high_level_markers):
            return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        if lowered_title == tool_name.value:
            return EvidenceSupportLevel.SOURCE_DISCOVERY
        if len(" ".join(excerpt.split())) >= 40:
            return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if tool_name != ToolName.FETCH_URL_TEXT:
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_locator
        for marker in (
            "/summary/",
            "legislation_en",
            "index_en",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    detailed_markers = (
        "article ",
        "articles ",
        "must ",
        "shall ",
        "required to",
        "prohibited",
        "obligations",
        "prohibitions",
        "criteria",
        "threshold",
        "deadline",
        "interoperability",
        "data portability",
    )
    if sum(1 for marker in detailed_markers if marker in lowered_excerpt) >= 2:
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if "regulation" in lowered_title and "summary" not in lowered_title:
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

# Enforce governed tool execution and evidence observation invariants.
def _build_observation_scratchpad_entry(
    *,
    state: TaskState,
    tool_result: ToolExecutionResult,
    evidence_refs: list[str],
) -> ScratchpadEntry:
    current_step = state.current_plan_step
    if current_step is None:
        raise ValueError("Cannot create observation scratchpad entry without a current plan step.")

    return ScratchpadEntry(
        turn_index=state.actor_turn_count,
        plan_step_id=current_step.step_id,
        entry_type=ScratchpadEntryType.OBSERVATION,
        summary=f"Observed tool result from '{tool_result.request.tool_name.value}': {tool_result.summary}",
        evidence_refs=evidence_refs,
    )

__all__ = [
    "tool_gateway",
    "observe_tool_result",
    "_tool_call_budget_exhausted",
    "_duplicate_tool_request_error",
    "_tool_request_fingerprint",
    "_build_observed_evidence_from_tool_result",
    "_is_meaningful_evidence_excerpt",
    "_tool_result_indicates_extractability_limit",
    "_classify_observed_evidence_support_level",
    "_build_observation_scratchpad_entry"
]
