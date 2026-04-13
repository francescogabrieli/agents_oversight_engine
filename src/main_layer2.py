from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.types import Command
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


def run_layer2_flow(
    *,
    raw_task_text: str,
    context_hint: str | None = None,
    policy_profile: str | None = None,
    thread_id: str | None = None,
    auto_resume: bool = True,
    human_decision: str = "approve",
    human_comment: str | None = "Approved during local runtime demo.",
    demo_intake_profile: str | None = None,
) -> dict[str, Any]:
    # This is the linear entrypoint for local demos and debugging.
    # It runs Layer 1 intake, invokes the Layer 2 graph, and optionally
    # auto-resumes the graph if it pauses for human review.
    runtime_graph = build_runtime_graph()
    resolved_thread_id = thread_id or f"layer2-{uuid4()}"
    config = {"configurable": {"thread_id": resolved_thread_id}}

    if demo_intake_profile is not None:
        intake_result = _build_demo_intake_result(
            profile=demo_intake_profile,
            raw_task_text=raw_task_text,
            context_hint=context_hint,
        )
    else:
        intake_service = build_intake_service()
        intake_result = intake_service.execute(
            IntakeCommand(
                raw_task_text=raw_task_text,
                context_hint=context_hint,
                policy_profile=policy_profile,
            )
        )

    first_result = runtime_graph.invoke(
        {"intake_result": intake_result},
        config=config,
    )

    trace: dict[str, Any] = {
        "thread_id": resolved_thread_id,
        "intake_result": intake_result,
        "first_result": first_result,
        "interrupt_payload": None,
        "resume_payload": None,
        "final_result": first_result,
    }

    interrupt_payload = _extract_interrupt_payload(first_result)
    if interrupt_payload is not None:
        trace["interrupt_payload"] = interrupt_payload

        if auto_resume:
            resume_payload = {
                "decision": human_decision,
                "comment": human_comment,
            }
            trace["resume_payload"] = resume_payload
            trace["final_result"] = runtime_graph.invoke(
                Command(resume=resume_payload),
                config=config,
            )

    return trace


def build_compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    # Convert the raw trace into a compact terminal-friendly summary.
    intake_result = trace["intake_result"]
    first_result = trace["first_result"]
    final_result = trace["final_result"]
    first_task_state = first_result.get("task_state")
    final_task_state = final_result.get("task_state")

    return {
        "runtime": {
            "thread_id": trace["thread_id"],
            "auto_resume_enabled": trace["resume_payload"] is not None,
            "interrupt_detected": trace["interrupt_payload"] is not None,
            "demo_intake_used": trace["intake_result"].metadata.parser_name == "demo_intake_builder",
        },
        "intake_summary": {
            "goal": intake_result.structured_task.task_spec.goal,
            "domain_type": intake_result.structured_task.task_spec.domain_type.value,
            "sensitivity": intake_result.structured_task.task_spec.sensitivity.value,
            "parse_status": intake_result.metadata.parse_status.value,
            "read_only_mode": intake_result.structured_task.execution_policy.read_only_mode,
        },
        "first_pass": {
            "current_state": (
                first_task_state.current_state.value if first_task_state is not None else None
            ),
            "step_count": len(first_task_state.step_history) if first_task_state else 0,
            "governance_decision": (
                first_task_state.governance_output.decision.value
                if first_task_state and first_task_state.governance_output is not None
                else None
            ),
        },
        "human_review": {
            "interrupt_payload": trace["interrupt_payload"],
            "resume_payload": trace["resume_payload"],
        },
        "final_state": {
            "current_state": (
                final_task_state.current_state.value if final_task_state is not None else None
            ),
            "step_count": len(final_task_state.step_history) if final_task_state else 0,
            "termination_reason": (
                final_task_state.termination_reason if final_task_state is not None else None
            ),
            "final_output": (
                final_task_state.final_output if final_task_state is not None else None
            ),
        },
    }


def _extract_interrupt_payload(result: dict[str, Any]) -> Any | None:
    # LangGraph returns interrupts in a wrapper object.
    # This helper extracts the actual review payload.
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    return getattr(first_interrupt, "value", first_interrupt)


def to_jsonable(value: Any) -> Any:
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
    # Pretty-print one section of the runtime trace.
    print(f"\n=== {title} ===")
    print(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


def print_layer2_summary(trace: dict[str, Any]) -> None:
    # Default compact output for terminal runs.
    compact_trace = build_compact_trace(trace)
    _print_section("RUNTIME", compact_trace["runtime"])
    _print_section("INTAKE SUMMARY", compact_trace["intake_summary"])
    _print_section("FIRST PASS", compact_trace["first_pass"])

    if compact_trace["human_review"]["interrupt_payload"] is not None:
        _print_section("HUMAN REVIEW", compact_trace["human_review"])

    _print_section("FINAL STATE", compact_trace["final_state"])


def print_layer2_trace(trace: dict[str, Any]) -> None:
    # Full debug output when a compact summary is not enough.
    print(json.dumps(to_jsonable(trace), indent=2, ensure_ascii=False))


def parse_cli_args() -> argparse.Namespace:
    # CLI entrypoint used for local execution of the Layer 2 runtime.
    parser = argparse.ArgumentParser(description="Run the compact Layer 2 runtime flow.")
    parser.add_argument(
        "--task",
        default=(
            "Please draft and send a customer-facing email confirming the updated delivery date."
        ),
        help="Raw task text to parse through Layer 1 and execute in Layer 2.",
    )
    parser.add_argument(
        "--context",
        default="Assume the message is externally visible and must be reviewed before sending.",
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
        "--no-auto-resume",
        action="store_true",
        help="Stop after interrupt instead of auto-resuming with a demo human decision.",
    )
    parser.add_argument(
        "--human-decision",
        default="approve",
        help="Decision used for auto-resume: approve, reject, edit, request_more_evidence, stop.",
    )
    parser.add_argument(
        "--human-comment",
        default="Approved during local runtime demo.",
        help="Comment attached to the auto-resume decision.",
    )
    parser.add_argument(
        "--full-trace",
        action="store_true",
        help="Print the full trace instead of the compact summary.",
    )
    parser.add_argument(
        "--demo-intake-profile",
        choices=["research", "external_communication", "clarification"],
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
    # Build deterministic intake payloads so Layer 2 can be tested without
    # calling the real Layer 1 parser or the OpenAI API.
    normalized_goal = " ".join(raw_task_text.strip().split()) or "Demo runtime task."
    normalized_context = " ".join((context_hint or "").strip().split())

    if profile == "external_communication":
        task_spec = TaskSpec(
            goal=normalized_goal,
            context=normalized_context or "Externally visible customer communication.",
            constraints=["Use a professional tone."],
            domain_type=DomainType.EXTERNAL_COMMUNICATION,
            domain_label="demo_external_communication",
            sensitivity=SensitivityLevel.HIGH,
            success_criteria=["Prepare the external communication."],
        )
        parse_status = ParseStatus.COMPLETE
    elif profile == "clarification":
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
    # Run the Layer 2 demo flow from the command line.
    args = parse_cli_args()
    trace = run_layer2_flow(
        raw_task_text=args.task,
        context_hint=args.context,
        policy_profile=args.policy_profile,
        thread_id=args.thread_id,
        auto_resume=not args.no_auto_resume,
        human_decision=args.human_decision,
        human_comment=args.human_comment,
        demo_intake_profile=args.demo_intake_profile,
    )

    if args.full_trace:
        print_layer2_trace(trace)
    else:
        print_layer2_summary(trace)


if __name__ == "__main__":
    main()
