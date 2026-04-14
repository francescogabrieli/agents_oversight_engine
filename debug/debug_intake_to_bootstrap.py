from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from layer1_intake import (
    BudgetConfig,
    DomainType,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance import (
    TaskState,
    bootstrap_from_intake,
)
from main_layer1 import run_layer1_flow, to_jsonable


def parse_cli_args() -> argparse.Namespace:
    # Parse the CLI options for the bootstrap inspection script.
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the handoff from Layer 1 Task Intake to Layer 2 bootstrap "
            "without executing the rest of the runtime graph."
        )
    )
    parser.add_argument(
        "--task",
        default=(
            "Find official sources about the Digital Markets Act and produce a short summary."
        ),
        help="Raw task text to parse through Layer 1.",
    )
    parser.add_argument(
        "--context",
        default="Prefer official EU sources.",
        help="Optional context hint passed to Layer 1.",
    )
    parser.add_argument(
        "--policy-profile",
        default=None,
        help="Optional named execution policy profile.",
    )
    parser.add_argument(
        "--demo-intake-profile",
        choices=["research", "clarification"],
        default=None,
        help=(
            "Use a deterministic local IntakeResult instead of the real Layer 1 parser. "
            "Useful when OPENAI_API_KEY is not available."
        ),
    )
    return parser.parse_args()


def build_demo_intake_result(
    *,
    profile: str,
    raw_task_text: str,
    context_hint: str | None,
) -> IntakeResult:
    # Build a deterministic IntakeResult when Layer 1 should be skipped during debugging.
    normalized_goal = " ".join(raw_task_text.strip().split()) or "Demo runtime task."
    normalized_context = " ".join((context_hint or "").strip().split())

    if profile == "clarification":
        task_spec = TaskSpec(
            goal=normalized_goal,
            context=normalized_context or "Sensitive analysis request.",
            constraints=[],
            domain_type=DomainType.ANALYSIS,
            domain_label="demo_clarification_required",
            sensitivity=SensitivityLevel.HIGH,
            success_criteria=[],
        )
        parse_status = ParseStatus.NEEDS_CLARIFICATION
        missing_information = ["constraints", "success_criteria"]
        warnings = [
            "High-sensitivity task without explicit constraints.",
            "No explicit success criteria were extracted.",
        ]
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
        missing_information = []
        warnings = []

    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=task_spec,
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=5, max_tool_calls=10),
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=parse_status,
            parser_name="debug_demo_intake_builder",
            prompt_version="debug_demo_v1",
            schema_version="1.0.0",
            warnings=warnings,
            missing_information=missing_information,
        ),
    )


def build_trace(
    *,
    raw_task_text: str,
    context_hint: str | None,
    policy_profile: str | None,
    demo_intake_profile: str | None,
) -> dict[str, Any]:
    # Run either real Layer 1 or demo intake, then stop at bootstrap and collect the trace.
    if demo_intake_profile is not None:
        intake_result = build_demo_intake_result(
            profile=demo_intake_profile,
            raw_task_text=raw_task_text,
            context_hint=context_hint,
        )
        layer1_trace: dict[str, Any] | None = None
        layer1_source = {
            "mode": "demo_intake_profile",
            "profile": demo_intake_profile,
            "note": "Layer 1 parsing was not invoked; the intake payload was built locally.",
        }
    else:
        layer1_trace = run_layer1_flow(
            raw_task_text=raw_task_text,
            context_hint=context_hint,
            policy_profile=policy_profile,
        )
        intake_result = layer1_trace["final_result"]
        layer1_source = {
            "mode": "real_layer1",
            "profile": None,
            "note": "The script invoked the real Layer 1 Task Intake flow.",
        }

    task_state = bootstrap_from_intake(intake_result)
    return {
        "layer1_source": layer1_source,
        "layer1_trace": layer1_trace,
        "intake_result": intake_result,
        "task_state": task_state,
        "bootstrap_mapping": build_bootstrap_mapping(
            intake_result=intake_result,
            task_state=task_state,
        ),
    }


def build_bootstrap_mapping(
    *,
    intake_result: IntakeResult,
    task_state: TaskState,
) -> dict[str, Any]:
    # Explain in plain terms how IntakeResult fields become TaskState fields and flags.
    parse_status = intake_result.metadata.parse_status
    mapping_rules: list[str] = [
        "task_spec <- intake_result.structured_task.task_spec",
        "execution_policy <- intake_result.structured_task.execution_policy",
        "intake_metadata <- intake_result.metadata",
        "intake_payload <- full IntakeResult",
        "step_index starts at 0",
        "step_history gets one StepRecord from bootstrap_from_intake",
    ]

    if parse_status == ParseStatus.NEEDS_CLARIFICATION:
        mapping_rules.append(
            "parse_status = needs_clarification -> current_state = waiting_human_review"
        )
        mapping_rules.append(
            "parse_status = needs_clarification -> runtime_flags += clarification_required"
        )
    elif parse_status == ParseStatus.PARTIAL:
        mapping_rules.append("parse_status = partial -> current_state = planning")
        mapping_rules.append("parse_status = partial -> runtime_flags += incomplete_intake")
    else:
        mapping_rules.append("parse_status = complete -> current_state = planning")

    if intake_result.structured_task.execution_policy.read_only_mode:
        mapping_rules.append("execution_policy.read_only_mode = true -> runtime_flags += read_only_execution")

    if intake_result.structured_task.task_spec.sensitivity in {
        SensitivityLevel.HIGH,
        SensitivityLevel.CRITICAL,
    }:
        mapping_rules.append("sensitivity in {high, critical} -> runtime_flags += high_sensitivity")

    return {
        "source_parse_status": parse_status,
        "resulting_current_state": task_state.current_state,
        "resulting_runtime_flags": list(task_state.runtime_flags),
        "mapping_rules_applied": mapping_rules,
    }


def print_section(title: str, payload: Any) -> None:
    # Print one labeled JSON block in a stable, readable format.
    print(f"\n=== {title} ===")
    print(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


def print_trace(trace: dict[str, Any]) -> None:
    # Print the full handoff trace from Layer 1 to bootstrap.
    print_section("LAYER 1 SOURCE", trace["layer1_source"])

    if trace["layer1_trace"] is not None:
        print_section("INTAKE COMMAND", trace["layer1_trace"]["input_command"])
        print_section("NORMALIZED COMMAND", trace["layer1_trace"]["normalized_command"])
        print_section("TASK SPEC BUILD RESULT", trace["layer1_trace"]["task_spec_build_result"])
        print_section("EXECUTION POLICY", trace["layer1_trace"]["execution_policy"])
        print_section("METADATA BEFORE VALIDATION", trace["layer1_trace"]["metadata_before_validation"])

    print_section("FINAL INTAKE RESULT", trace["intake_result"])
    print_section("TASKSTATE AFTER BOOTSTRAP", trace["task_state"])
    print_section("BOOTSTRAP MAPPING", trace["bootstrap_mapping"])


def main() -> None:
    # Run the bootstrap inspection script from the command line.
    args = parse_cli_args()
    trace = build_trace(
        raw_task_text=args.task,
        context_hint=args.context,
        policy_profile=args.policy_profile,
        demo_intake_profile=args.demo_intake_profile,
    )
    print_trace(trace)


if __name__ == "__main__":
    main()
