from __future__ import annotations

import argparse
from copy import deepcopy
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from langgraph.types import Command

from console_output import print_json_section as _print_section
from layer1_intake import (
    ExecutionPolicy,
    IntakeCommand,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer1_intake.infrastructure import build_intake_service
from layer2_runtime_governance import HumanReviewDecision, build_default_checkpointer
from layer2_runtime_governance.actor_contract import ActorPort
from layer2_runtime_governance.composition import (
    Layer2RuntimeDependencies,
    compose_layer2_runtime,
)
from layer2_runtime_governance.domain import RuntimeState, TaskState, TerminationReason
from layer2_runtime_governance.governance_components import PolicyEnginePort
from layer2_runtime_governance.hitl.decision_inbox import (
    ReviewDecisionInboxPort,
    ReviewDecisionSubmission,
)
from layer2_runtime_governance.judge import JudgePort
from layer2_runtime_governance.planning import PlannerPort
from layer2_runtime_governance.planning.replanning import ReplannerPort
from layer3_tool_bus_external_capabilities.tool_contract import ToolGatewayPort
from layer4_state_evidence_observability.behavioral import BehavioralStorePort
from layer4_state_evidence_observability.infrastructure.checkpoint_retention import (
    prune_expired_checkpoint_threads,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    close_checkpointer,
)
from layer4_state_evidence_observability.infrastructure.review_decision_inbox import (
    SQLiteReviewDecisionInbox,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
    persist_runtime_artifacts,
)
from layer4_state_evidence_observability.observability import (
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    build_trace_summary_markdown,
    layer2_trace_to_json,
)


class IntakeExecutorPort(Protocol):
    # This port lets tests inject a deterministic intake service into the Layer 2 runner.
    def execute(self, command: IntakeCommand) -> IntakeResult: ...


@dataclass(frozen=True)
class _Layer2RuntimeSession:
    runtime_graph: Any
    checkpointer: Any
    artifact_store: RuntimeArtifactStore


# Compose one scoped runtime session and release only infrastructure created here.
@contextmanager
def _open_layer2_runtime_session(
    *,
    planner_service: PlannerPort | None,
    actor_service: ActorPort | None,
    tool_gateway_service: ToolGatewayPort | None,
    replanner_service: ReplannerPort | None,
    policy_engine_service: PolicyEnginePort | None,
    judge_service: JudgePort | None,
    behavioral_store_service: BehavioralStorePort | None,
    checkpointer: Any | None,
    artifact_store: RuntimeArtifactStore | None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
) -> Iterator[_Layer2RuntimeSession]:
    uses_default_checkpointer = checkpointer is None
    resolved_artifact_store = (
        artifact_store if artifact_store is not None else RuntimeArtifactStore()
    )
    resolved_checkpointer = (
        checkpointer if checkpointer is not None else build_default_checkpointer()
    )
    try:
        runtime_graph = compose_layer2_runtime(
            Layer2RuntimeDependencies(
                planner=planner_service,
                actor=actor_service,
                tool_gateway=tool_gateway_service,
                replanner=replanner_service,
                policy_engine=policy_engine_service,
                judge=judge_service,
                behavioral_store=behavioral_store_service,
                checkpointer=resolved_checkpointer,
                hitl_event_callback=hitl_event_callback,
            )
        )
        yield _Layer2RuntimeSession(
            runtime_graph=runtime_graph,
            checkpointer=resolved_checkpointer,
            artifact_store=resolved_artifact_store,
        )
    finally:
        if uses_default_checkpointer:
            _prune_expired_checkpoints(
                checkpointer=resolved_checkpointer,
                artifact_store=resolved_artifact_store,
            )
            close_checkpointer(resolved_checkpointer)


def run_layer2_flow(
    *,
    raw_task_text: str,
    context_hint: str | None = None,
    policy_profile: str | None = None,
    thread_id: str | None = None,
    demo_intake_profile: str | None = None,
    intake_service: IntakeExecutorPort | None = None,
    planner_service: PlannerPort | None = None,
    actor_service: ActorPort | None = None,
    tool_gateway_service: ToolGatewayPort | None = None,
    replanner_service: ReplannerPort | None = None,
    policy_engine_service: PolicyEnginePort | None = None,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    checkpointer: Any | None = None,
    artifact_store: RuntimeArtifactStore | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
    live_trace: bool = False,
) -> dict[str, Any]:
    # Run Layer 1 plus the current Layer 2 graph and return the resulting trace.
    with _open_layer2_runtime_session(
        planner_service=planner_service,
        actor_service=actor_service,
        tool_gateway_service=tool_gateway_service,
        replanner_service=replanner_service,
        policy_engine_service=policy_engine_service,
        judge_service=judge_service,
        behavioral_store_service=behavioral_store_service,
        checkpointer=checkpointer,
        artifact_store=artifact_store,
        hitl_event_callback=hitl_event_callback,
    ) as session:
        resolved_thread_id = thread_id or f"layer2-{uuid4()}"
        config = {"configurable": {"thread_id": resolved_thread_id}}
        _assert_thread_can_bootstrap(runtime_graph=session.runtime_graph, config=config)

        if demo_intake_profile is not None and intake_service is not None:
            raise ValueError(
                "demo_intake_profile and intake_service cannot be used together in run_layer2_flow."
            )

        if demo_intake_profile is not None:
            if progress_callback is not None:
                progress_callback("layer1_demo_intake", {})
            intake_result = _build_demo_intake_result(
                profile=demo_intake_profile,
                raw_task_text=raw_task_text,
                context_hint=context_hint,
            )
        else:
            if progress_callback is not None:
                progress_callback("layer1_intake", {})
            resolved_intake_service = intake_service or build_intake_service()
            intake_result = resolved_intake_service.execute(
                IntakeCommand(
                    raw_task_text=raw_task_text,
                    context_hint=context_hint,
                    policy_profile=policy_profile,
                )
            )

        graph_input = {"intake_result": intake_result, "thread_id": resolved_thread_id}
        try:
            with session.artifact_store.capture_governance_exchange(
                thread_id=resolved_thread_id
            ):
                if progress_callback is None:
                    session.runtime_graph.invoke(graph_input, config=config)
                    final_result = _build_final_result_from_graph_snapshot(
                        runtime_graph=session.runtime_graph,
                        config=config,
                    )
                else:
                    final_result = _stream_runtime_graph_with_progress(
                        runtime_graph=session.runtime_graph,
                        graph_input=graph_input,
                        thread_id=resolved_thread_id,
                        config=config,
                        progress_callback=progress_callback,
                        live_trace=live_trace,
                        trace_tag="chunk",
                    )

            # Return both layer boundaries so callers can inspect the handoff end to end.
            trace = {
                "thread_id": resolved_thread_id,
                "intake_result": intake_result,
                "final_result": final_result,
            }
            _persist_runtime_artifacts_from_trace(
                trace,
                artifact_store=session.artifact_store,
            )
            return trace
        except Exception as exc:
            _persist_failed_runtime_snapshot(
                thread_id=resolved_thread_id,
                intake_result=intake_result,
                runtime_graph=session.runtime_graph,
                config=config,
                artifact_store=session.artifact_store,
                error=exc,
            )
            raise


# Resume a paused Layer 2 run using the same thread id and a typed human decision.
def resume_layer2_flow(
    *,
    thread_id: str,
    human_review_decision: dict[str, Any] | HumanReviewDecision,
    planner_service: PlannerPort | None = None,
    actor_service: ActorPort | None = None,
    tool_gateway_service: ToolGatewayPort | None = None,
    replanner_service: ReplannerPort | None = None,
    policy_engine_service: PolicyEnginePort | None = None,
    judge_service: JudgePort | None = None,
    behavioral_store_service: BehavioralStorePort | None = None,
    checkpointer: Any | None = None,
    artifact_store: RuntimeArtifactStore | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    live_trace: bool = False,
) -> dict[str, Any]:
    with _open_layer2_runtime_session(
        planner_service=planner_service,
        actor_service=actor_service,
        tool_gateway_service=tool_gateway_service,
        replanner_service=replanner_service,
        policy_engine_service=policy_engine_service,
        judge_service=judge_service,
        behavioral_store_service=behavioral_store_service,
        checkpointer=checkpointer,
        artifact_store=artifact_store,
    ) as session:
        config = {"configurable": {"thread_id": thread_id}}
        graph_input = Command(resume=human_review_decision)
        try:
            with session.artifact_store.capture_governance_exchange(thread_id=thread_id):
                # Stream owner-side resume updates when the calling terminal requested progress.
                if progress_callback is None:
                    resume_result = session.runtime_graph.invoke(graph_input, config=config)
                else:
                    resume_result = _stream_runtime_graph_with_progress(
                        runtime_graph=session.runtime_graph,
                        graph_input=graph_input,
                        thread_id=thread_id,
                        config=config,
                        progress_callback=progress_callback,
                        live_trace=live_trace,
                        trace_tag="resume_chunk",
                    )
            snapshot_result = _build_final_result_from_graph_snapshot(
                runtime_graph=session.runtime_graph,
                config=config,
            )
            final_result = _resolve_resume_final_result(
                resume_result=resume_result,
                snapshot_result=snapshot_result,
            )
            intake_result = _load_intake_result_from_final_result(final_result)
            if intake_result is None:
                intake_result = _load_intake_result_from_checkpoint(
                    thread_id=thread_id,
                    runtime_graph=session.runtime_graph,
                )
            trace = {
                "thread_id": thread_id,
                "final_result": final_result,
            }
            if intake_result is not None:
                trace["intake_result"] = intake_result
                _persist_runtime_artifacts_from_trace(
                    trace,
                    artifact_store=session.artifact_store,
                )
            return trace
        except Exception as exc:
            _persist_failed_runtime_snapshot(
                thread_id=thread_id,
                intake_result=None,
                runtime_graph=session.runtime_graph,
                config=config,
                artifact_store=session.artifact_store,
                error=exc,
            )
            raise


# Keep checkpoint state authoritative: a bootstrap must never overwrite a live or paused thread.
def _assert_thread_can_bootstrap(*, runtime_graph: Any, config: dict[str, Any]) -> None:
    snapshot = runtime_graph.get_state(config)
    values = snapshot.values if snapshot is not None else {}
    task_state = values.get("task_state") if isinstance(values, dict) else None
    if not isinstance(task_state, TaskState):
        return
    thread_id = config["configurable"]["thread_id"]
    if task_state.current_state == RuntimeState.WAITING_HUMAN_REVIEW:
        raise ValueError(
            f"Thread '{thread_id}' is waiting for human review; use dashboard/resume_layer2_flow instead of a new bootstrap."
        )
    if task_state.current_state in {
        RuntimeState.COMPLETED,
        RuntimeState.ABORTED,
    }:
        raise ValueError(
            f"Thread '{thread_id}' is terminal; use a new --thread-id for a new task."
        )
    raise ValueError(
        f"Thread '{thread_id}' is already active; use dashboard/resume_layer2_flow or a new --thread-id."
    )


# Keep retention maintenance non-blocking for the live task result.
def _prune_expired_checkpoints(
    *,
    checkpointer: Any,
    artifact_store: RuntimeArtifactStore,
) -> None:
    try:
        prune_expired_checkpoint_threads(
            checkpointer=checkpointer,
            artifact_store=artifact_store,
        )
    except Exception:  # noqa: BLE001 - retention must not mask the live result.
        return


# Reuse the canonical invoke output when it contains the typed runtime state.
def _resolve_resume_final_result(
    *,
    resume_result: Any,
    snapshot_result: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(resume_result, dict):
        resolved = dict(resume_result)
        if "__interrupt__" not in resolved and "__interrupt__" in snapshot_result:
            resolved["__interrupt__"] = snapshot_result["__interrupt__"]
        if isinstance(resolved.get("task_state"), TaskState):
            return resolved
    return snapshot_result


# Stream graph updates so the CLI can show progress during long LLM/tool calls.
def _stream_runtime_graph_with_progress(
    *,
    runtime_graph: Any,
    graph_input: dict[str, Any] | Command[Any],
    thread_id: str,
    config: dict[str, Any],
    progress_callback: Callable[[str, dict[str, Any]], None],
    live_trace: bool,
    trace_tag: str,
) -> dict[str, Any]:
    final_result: dict[str, Any] = {}
    for chunk in runtime_graph.stream(
        graph_input,
        config=config,
        stream_mode="updates",
    ):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            node_update = update if isinstance(update, dict) else {}
            progress_callback(str(node_name), node_update)
            final_result.update(node_update)
        if live_trace:
            _print_live_trace_snapshot(
                runtime_graph=runtime_graph,
                config=config,
                thread_id=thread_id,
                tag=trace_tag,
            )

    snapshot = runtime_graph.get_state(config)
    snapshot_values = snapshot.values if snapshot is not None else {}
    if isinstance(snapshot_values, dict):
        final_result = dict(snapshot_values)
    if snapshot is not None and getattr(snapshot, "interrupts", None):
        final_result["__interrupt__"] = list(snapshot.interrupts)

    return final_result


# Print the current full trace snapshot during streaming so the terminal can follow the run live.
def _print_live_trace_snapshot(
    *,
    runtime_graph: Any,
    config: dict[str, Any],
    thread_id: str,
    tag: str,
) -> None:
    snapshot = runtime_graph.get_state(config)
    if snapshot is None:
        return
    snapshot_values = snapshot.values if snapshot is not None else {}
    if not isinstance(snapshot_values, dict):
        return
    task_state = snapshot_values.get("task_state")
    intake_result = snapshot_values.get("intake_result")
    if task_state is None or intake_result is None:
        return

    runtime_trace = build_layer2_runtime_trace(
        thread_id=thread_id,
        intake_result=intake_result,
        task_state=task_state,
    )
    print(f"\n[Layer 2][trace:{tag}]", file=sys.stderr, flush=True)
    print(layer2_trace_to_json(runtime_trace), file=sys.stderr, flush=True)


# Read the post-run graph snapshot so invoke and stream paths share the same final-state contract.
def _build_final_result_from_graph_snapshot(
    *,
    runtime_graph: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    snapshot = runtime_graph.get_state(config)
    snapshot_values = snapshot.values if snapshot is not None else {}
    final_result = dict(snapshot_values) if isinstance(snapshot_values, dict) else {}
    if snapshot is not None and getattr(snapshot, "interrupts", None):
        final_result["__interrupt__"] = list(snapshot.interrupts)
    return final_result


def build_compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    # Preserve the legacy compact trace API while delegating to the typed exporter.
    intake_result = trace["intake_result"]
    final_result = trace["final_result"]
    final_task_state = final_result.get("task_state")

    if final_task_state is None:
        raise ValueError("Cannot build Layer 2 trace without final task_state.")

    runtime_trace = build_layer2_runtime_trace(
        thread_id=trace["thread_id"],
        intake_result=intake_result,
        task_state=final_task_state,
    )
    return build_compact_trace_from_runtime_trace(runtime_trace)


# Print compact progress updates while preserving stdout for final traces.
def _print_progress(node_name: str, update: dict[str, Any]) -> None:
    task_state = update.get("task_state")
    details = _format_task_state_progress(task_state, node_name=node_name)
    suffix = f" | {details}" if details else ""
    print(f"[Layer 2] {node_name}{suffix}", file=sys.stderr, flush=True)


# Summarize only audit-safe state fields during streaming progress.
def _format_task_state_progress(task_state: Any, node_name: str = "") -> str:
    if task_state is None:
        return ""

    current_state = getattr(task_state, "current_state", None)
    actor_turn_count = getattr(task_state, "actor_turn_count", None)
    tool_call_count = getattr(task_state, "tool_call_count", None)
    current_plan_step = getattr(task_state, "current_plan_step", None)
    plan_step_id = getattr(current_plan_step, "step_id", None)

    parts: list[str] = []
    if current_state is not None:
        parts.append(f"state={getattr(current_state, 'value', current_state)}")
    if plan_step_id:
        parts.append(f"step={plan_step_id}")
    if actor_turn_count is not None:
        parts.append(f"actor_turns={actor_turn_count}")
    if tool_call_count is not None:
        parts.append(f"tool_calls={tool_call_count}")
    if node_name == "planner":
        plan_steps = getattr(task_state, "plan_steps", None)
        if plan_steps is not None:
            parts.append(f"steps={len(plan_steps)}")
    return ", ".join(parts)


def print_layer2_summary(trace: dict[str, Any]) -> None:
    # Print the compact runtime trace used by the current Layer 2 CLI.
    compact_trace = build_compact_trace(trace)
    _print_section("RUNTIME", compact_trace["runtime"])
    _print_section("INTAKE SUMMARY", compact_trace["intake_summary"])
    _print_section("PLANNING SNAPSHOT", compact_trace["planning_snapshot"])
    _print_section("ACTOR SNAPSHOT", compact_trace["actor_snapshot"])
    _print_section("EXECUTION SNAPSHOT", compact_trace["execution_snapshot"])
    _print_section("GOVERNANCE SNAPSHOT", compact_trace["governance_snapshot"])
    _print_section("HITL SNAPSHOT", compact_trace["human_review_snapshot"])
    _print_section("TOOL SNAPSHOT", compact_trace["tool_snapshot"])
    _print_section("BOOTSTRAP STATE", compact_trace["bootstrap_state"])


def print_layer2_trace(trace: dict[str, Any]) -> None:
    # Print the typed full runtime trace when compact output hides too much detail.
    runtime_trace = _build_runtime_trace(trace)
    print(layer2_trace_to_json(runtime_trace))


# Write the typed full runtime trace to disk for later inspection and thesis evidence.
def write_layer2_trace_file(
    trace: dict[str, Any],
    *,
    output_path: str | Path,
) -> Path:
    runtime_trace = _build_runtime_trace(trace)
    resolved_output_path = Path(output_path)
    resolved_output_path.write_text(layer2_trace_to_json(runtime_trace), encoding="utf-8")
    summary_path = resolved_output_path.with_suffix(".summary.md")
    summary_path.write_text(build_trace_summary_markdown(runtime_trace), encoding="utf-8")
    return resolved_output_path


# Build the canonical typed runtime trace once so printing and file export stay aligned.
def _build_runtime_trace(trace: dict[str, Any]) -> Any:
    final_task_state = trace["final_result"].get("task_state")
    if final_task_state is None:
        raise ValueError("Cannot build Layer 2 trace without final task_state.")

    return build_layer2_runtime_trace(
        thread_id=trace["thread_id"],
        intake_result=trace["intake_result"],
        task_state=final_task_state,
    )


# Persist the canonical runtime artifacts whenever a full task_state is available.
def _persist_runtime_artifacts_from_trace(
    trace: dict[str, Any],
    *,
    artifact_store: RuntimeArtifactStore | None,
) -> None:
    intake_result = trace.get("intake_result")
    final_result = trace.get("final_result", {})
    task_state = final_result.get("task_state") if isinstance(final_result, dict) else None
    if not isinstance(intake_result, IntakeResult):
        return
    if not isinstance(task_state, TaskState):
        return
    persist_runtime_artifacts(
        thread_id=trace["thread_id"],
        intake_result=intake_result,
        task_state=task_state,
        artifact_store=artifact_store,
    )


# Preserve the latest canonical checkpoint plus one explicit failure marker.
def _persist_failed_runtime_snapshot(
    *,
    thread_id: str,
    intake_result: IntakeResult | None,
    runtime_graph: Any,
    config: dict[str, Any],
    artifact_store: RuntimeArtifactStore,
    error: Exception,
) -> None:
    task_state: TaskState | None = None
    resolved_intake_result = intake_result
    try:
        snapshot_result = _build_final_result_from_graph_snapshot(
            runtime_graph=runtime_graph,
            config=config,
        )
        candidate = snapshot_result.get("task_state")
        if isinstance(candidate, TaskState):
            task_state = deepcopy(candidate)
            task_state.termination_reason = TerminationReason.RUNTIME_FAILURE
            if resolved_intake_result is None:
                resolved_intake_result = candidate.intake_payload
            if isinstance(resolved_intake_result, IntakeResult):
                failure_snapshot = dict(snapshot_result)
                failure_snapshot["task_state"] = task_state
                _persist_runtime_artifacts_from_trace(
                    {
                        "thread_id": thread_id,
                        "intake_result": resolved_intake_result,
                        "final_result": failure_snapshot,
                    },
                    artifact_store=artifact_store,
                )
    except Exception:  # noqa: BLE001 - preserve the original runtime failure.
        task_state = None
    try:
        artifact_store.persist_failure(
            thread_id=thread_id,
            error=error,
            task_state=task_state,
        )
    except Exception:  # noqa: BLE001 - diagnostics must not mask the runtime failure.
        return


# Recover the canonical intake payload from the persisted checkpoint snapshot after resume.
def _load_intake_result_from_checkpoint(
    *,
    thread_id: str,
    runtime_graph: Any,
) -> IntakeResult | None:
    snapshot = runtime_graph.get_state({"configurable": {"thread_id": thread_id}})
    snapshot_values = snapshot.values if snapshot is not None else {}
    if not isinstance(snapshot_values, dict):
        return None
    task_state = snapshot_values.get("task_state")
    if not isinstance(task_state, TaskState):
        return None
    return task_state.intake_payload


# Recover the intake payload directly from an already-resolved runtime result when possible.
def _load_intake_result_from_final_result(final_result: dict[str, Any]) -> IntakeResult | None:
    task_state = final_result.get("task_state")
    if not isinstance(task_state, TaskState):
        return None
    return task_state.intake_payload


def parse_cli_args() -> argparse.Namespace:
    # Parse CLI arguments for the current Layer 2 runtime slice.
    parser = argparse.ArgumentParser(
        description=(
            "Run the Layer 2 planner/actor/governance/tool/HITL/external-execution loop."
        )
    )
    parser.add_argument(
        "--task",
        default=(
            "Find official sources about the Digital Markets Act and produce a short summary."
        ),
        help="Raw task text to parse through Layer 1 and bootstrap into Layer 2.",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Optional context hint for Layer 1.",
    )
    parser.add_argument(
        "--policy-profile",
        default=None,
        help="Optional named execution policy profile.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional LangGraph thread identifier.",
    )
    parser.add_argument(
        "--attach-thread",
        default=None,
        help="Recover ownership of an existing thread paused for human review.",
    )
    parser.add_argument(
        "--full-trace",
        action="store_true",
        help="Print the full trace instead of the compact summary.",
    )
    parser.add_argument(
        "--live-trace",
        action="store_true",
        help="Print the full trace snapshot after each streamed node update.",
    )
    parser.add_argument(
        "--demo-intake-profile",
        choices=["research", "clarification"],
        default=None,
        help="Use a local deterministic intake payload instead of invoking Layer 1 via OpenAI.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable streaming progress logs and print only the final trace or summary.",
    )
    parser.add_argument(
        "--trace-file",
        default=None,
        help="Optionally write an additional full trace JSON outside per-thread artifacts.",
    )
    return parser.parse_args()


def _build_demo_intake_result(
    *,
    profile: str,
    raw_task_text: str,
    context_hint: str | None,
) -> IntakeResult:
    # Build deterministic intake payloads so bootstrap can be tested without Layer 1 parsing.
    normalized_goal = " ".join(raw_task_text.strip().split()) or "Demo runtime task."
    normalized_context = " ".join((context_hint or "").strip().split())

    # Keep one profile that blocks into clarification and one that enters normal planning.
    if profile == "clarification":
        task_spec = TaskSpec(
            goal=normalized_goal,
            context=normalized_context,
            constraints=[],
            sensitivity=SensitivityLevel.HIGH,
            success_criteria=[],
        )
        parse_status = ParseStatus.NEEDS_CLARIFICATION
    else:
        task_spec = TaskSpec(
            goal=normalized_goal,
            context=normalized_context or "Use reliable documentary evidence.",
            constraints=["Use reliable sources."],
            sensitivity=SensitivityLevel.LOW,
            success_criteria=["Return a grounded concise answer."],
        )
        parse_status = ParseStatus.COMPLETE

    execution_policy = ExecutionPolicy(
        confidence_threshold=0.65,
    )

    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=task_spec,
            execution_policy=execution_policy,
        ),
        metadata=IntakeMetadata(
            parse_status=parse_status,
            parser_name="demo_intake_builder",
            prompt_version="demo",
            schema_version="1.0.0",
            warnings=[],
            missing_information=(
                ["success_criteria", "constraints"]
                if profile == "clarification"
                else []
            ),
        ),
    )


# Return the canonical runtime state carried by one Layer 2 trace.
def _trace_task_state(trace: dict[str, Any]) -> TaskState:
    final_result = trace.get("final_result")
    task_state = final_result.get("task_state") if isinstance(final_result, dict) else None
    if not isinstance(task_state, TaskState):
        raise ValueError(  # noqa: TRY004 - malformed trace values use the public ValueError contract.
            "Layer 2 trace does not contain a canonical task_state."
        )
    return task_state


# Rebuild only canonical interrupted state so a replacement terminal can own it.
def _load_interrupted_trace_from_checkpoint(
    *,
    thread_id: str,
    checkpointer: Any,
) -> dict[str, Any]:
    runtime_graph = compose_layer2_runtime(
        Layer2RuntimeDependencies(checkpointer=checkpointer)
    )
    final_result = _build_final_result_from_graph_snapshot(
        runtime_graph=runtime_graph,
        config={"configurable": {"thread_id": thread_id}},
    )
    task_state = final_result.get("task_state")
    if not isinstance(task_state, TaskState):
        raise ValueError(  # noqa: TRY004 - checkpoint lookup exposes a ValueError contract.
            f"Thread '{thread_id}' has no canonical LangGraph checkpoint."
        )
    if task_state.current_state != RuntimeState.WAITING_HUMAN_REVIEW:
        raise ValueError(
            f"Thread '{thread_id}' is not waiting for human review and cannot be attached."
        )
    return {
        "thread_id": thread_id,
        "intake_result": task_state.intake_payload,
        "final_result": final_result,
    }


# Keep the execution owner alive until the dashboard submits the exact pending decision.
def _wait_for_dashboard_decision(
    *,
    thread_id: str,
    request_id: str,
    owner_id: str,
    decision_inbox: ReviewDecisionInboxPort,
    poll_interval_seconds: float = 0.5,
) -> ReviewDecisionSubmission:
    while True:
        decision_inbox.heartbeat_owner(
            thread_id=thread_id,
            owner_id=owner_id,
            status="waiting_human_review",
        )
        submission = decision_inbox.claim_next(
            thread_id=thread_id,
            request_id=request_id,
            owner_id=owner_id,
        )
        if submission is not None:
            return submission
        time.sleep(poll_interval_seconds)


# Renew the owner lease while providers or tools block between streamed node updates.
def _resume_with_owner_heartbeat(
    *,
    thread_id: str,
    owner_id: str,
    submission: ReviewDecisionSubmission,
    checkpointer: Any,
    artifact_store: RuntimeArtifactStore,
    decision_inbox: ReviewDecisionInboxPort,
    progress_callback: Callable[[str, dict[str, Any]], None] | None,
    live_trace: bool,
    heartbeat_interval_seconds: float = 2.0,
) -> dict[str, Any]:
    stop_heartbeat = threading.Event()
    heartbeat_failures: list[Exception] = []

    # Keep the lease live independently from provider response latency.
    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(heartbeat_interval_seconds):
            try:
                decision_inbox.heartbeat_owner(
                    thread_id=thread_id,
                    owner_id=owner_id,
                    status="resuming",
                )
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                heartbeat_failures.append(exc)
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name=f"layer2-owner-heartbeat-{thread_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        trace = resume_layer2_flow(
            thread_id=thread_id,
            human_review_decision=submission.decision_payload,
            checkpointer=checkpointer,
            artifact_store=artifact_store,
            progress_callback=progress_callback,
            live_trace=live_trace,
        )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=max(heartbeat_interval_seconds * 2, 1.0))

    if heartbeat_failures:
        live_owner = decision_inbox.get_live_owner(thread_id=thread_id)
        if live_owner is None or live_owner.owner_id != owner_id:
            raise RuntimeError("Runtime ownership was lost while applying the human decision.")
    return trace


# Run every HITL continuation in the terminal process that owns the original execution.
def _run_owned_cli_session(
    *,
    args: argparse.Namespace,
    checkpointer: Any,
    artifact_store: RuntimeArtifactStore,
    decision_inbox: ReviewDecisionInboxPort,
) -> dict[str, Any]:
    attach_thread = getattr(args, "attach_thread", None)
    if attach_thread and getattr(args, "thread_id", None):
        raise ValueError("--attach-thread and --thread-id cannot be used together.")
    thread_id = attach_thread or args.thread_id or f"layer2-{uuid4()}"
    progress_callback = None if args.quiet else _print_progress
    if attach_thread:
        trace = _load_interrupted_trace_from_checkpoint(
            thread_id=thread_id,
            checkpointer=checkpointer,
        )
        print(
            f"[Layer 2] attached to interrupted runtime | thread={thread_id}",
            file=sys.stderr,
            flush=True,
        )
    else:
        trace = run_layer2_flow(
            raw_task_text=args.task,
            context_hint=args.context,
            policy_profile=args.policy_profile,
            thread_id=thread_id,
            demo_intake_profile=args.demo_intake_profile,
            checkpointer=checkpointer,
            artifact_store=artifact_store,
            progress_callback=progress_callback,
            live_trace=args.live_trace,
        )

    owner_id = f"cli-{os.getpid()}-{uuid4()}"
    owner_acquired = False
    try:
        while _trace_task_state(trace).current_state == RuntimeState.WAITING_HUMAN_REVIEW:
            if not owner_acquired:
                decision_inbox.acquire_owner(
                    thread_id=thread_id,
                    owner_id=owner_id,
                    process_id=os.getpid(),
                    terminal_label=f"terminal-pid-{os.getpid()}",
                    status="waiting_human_review",
                )
                owner_acquired = True
            else:
                decision_inbox.heartbeat_owner(
                    thread_id=thread_id,
                    owner_id=owner_id,
                    status="waiting_human_review",
                )

            pending_request = _trace_task_state(trace).pending_human_review_request
            request_id = pending_request.request_id if pending_request is not None else "unknown"
            print(
                f"[Layer 2] waiting for dashboard decision | thread={thread_id} | request={request_id}",
                file=sys.stderr,
                flush=True,
            )
            submission = _wait_for_dashboard_decision(
                thread_id=thread_id,
                request_id=request_id,
                owner_id=owner_id,
                decision_inbox=decision_inbox,
            )
            print(
                f"[Layer 2] dashboard decision received | decision={submission.decision_type}",
                file=sys.stderr,
                flush=True,
            )
            decision_inbox.heartbeat_owner(
                thread_id=thread_id,
                owner_id=owner_id,
                status="resuming",
            )
            try:
                trace = _resume_with_owner_heartbeat(
                    thread_id=thread_id,
                    owner_id=owner_id,
                    submission=submission,
                    checkpointer=checkpointer,
                    artifact_store=artifact_store,
                    decision_inbox=decision_inbox,
                    progress_callback=progress_callback,
                    live_trace=args.live_trace,
                )
            except Exception as exc:
                decision_inbox.mark_failed(
                    submission_id=submission.submission_id,
                    owner_id=owner_id,
                    error=str(exc),
                )
                raise
            decision_inbox.mark_consumed(
                submission_id=submission.submission_id,
                owner_id=owner_id,
            )
        return trace
    finally:
        if owner_acquired:
            decision_inbox.release_owner(thread_id=thread_id, owner_id=owner_id)


def main() -> None:
    # Run the current Layer 2 slice from the command line.
    args = parse_cli_args()
    checkpointer = build_default_checkpointer()
    artifact_store = RuntimeArtifactStore()
    decision_inbox = SQLiteReviewDecisionInbox()
    try:
        trace = _run_owned_cli_session(
            args=args,
            checkpointer=checkpointer,
            artifact_store=artifact_store,
            decision_inbox=decision_inbox,
        )
        if args.trace_file is not None:
            write_layer2_trace_file(trace, output_path=args.trace_file)

        if args.full_trace:
            print_layer2_trace(trace)
        else:
            print_layer2_summary(trace)
    finally:
        _prune_expired_checkpoints(
            checkpointer=checkpointer,
            artifact_store=artifact_store,
        )
        decision_inbox.close()
        close_checkpointer(checkpointer)


if __name__ == "__main__":
    main()
