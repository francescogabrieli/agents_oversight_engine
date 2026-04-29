from __future__ import annotations

import argparse
import json
from typing import Any, Protocol
from uuid import uuid4

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
from layer2_runtime_governance import build_runtime_graph
from layer2_runtime_governance.actor_contract import ActorPort
from layer2_runtime_governance.governance import PolicyEnginePort
from layer2_runtime_governance.planning import PlannerPort
from layer2_runtime_governance.replanning import ReplannerPort
from layer2_runtime_governance.trace import (
    build_compact_trace_from_runtime_trace,
    build_layer2_runtime_trace,
    layer2_trace_to_dict,
    layer2_trace_to_json,
)
from layer2_runtime_governance.tool_contract import ToolGatewayPort


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
) -> dict[str, Any]:
    # Run Layer 1 plus the current Layer 2 graph and return the resulting trace.
    runtime_graph = build_runtime_graph(
        planner_service=planner_service,
        actor_service=actor_service,
        tool_gateway_service=tool_gateway_service,
        replanner_service=replanner_service,
        policy_engine_service=policy_engine_service,
    )

    resolved_thread_id = thread_id or f"layer2-{uuid4()}"
    config = {"configurable": {"thread_id": resolved_thread_id}}

    if demo_intake_profile is not None and intake_service is not None:
        raise ValueError(
            "demo_intake_profile and intake_service cannot be used together in run_layer2_flow."
        )

    if demo_intake_profile is not None:
        intake_result = _build_demo_intake_result(
            profile=demo_intake_profile,
            raw_task_text=raw_task_text,
            context_hint=context_hint,
        )
    else:
        resolved_intake_service = intake_service or build_intake_service()
        intake_result = resolved_intake_service.execute(
            IntakeCommand(
                raw_task_text=raw_task_text,
                context_hint=context_hint,
                policy_profile=policy_profile,
            )
        )

    final_result = runtime_graph.invoke(
        {"intake_result": intake_result},
        config=config,
    )

    # Return both layer boundaries so callers can inspect the handoff end to end.
    return {
        "thread_id": resolved_thread_id,
        "intake_result": intake_result,
        "final_result": final_result,
    }


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


def print_layer2_summary(trace: dict[str, Any]) -> None:
    # Print the compact runtime trace used by the current Layer 2 CLI.
    compact_trace = build_compact_trace(trace)
    _print_section("RUNTIME", compact_trace["runtime"])
    _print_section("INTAKE SUMMARY", compact_trace["intake_summary"])
    _print_section("PLANNING SNAPSHOT", compact_trace["planning_snapshot"])
    _print_section("ACTOR SNAPSHOT", compact_trace["actor_snapshot"])
    _print_section("EXECUTION SNAPSHOT", compact_trace["execution_snapshot"])
    _print_section("GOVERNANCE SNAPSHOT", compact_trace["governance_snapshot"])
    _print_section("TOOL SNAPSHOT", compact_trace["tool_snapshot"])
    _print_section("BOOTSTRAP STATE", compact_trace["bootstrap_state"])


def print_layer2_trace(trace: dict[str, Any]) -> None:
    # Print the typed full runtime trace when compact output hides too much detail.
    final_task_state = trace["final_result"].get("task_state")
    if final_task_state is None:
        raise ValueError("Cannot print Layer 2 trace without final task_state.")

    runtime_trace = build_layer2_runtime_trace(
        thread_id=trace["thread_id"],
        intake_result=trace["intake_result"],
        task_state=final_task_state,
    )
    print(layer2_trace_to_json(runtime_trace))


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
    trace = run_layer2_flow(
        raw_task_text=args.task,
        context_hint=args.context,
        policy_profile=args.policy_profile,
        thread_id=args.thread_id,
        demo_intake_profile=args.demo_intake_profile,
    )

    if args.full_trace:
        print_layer2_trace(trace)
    else:
        print_layer2_summary(trace)


if __name__ == "__main__":
    main()
