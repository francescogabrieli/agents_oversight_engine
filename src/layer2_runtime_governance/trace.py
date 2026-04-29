from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from layer1_intake import IntakeResult

from .actor_contract import ActorResult
from .governance import GovernanceDecision
from .oversight import (
    EvidenceQualityAssessment,
    OperationalRiskAssessment,
    OversightAssessment,
    TrajectoryHealthAssessment,
)
from .state import ExecutedPlanStep, ScratchpadEntry, StepRecord, StepValidation, TaskState
from .tool_contract import ObservedEvidence, ToolExecutionResult, ToolRequest, ToolSource


@dataclass(frozen=True)
class TraceRuntimeSummary:
    # TraceRuntimeSummary identifies the run and its terminal runtime state.
    thread_id: str
    current_state: str
    bootstrap_completed: bool
    runtime_flags: tuple[str, ...]
    step_history: tuple[dict[str, Any], ...]
    actor_turn_count: int
    tool_call_count: int


@dataclass(frozen=True)
class TraceIntakeSummary:
    # TraceIntakeSummary preserves the Layer 1 handoff in compact audit form.
    goal: str
    context: str
    domain_type: str
    domain_label: str
    sensitivity: str
    success_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    parse_status: str
    parser_name: str
    prompt_version: str
    schema_version: str
    policy_profile_name: str
    confidence_threshold: float
    max_steps: int
    max_tool_calls: int | None
    read_only_mode: bool


@dataclass(frozen=True)
class TracePlanStep:
    # TracePlanStep is one semantic planner step as seen by runtime governance.
    step_id: str
    objective: str
    rationale: str
    expected_outcome: str
    status: str


@dataclass(frozen=True)
class TraceActorTurn:
    # TraceActorTurn captures actor-facing execution without hidden reasoning.
    actor_name: str | None
    actor_prompt_version: str | None
    result_type: str | None
    plan_step_id: str | None
    summary: str | None
    capability_mode: str | None
    external_impact: bool | None
    output_text: str | None
    blocking_reason: str | None
    proposed_tool_name: str | None


@dataclass(frozen=True)
class TraceToolCall:
    # TraceToolCall records the tool request/result material retained by TaskState.
    plan_step_id: str
    tool_name: str
    reason: str
    query: str | None
    url: str | None
    text: str | None
    path: str | None
    expression: str | None
    timezone: str | None
    limit: int | None
    max_results: int
    extractive_goal: str | None
    success: bool | None
    summary: str | None
    content: str | None
    error_message: str | None
    sources: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TraceObservedEvidence:
    # TraceObservedEvidence is the grounded evidence surface visible to later actor turns.
    evidence_id: str
    plan_step_id: str
    title: str
    locator: str
    excerpt: str
    source_tool: str


@dataclass(frozen=True)
class TraceGovernanceDecision:
    # TraceGovernanceDecision makes each policy/combiner decision readable.
    directive: str
    policy_decision: str
    reason_codes: tuple[str, ...]
    summary: str
    constrains_capability_use: bool
    evaluated_actor_result_type: str
    evaluated_tool_name: str | None
    policy_engine_name: str
    policy_bundle_version: str
    policy_directive: str
    oversight_recommended_directive: str
    oversight_summary: str | None


@dataclass(frozen=True)
class TraceOversightSignal:
    # TraceOversightSignal keeps each adaptive score separate and explainable.
    score: float
    level: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class TraceOversightAssessment:
    # TraceOversightAssessment records the confidence/risk/trajectory recommendation.
    evidence_quality: TraceOversightSignal
    operational_risk: TraceOversightSignal
    trajectory_health: TraceOversightSignal
    recommended_directive: str
    summary: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class TraceExecutionSummary:
    # TraceExecutionSummary gathers final output and audit-safe execution history.
    final_output_text: str | None
    executed_steps: tuple[dict[str, Any], ...]
    scratchpad: tuple[dict[str, Any], ...]
    step_validations: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Layer2RuntimeTrace:
    # Layer2RuntimeTrace is the stable JSON-exportable audit artifact for Layer 2.
    runtime: TraceRuntimeSummary
    intake: TraceIntakeSummary
    planner_name: str | None
    planner_prompt_version: str | None
    planner_rationale: str | None
    planning_source: str | None
    plan_steps: tuple[TracePlanStep, ...]
    current_plan_step_index: int | None
    current_plan_step_id: str | None
    actor: TraceActorTurn
    execution: TraceExecutionSummary
    tool_calls: tuple[TraceToolCall, ...]
    observed_evidence: tuple[TraceObservedEvidence, ...]
    governance_decisions: tuple[TraceGovernanceDecision, ...]
    oversight_assessments: tuple[TraceOversightAssessment, ...]


# Build the stable Layer 2 trace from the canonical runtime state.
def build_layer2_runtime_trace(
    thread_id: str,
    intake_result: IntakeResult,
    task_state: TaskState,
) -> Layer2RuntimeTrace:
    return Layer2RuntimeTrace(
        runtime=_build_runtime_summary(thread_id=thread_id, task_state=task_state),
        intake=_build_intake_summary(intake_result=intake_result),
        planner_name=task_state.planner_name,
        planner_prompt_version=task_state.planner_prompt_version,
        planner_rationale=task_state.planner_rationale,
        planning_source=_resolve_planning_source(task_state.planner_name),
        plan_steps=tuple(_build_plan_step(step) for step in task_state.plan_steps),
        current_plan_step_index=task_state.current_plan_step_index,
        current_plan_step_id=(
            task_state.current_plan_step.step_id if task_state.current_plan_step is not None else None
        ),
        actor=_build_actor_turn(actor_result=task_state.actor_result, task_state=task_state),
        execution=_build_execution_summary(task_state=task_state),
        tool_calls=_build_tool_calls(task_state=task_state),
        observed_evidence=tuple(
            _build_observed_evidence(evidence) for evidence in task_state.observed_evidence
        ),
        governance_decisions=tuple(
            _build_governance_decision(decision) for decision in task_state.governance_history
        ),
        oversight_assessments=tuple(
            _build_oversight_assessment(assessment)
            for assessment in task_state.oversight_history
        ),
    )


# Convert a Layer 2 trace into plain JSON-safe Python structures.
def layer2_trace_to_dict(trace: Layer2RuntimeTrace) -> dict[str, Any]:
    return _to_jsonable(trace)


# Convert a Layer 2 trace into stable formatted JSON.
def layer2_trace_to_json(trace: Layer2RuntimeTrace) -> str:
    return json.dumps(layer2_trace_to_dict(trace), indent=2, ensure_ascii=False)


# Build the old compact trace shape from the new typed runtime trace.
def build_compact_trace_from_runtime_trace(trace: Layer2RuntimeTrace) -> dict[str, Any]:
    return {
        "runtime": {
            "thread_id": trace.runtime.thread_id,
            "demo_intake_used": trace.intake.parser_name == "demo_intake_builder",
        },
        "intake_summary": {
            "goal": trace.intake.goal,
            "domain_type": trace.intake.domain_type,
            "sensitivity": trace.intake.sensitivity,
            "parse_status": trace.intake.parse_status,
            "read_only_mode": trace.intake.read_only_mode,
        },
        "planning_snapshot": {
            "plan_step_count": len(trace.plan_steps),
            "current_plan_step_index": trace.current_plan_step_index,
            "current_plan_step": _current_step_objective(trace),
            "planner_name": trace.planner_name,
            "planning_source": trace.planning_source,
            "planner_prompt_version": trace.planner_prompt_version,
            "plan_steps": [asdict(step) for step in trace.plan_steps],
        },
        "actor_snapshot": asdict(trace.actor),
        "execution_snapshot": asdict(trace.execution),
        "governance_snapshot": {
            "governance_pass_count": len(trace.governance_decisions),
            "last_governance_decision": (
                asdict(trace.governance_decisions[-1]) if trace.governance_decisions else None
            ),
            "oversight_pass_count": len(trace.oversight_assessments),
            "last_oversight_assessment": (
                asdict(trace.oversight_assessments[-1]) if trace.oversight_assessments else None
            ),
        },
        "bootstrap_state": {
            "current_state": trace.runtime.current_state,
            "step_count": len(trace.runtime.step_history),
            "bootstrap_completed": trace.runtime.bootstrap_completed,
            "runtime_flags": list(trace.runtime.runtime_flags),
        },
        "tool_snapshot": {
            "actor_turn_count": trace.runtime.actor_turn_count,
            "tool_call_count": trace.runtime.tool_call_count,
            "current_tool_request": _compact_current_tool_request(trace.tool_calls),
            "last_tool_result": _compact_last_tool_result(trace.tool_calls),
            "observed_evidence_count": len(trace.observed_evidence),
        },
    }


# Build the run identity and terminal runtime snapshot.
def _build_runtime_summary(*, thread_id: str, task_state: TaskState) -> TraceRuntimeSummary:
    return TraceRuntimeSummary(
        thread_id=thread_id,
        current_state=task_state.current_state.value,
        bootstrap_completed=task_state.bootstrap_completed,
        runtime_flags=tuple(flag.value for flag in task_state.runtime_flags),
        step_history=tuple(_build_step_record(record) for record in task_state.step_history),
        actor_turn_count=task_state.actor_turn_count,
        tool_call_count=task_state.tool_call_count,
    )


# Build the compact Layer 1 handoff summary.
def _build_intake_summary(*, intake_result: IntakeResult) -> TraceIntakeSummary:
    task_spec = intake_result.structured_task.task_spec
    execution_policy = intake_result.structured_task.execution_policy
    return TraceIntakeSummary(
        goal=task_spec.goal,
        context=task_spec.context,
        domain_type=task_spec.domain_type.value,
        domain_label=task_spec.domain_label,
        sensitivity=task_spec.sensitivity.value,
        success_criteria=tuple(task_spec.success_criteria),
        constraints=tuple(task_spec.constraints),
        parse_status=intake_result.metadata.parse_status.value,
        parser_name=intake_result.metadata.parser_name,
        prompt_version=intake_result.metadata.prompt_version,
        schema_version=intake_result.metadata.schema_version,
        policy_profile_name=execution_policy.policy_profile_name,
        confidence_threshold=execution_policy.confidence_threshold,
        max_steps=execution_policy.budget.max_steps,
        max_tool_calls=execution_policy.budget.max_tool_calls,
        read_only_mode=execution_policy.read_only_mode,
    )


# Convert one plan step into the trace representation.
def _build_plan_step(step: Any) -> TracePlanStep:
    return TracePlanStep(
        step_id=step.step_id,
        objective=step.objective,
        rationale=step.rationale,
        expected_outcome=step.expected_outcome,
        status=step.status.value,
    )


# Convert the latest actor result into the trace representation.
def _build_actor_turn(*, actor_result: ActorResult | None, task_state: TaskState) -> TraceActorTurn:
    return TraceActorTurn(
        actor_name=task_state.actor_name,
        actor_prompt_version=task_state.actor_prompt_version,
        result_type=actor_result.result_type.value if actor_result is not None else None,
        plan_step_id=actor_result.plan_step_id if actor_result is not None else None,
        summary=actor_result.summary if actor_result is not None else None,
        capability_mode=actor_result.capability_mode.value if actor_result is not None else None,
        external_impact=actor_result.external_impact if actor_result is not None else None,
        output_text=actor_result.output_text if actor_result is not None else None,
        blocking_reason=actor_result.blocking_reason if actor_result is not None else None,
        proposed_tool_name=(
            actor_result.tool_request.tool_name.value
            if actor_result is not None and actor_result.tool_request is not None
            else None
        ),
    )


# Build final output and audit-safe execution history.
def _build_execution_summary(*, task_state: TaskState) -> TraceExecutionSummary:
    return TraceExecutionSummary(
        final_output_text=task_state.final_output_text,
        executed_steps=tuple(_build_executed_step(step) for step in task_state.executed_steps),
        scratchpad=tuple(_build_scratchpad_entry(entry) for entry in task_state.scratchpad),
        step_validations=tuple(
            _build_step_validation(validation) for validation in task_state.step_validations
        ),
    )


# Build the retained tool request/result trace from TaskState.
def _build_tool_calls(*, task_state: TaskState) -> tuple[TraceToolCall, ...]:
    calls: list[TraceToolCall] = []
    if task_state.last_tool_result is not None:
        calls.append(_build_tool_call_from_result(task_state.last_tool_result))
    elif task_state.current_tool_request is not None:
        calls.append(_build_tool_call_from_request(task_state.current_tool_request))
    return tuple(calls)


# Convert a retained tool result into trace form.
def _build_tool_call_from_result(result: ToolExecutionResult) -> TraceToolCall:
    request = result.request
    return TraceToolCall(
        **_tool_request_fields(request),
        success=result.success,
        summary=result.summary,
        content=result.content,
        error_message=result.error_message,
        sources=tuple(_build_tool_source(source) for source in result.sources),
    )


# Convert a pending/current tool request into trace form.
def _build_tool_call_from_request(request: ToolRequest) -> TraceToolCall:
    return TraceToolCall(
        **_tool_request_fields(request),
        success=None,
        summary=None,
        content=None,
        error_message=None,
        sources=(),
    )


# Convert tool request fields shared by pending and completed tool calls.
def _tool_request_fields(request: ToolRequest) -> dict[str, Any]:
    return {
        "plan_step_id": request.plan_step_id,
        "tool_name": request.tool_name.value,
        "reason": request.reason,
        "query": request.query,
        "url": request.url,
        "text": request.text,
        "path": request.path,
        "expression": request.expression,
        "timezone": request.timezone,
        "limit": request.limit,
        "max_results": request.max_results,
        "extractive_goal": request.extractive_goal,
    }


# Convert a tool source into plain provenance fields.
def _build_tool_source(source: ToolSource) -> dict[str, Any]:
    return {
        "title": source.title,
        "locator": source.locator,
        "snippet": source.snippet,
    }


# Convert one observed evidence item into the trace representation.
def _build_observed_evidence(evidence: ObservedEvidence) -> TraceObservedEvidence:
    return TraceObservedEvidence(
        evidence_id=evidence.evidence_id,
        plan_step_id=evidence.plan_step_id,
        title=evidence.title,
        locator=evidence.locator,
        excerpt=evidence.excerpt,
        source_tool=evidence.source_tool.value,
    )


# Convert one governance decision into the trace representation.
def _build_governance_decision(decision: GovernanceDecision) -> TraceGovernanceDecision:
    return TraceGovernanceDecision(
        directive=decision.directive.value,
        policy_decision=decision.policy_decision.value,
        reason_codes=tuple(code.value for code in decision.reason_codes),
        summary=decision.summary,
        constrains_capability_use=decision.constrains_capability_use,
        evaluated_actor_result_type=decision.evaluated_actor_result_type.value,
        evaluated_tool_name=(
            decision.evaluated_tool_name.value if decision.evaluated_tool_name is not None else None
        ),
        policy_engine_name=decision.policy_engine_name,
        policy_bundle_version=decision.policy_bundle_version,
        policy_directive=decision.policy_directive.value,
        oversight_recommended_directive=decision.oversight_recommended_directive.value,
        oversight_summary=decision.oversight_summary,
    )


# Convert one oversight assessment into the trace representation.
def _build_oversight_assessment(assessment: OversightAssessment) -> TraceOversightAssessment:
    return TraceOversightAssessment(
        evidence_quality=_build_oversight_signal(assessment.evidence_quality),
        operational_risk=_build_oversight_signal(assessment.operational_risk),
        trajectory_health=_build_oversight_signal(assessment.trajectory_health),
        recommended_directive=assessment.recommended_directive.value,
        summary=assessment.summary,
        reason_codes=tuple(code.value for code in assessment.reason_codes),
    )


# Convert any oversight signal score into trace form.
def _build_oversight_signal(
    signal: EvidenceQualityAssessment | OperationalRiskAssessment | TrajectoryHealthAssessment,
) -> TraceOversightSignal:
    return TraceOversightSignal(
        score=signal.score,
        level=signal.level.value,
        reason_codes=tuple(code.value for code in signal.reason_codes),
    )


# Convert one runtime step record into plain fields.
def _build_step_record(record: StepRecord) -> dict[str, Any]:
    return {
        "step_index": record.step_index,
        "runtime_state": record.runtime_state.value,
        "node_name": record.node_name,
        "summary": record.summary,
    }


# Convert one executed plan step into plain fields.
def _build_executed_step(step: ExecutedPlanStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "objective": step.objective,
        "result_type": step.result_type.value,
        "summary": step.summary,
        "output_text": step.output_text,
    }


# Convert one audit-safe scratchpad entry into plain fields.
def _build_scratchpad_entry(entry: ScratchpadEntry) -> dict[str, Any]:
    return {
        "turn_index": entry.turn_index,
        "plan_step_id": entry.plan_step_id,
        "entry_type": entry.entry_type.value,
        "summary": entry.summary,
        "evidence_refs": list(entry.evidence_refs),
    }


# Convert one step validation into plain fields.
def _build_step_validation(validation: StepValidation) -> dict[str, Any]:
    return {
        "plan_step_id": validation.plan_step_id,
        "objective_satisfied": validation.objective_satisfied,
        "evidence_sufficient": validation.evidence_sufficient,
        "confidence": validation.confidence,
        "next_action": validation.next_action.value,
        "rationale_summary": validation.rationale_summary,
        "residual_uncertainty": validation.residual_uncertainty,
    }


# Resolve whether the current plan came from the initial planner or replanner.
def _resolve_planning_source(planner_name: str | None) -> str | None:
    if planner_name is None:
        return None
    if "replanner" in planner_name:
        return "replanner"
    return "planner"


# Return the objective for the current plan step in compact trace output.
def _current_step_objective(trace: Layer2RuntimeTrace) -> str | None:
    if trace.current_plan_step_id is None:
        return None
    for step in trace.plan_steps:
        if step.step_id == trace.current_plan_step_id:
            return step.objective
    return None


# Return the compact pending/current tool request shape used by legacy CLI output.
def _compact_current_tool_request(tool_calls: tuple[TraceToolCall, ...]) -> dict[str, Any] | None:
    if not tool_calls:
        return None
    call = tool_calls[-1]
    return {
        "tool_name": call.tool_name,
        "reason": call.reason,
        "query": call.query,
        "url": call.url,
        "text": call.text,
        "path": call.path,
        "expression": call.expression,
        "timezone": call.timezone,
        "limit": call.limit,
        "max_results": call.max_results,
        "extractive_goal": call.extractive_goal,
    }


# Return the compact last tool result shape used by legacy CLI output.
def _compact_last_tool_result(tool_calls: tuple[TraceToolCall, ...]) -> dict[str, Any] | None:
    completed_calls = [call for call in tool_calls if call.success is not None]
    if not completed_calls:
        return None
    call = completed_calls[-1]
    return {
        "tool_name": call.tool_name,
        "success": call.success,
        "summary": call.summary,
        "error_message": call.error_message,
        "source_count": len(call.sources),
    }


# Convert runtime objects recursively into JSON-safe values.
def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


__all__ = [
    "Layer2RuntimeTrace",
    "TraceActorTurn",
    "TraceExecutionSummary",
    "TraceGovernanceDecision",
    "TraceIntakeSummary",
    "TraceObservedEvidence",
    "TraceOversightAssessment",
    "TracePlanStep",
    "TraceRuntimeSummary",
    "TraceToolCall",
    "build_compact_trace_from_runtime_trace",
    "build_layer2_runtime_trace",
    "layer2_trace_to_dict",
    "layer2_trace_to_json",
]
