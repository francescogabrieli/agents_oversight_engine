from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

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
) -> dict[str, Any]:
    # Run Layer 1 plus the minimal Layer 2 bootstrap graph and return the resulting trace.
    # This is the linear entrypoint for local demos and debugging.
    # It runs Layer 1 intake and invokes the current Layer 2 bootstrap graph.
    runtime_graph = build_runtime_graph()
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

    return {
        "thread_id": resolved_thread_id,
        "intake_result": intake_result,
        "final_result": final_result,
    }


def build_compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    # Reduce the raw trace to the parts that matter for bootstrap inspection.
    # Convert the raw trace into a compact terminal-friendly summary.
    intake_result = trace["intake_result"]
    final_result = trace["final_result"]
    final_task_state = final_result.get("task_state")

    return {
        "runtime": {
            "thread_id": trace["thread_id"],
            "demo_intake_used": trace["intake_result"].metadata.parser_name == "demo_intake_builder",
        },
        "intake_summary": {
            "goal": intake_result.structured_task.task_spec.goal,
            "domain_type": intake_result.structured_task.task_spec.domain_type.value,
            "sensitivity": intake_result.structured_task.task_spec.sensitivity.value,
            "parse_status": intake_result.metadata.parse_status.value,
            "read_only_mode": intake_result.structured_task.execution_policy.read_only_mode,
        },
        "bootstrap_state": {
            "current_state": (
                final_task_state.current_state.value if final_task_state is not None else None
            ),
            "step_count": len(final_task_state.step_history) if final_task_state else 0,
            "bootstrap_completed": (
                final_task_state.bootstrap_completed if final_task_state is not None else None
            ),
            "runtime_flags": (
                [flag.value for flag in final_task_state.runtime_flags]
                if final_task_state is not None
                else []
            ),
        },
    }


def to_jsonable(value: Any) -> Any:
    # Convert runtime objects into plain JSON so traces can be printed consistently.
    # Convert runtime objects into plain JSON-serializable structures.
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if is_dataclass(value):
        return to_jsonable(asdict(value))

    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]

    return value


def _print_section(title: str, payload: Any) -> None:
    # Print one section of the Layer 2 trace with a stable visual format.
    # Pretty-print one section of the runtime trace.
    print(f"\n=== {title} ===")
    print(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


def print_layer2_summary(trace: dict[str, Any]) -> None:
    # Print the compact bootstrap trace used by the current Layer 2 CLI.
    # Default compact output for terminal runs.
    compact_trace = build_compact_trace(trace)
    _print_section("RUNTIME", compact_trace["runtime"])
    _print_section("INTAKE SUMMARY", compact_trace["intake_summary"])
    _print_section("BOOTSTRAP STATE", compact_trace["bootstrap_state"])


def print_layer2_trace(trace: dict[str, Any]) -> None:
    # Print the full raw trace when compact output hides too much detail.
    # Full debug output when a compact summary is not enough.
    print(json.dumps(to_jsonable(trace), indent=2, ensure_ascii=False))


def parse_cli_args() -> argparse.Namespace:
    # Parse CLI arguments for the current bootstrap-only Layer 2 runner.
    # CLI entrypoint used for local execution of the Layer 2 bootstrap flow.
    parser = argparse.ArgumentParser(description="Run the current Layer 2 bootstrap flow.")
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
    # Build deterministic intake payloads so Layer 2 can be tested without
    # calling the real Layer 1 parser or the OpenAI API.
    normalized_goal = " ".join(raw_task_text.strip().split()) or "Demo runtime task."
    normalized_context = " ".join((context_hint or "").strip().split())

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
    # Run the bootstrap-only Layer 2 flow from the command line.
    # Run the Layer 2 demo flow from the command line.
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
