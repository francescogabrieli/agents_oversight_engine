from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from langgraph.types import Command

from layer1_intake import (
    BudgetConfig,
    DomainType,
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
from layer2_runtime_governance import build_default_checkpointer, build_runtime_graph
from layer2_runtime_governance import HumanReviewDecision
from layer2_runtime_governance.actor_contract import ActorPort
from layer2_runtime_governance.governance import PolicyEnginePort
from layer2_runtime_governance.planning import PlannerPort
from layer2_runtime_governance.replanning import ReplannerPort
from layer2_runtime_governance.state import RuntimeState, TaskState
from layer2_runtime_governance.trace import (
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    layer2_trace_to_dict,
    layer2_trace_to_json,
)
from layer2_runtime_governance.tool_contract import ToolGatewayPort


# Keep the CLI trace output on a stable default file unless the caller overrides it.
DEFAULT_FULL_TRACE_PATH = Path("full-trace-1.json")


class IntakeExecutorPort(Protocol):
    # This port lets tests inject a deterministic intake service into the Layer 2 runner.
    def execute(self, command: IntakeCommand) -> IntakeResult: ...


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
    checkpointer: Any | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    hitl_event_callback: Callable[[TaskState], None] | None = None,
) -> dict[str, Any]:
    # Run Layer 1 plus the current Layer 2 graph and return the resulting trace.
    runtime_graph = build_runtime_graph(
        planner_service=planner_service,
        actor_service=actor_service,
        tool_gateway_service=tool_gateway_service,
        replanner_service=replanner_service,
        policy_engine_service=policy_engine_service,
        checkpointer=checkpointer,
        hitl_event_callback=hitl_event_callback,
    )

    resolved_thread_id = thread_id or f"layer2-{uuid4()}"
    config = {"configurable": {"thread_id": resolved_thread_id}}

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

    if progress_callback is None:
        final_result = runtime_graph.invoke(
            {"intake_result": intake_result, "thread_id": resolved_thread_id},
            config=config,
        )
    else:
        final_result = _stream_runtime_graph_with_progress(
            runtime_graph=runtime_graph,
            intake_result=intake_result,
            thread_id=resolved_thread_id,
            config=config,
            progress_callback=progress_callback,
        )

    # Return both layer boundaries so callers can inspect the handoff end to end.
    return {
        "thread_id": resolved_thread_id,
        "intake_result": intake_result,
        "final_result": final_result,
    }


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
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    runtime_graph = build_runtime_graph(
        planner_service=planner_service,
        actor_service=actor_service,
        tool_gateway_service=tool_gateway_service,
        replanner_service=replanner_service,
        policy_engine_service=policy_engine_service,
        checkpointer=checkpointer,
    )

    config = {"configurable": {"thread_id": thread_id}}
    final_result = runtime_graph.invoke(Command(resume=human_review_decision), config=config)
    return {
        "thread_id": thread_id,
        "final_result": final_result,
    }


# Stream graph updates so the CLI can show progress during long LLM/tool calls.
def _stream_runtime_graph_with_progress(
    *,
    runtime_graph: Any,
    intake_result: IntakeResult,
    thread_id: str,
    config: dict[str, Any],
    progress_callback: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    final_result: dict[str, Any] = {}
    for chunk in runtime_graph.stream(
        {"intake_result": intake_result, "thread_id": thread_id},
        config=config,
        stream_mode="updates",
    ):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            node_update = update if isinstance(update, dict) else {}
            progress_callback(str(node_name), node_update)
            final_result.update(node_update)

    snapshot = runtime_graph.get_state(config)
    snapshot_values = snapshot.values if snapshot is not None else {}
    if isinstance(snapshot_values, dict):
        final_result = dict(snapshot_values)
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


def to_jsonable(value: Any) -> Any:
    # Keep a small compatibility helper for existing terminal printing code.
    return layer2_trace_to_dict(value) if hasattr(value, "__dataclass_fields__") else value


def _print_section(title: str, payload: Any) -> None:
    # Print one section of the Layer 2 trace with a stable visual format.
    print(f"\n=== {title} ===")
    print(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


# Print compact progress updates while preserving stdout for final traces.
def _print_progress(node_name: str, update: dict[str, Any]) -> None:
    task_state = update.get("task_state")
    details = _format_task_state_progress(task_state)
    suffix = f" | {details}" if details else ""
    print(f"[Layer 2] {node_name}{suffix}", file=sys.stderr, flush=True)


# Summarize only audit-safe state fields during streaming progress.
def _format_task_state_progress(task_state: Any) -> str:
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
    output_path: str | Path = DEFAULT_FULL_TRACE_PATH,
) -> Path:
    runtime_trace = _build_runtime_trace(trace)
    resolved_output_path = Path(output_path)
    resolved_output_path.write_text(layer2_trace_to_json(runtime_trace), encoding="utf-8")
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


def parse_cli_args() -> argparse.Namespace:
    # Parse CLI arguments for the current Layer 2 runtime slice.
    parser = argparse.ArgumentParser(
        description="Run the current Layer 2 bootstrap, planner, actor, and tool loop."
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
        default="Prefer official EU sources.",
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
        "--full-trace",
        action="store_true",
        help="Print the full trace instead of the compact summary.",
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
        default=str(DEFAULT_FULL_TRACE_PATH),
        help="Write the full Layer 2 trace JSON to this file.",
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
            domain_type=DomainType.ANALYSIS,
            domain_label="demo_clarification_required",
            sensitivity=SensitivityLevel.HIGH,
            success_criteria=[],
        )
        parse_status = ParseStatus.NEEDS_CLARIFICATION
    else:
        task_spec = TaskSpec(
            goal=normalized_goal,
            context=normalized_context or "Use reliable documentary evidence.",
            constraints=["Use reliable sources."],
            domain_type=DomainType.RESEARCH,
            domain_label="demo_research",
            sensitivity=SensitivityLevel.LOW,
            success_criteria=["Return a grounded concise answer."],
        )
        parse_status = ParseStatus.COMPLETE

    execution_policy = ExecutionPolicy(
        confidence_threshold=0.65,
        budget=BudgetConfig(max_steps=5, max_tool_calls=10),
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


def main() -> None:
    # Run the current Layer 2 slice from the command line.
    args = parse_cli_args()
    checkpointer = build_default_checkpointer()
    hitl_tracker = _HitlLaunchTracker()
    trace = run_layer2_flow(
        raw_task_text=args.task,
        context_hint=args.context,
        policy_profile=args.policy_profile,
        thread_id=args.thread_id,
        demo_intake_profile=args.demo_intake_profile,
        checkpointer=checkpointer,
        progress_callback=None if args.quiet else _print_progress,
        hitl_event_callback=hitl_tracker.record,
    )
    write_layer2_trace_file(trace, output_path=args.trace_file)

    if hitl_tracker.triggered:
        _maybe_launch_hitl_dashboard_from_trace(
            trace,
            checkpointer=checkpointer,
            open_browser=True,
        )

    if args.full_trace:
        print_layer2_trace(trace)
    else:
        print_layer2_summary(trace)


# Track whether the runtime has requested human review during the current CLI run.
class _HitlLaunchTracker:
    def __init__(self) -> None:
        self.request_ids: set[str] = set()

    # Record a review request exactly once per request id.
    def record(self, task_state: TaskState) -> None:
        pending_review = getattr(task_state, "pending_human_review_request", None)
        request_id = getattr(pending_review, "request_id", None)
        if isinstance(request_id, str) and request_id.strip():
            self.request_ids.add(request_id)

    # Expose whether the runtime ever requested human review during this run.
    @property
    def triggered(self) -> bool:
        return bool(self.request_ids)


# Build a dashboard session from a trace and launch it only when the runtime paused for HITL.
def _maybe_launch_hitl_dashboard_from_trace(
    trace: dict[str, Any],
    *,
    checkpointer: Any,
    open_browser: bool,
) -> None:
    task_state = trace["final_result"].get("task_state")
    if not isinstance(task_state, TaskState):
        return
    if task_state.current_state != RuntimeState.WAITING_HUMAN_REVIEW:
        return

    from main_layer2_dashboard import (
        build_dashboard_session_from_trace,
        launch_dashboard_session,
    )

    session = build_dashboard_session_from_trace(
        trace,
        checkpointer=checkpointer,
    )
    handle = launch_dashboard_session(
        session=session,
        open_browser=open_browser,
    )
    print(
        f"[Layer 2] HITL dashboard auto-launched at http://{handle.host}:{handle.port}",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
