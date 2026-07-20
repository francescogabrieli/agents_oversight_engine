from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from json_serialization import to_jsonable

from layer1_intake import (
    InputNormalizer,
    IntakeCommand,
    IntakeMetadata,
    IntakeResultValidator,
    IntakeService,
    JsonExecutionPolicyRepository,
    OpenAIParserSettings,
    OpenAITaskSpecParser,
    PolicyResolver,
    Sha256FingerprintService,
    StructuredTask,
    SystemClock,
    TaskSpecBuilder,
    get_default_policy_config_path,
)


# This file is intentionally linear.
# It does not replace the real layer implementation.
# Instead, it shows the exact order in which the layer 1 components are used,
# which makes the flow easier to read, explain, and debug.


def build_layer1_components(
    *,
    policy_config_path: str | Path | None = None,
    openai_settings: OpenAIParserSettings | None = None,
) -> dict[str, Any]:
    # Build the concrete Layer 1 dependency graph in a readable, inspectable form.

    resolved_policy_path = (
        Path(policy_config_path) if policy_config_path else get_default_policy_config_path()
    )
    settings = openai_settings or OpenAIParserSettings.from_env()

    parser = OpenAITaskSpecParser(settings=settings)
    repository = JsonExecutionPolicyRepository(config_path=resolved_policy_path)
    normalizer = InputNormalizer()
    task_spec_builder = TaskSpecBuilder(parser=parser)
    policy_resolver = PolicyResolver(repository=repository)
    validator = IntakeResultValidator()
    clock = SystemClock()
    fingerprint_service = Sha256FingerprintService()
    intake_service = IntakeService(
        normalizer=normalizer,
        task_spec_builder=task_spec_builder,
        policy_resolver=policy_resolver,
        validator=validator,
        clock=clock,
        fingerprint_service=fingerprint_service,
    )

    return {
        "policy_config_path": resolved_policy_path,
        "openai_settings": settings,
        "parser": parser,
        "repository": repository,
        "normalizer": normalizer,
        "task_spec_builder": task_spec_builder,
        "policy_resolver": policy_resolver,
        "validator": validator,
        "clock": clock,
        "fingerprint_service": fingerprint_service,
        "intake_service": intake_service,
    }


def run_layer1_flow(
    *,
    raw_task_text: str,
    context_hint: str | None = None,
    policy_profile: str | None = None,
    policy_config_path: str | Path | None = None,
    openai_settings: OpenAIParserSettings | None = None,
) -> dict[str, Any]:
    # Execute Layer 1 step by step and keep every intermediate payload for debugging.

    components = build_layer1_components(
        policy_config_path=policy_config_path,
        openai_settings=openai_settings,
    )

    # Step 1: create the raw command object received by layer 1.
    command = IntakeCommand(
        raw_task_text=raw_task_text,
        context_hint=context_hint,
        policy_profile=policy_profile,
    )

    # Step 2: normalize the command before parsing.
    normalized_command = components["normalizer"].normalize(command)

    # Step 3: call the parser through the builder and collect warnings/status.
    build_result = components["task_spec_builder"].build(normalized_command)

    # Step 4: resolve the execution policy from task attributes and config.
    execution_policy = components["policy_resolver"].resolve(
        task_spec=build_result.task_spec,
        profile_name=normalized_command.policy_profile,
    )

    # Step 5: combine semantic task data and execution constraints.
    structured_task = StructuredTask(
        task_spec=build_result.task_spec,
        execution_policy=execution_policy,
    )

    # Step 6: build operational metadata for traceability and debugging.
    metadata = IntakeMetadata(
        raw_input_fingerprint=components["fingerprint_service"].fingerprint(
            normalized_command.raw_task_text
        ),
        parse_status=build_result.parse_status,
        parser_name=components["task_spec_builder"].parser_name,
        prompt_version=components["task_spec_builder"].prompt_version,
        schema_version=components["intake_service"].schema_version,
        warnings=build_result.warnings,
        missing_information=build_result.missing_information,
        created_at=components["clock"].now(),
    )

    # Step 7: run the final validator to obtain the canonical layer output.
    final_result = components["validator"].validate(
        structured_task=structured_task,
        metadata=metadata,
    )

    # Return intermediate artifacts as well so the CLI can explain every intake step.
    return {
        "component_summary": {
            "policy_config_path": str(components["policy_config_path"]),
            "openai_model": components["openai_settings"].model,
            "openai_api_key_present": bool(components["openai_settings"].api_key),
            "parser_name": components["task_spec_builder"].parser_name,
            "prompt_version": components["task_spec_builder"].prompt_version,
        },
        "input_command": command,
        "normalized_command": normalized_command,
        "task_spec_build_result": build_result,
        "execution_policy": execution_policy,
        "structured_task": structured_task,
        "metadata_before_validation": metadata,
        "final_result": final_result,
    }



def print_layer1_trace(trace: dict[str, Any]) -> None:
    # Print the full debug trace without collapsing intermediate objects.

    print(json.dumps(to_jsonable(trace), indent=2, ensure_ascii=False))


def build_compact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    # Build a reduced trace for terminal use when the full payload would be too noisy.

    input_command = to_jsonable(trace["input_command"])
    normalized_command = to_jsonable(trace["normalized_command"])
    build_result = to_jsonable(trace["task_spec_build_result"])
    execution_policy = to_jsonable(trace["execution_policy"])
    metadata = to_jsonable(trace["metadata_before_validation"])
    final_result = to_jsonable(trace["final_result"])

    task_spec = build_result["task_spec"]

    return {
        "runtime": {
            "model": trace["component_summary"]["openai_model"],
            "parser_name": trace["component_summary"]["parser_name"],
            "prompt_version": trace["component_summary"]["prompt_version"],
            "policy_profile_requested": input_command["policy_profile"],
            "api_key_present": trace["component_summary"]["openai_api_key_present"],
        },
        "input": {
            "raw_task_text": input_command["raw_task_text"],
            "context_hint": input_command["context_hint"],
            "normalized_input_changed": input_command != normalized_command,
        },
        "parser_output": {
            # Keep the parser section close to the TaskSpec contract rather than the raw model shape.
            "goal": task_spec["goal"],
            "context": task_spec["context"],
            "domain_type": task_spec["domain_type"],
            "domain_label": task_spec["domain_label"],
            "sensitivity": task_spec["sensitivity"],
            "constraints_count": len(task_spec["constraints"]),
            "constraints": task_spec["constraints"],
            "success_criteria_count": len(task_spec["success_criteria"]),
            "success_criteria": task_spec["success_criteria"],
            "parse_status": build_result["parse_status"],
            "warnings": build_result["warnings"],
            "missing_information": build_result["missing_information"],
        },
        "execution_policy": {
            "policy_profile_name": execution_policy["policy_profile_name"],
            "confidence_threshold": execution_policy["confidence_threshold"],
            "max_steps": execution_policy["budget"]["max_steps"],
            "max_tool_calls": execution_policy["budget"]["max_tool_calls"],
            "read_only_mode": execution_policy["read_only_mode"],
        },
        "final_result": {
            "request_id": final_result["metadata"]["request_id"],
            "parse_status": final_result["metadata"]["parse_status"],
            "warnings_count": len(final_result["metadata"]["warnings"]),
            "missing_information_count": len(final_result["metadata"]["missing_information"]),
            "created_at": metadata["created_at"],
        },
    }


def _print_section(title: str, payload: Any) -> None:
    # Print one titled JSON block so the terminal output remains scannable.

    print(f"\n=== {title} ===")
    print(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


def print_layer1_summary(trace: dict[str, Any]) -> None:
    # Print the compact Layer 1 trace in the same order as the orchestration flow.

    compact_trace = build_compact_trace(trace)
    _print_section("RUNTIME", compact_trace["runtime"])
    _print_section("INPUT", compact_trace["input"])
    _print_section("PARSER OUTPUT", compact_trace["parser_output"])
    _print_section("EXECUTION POLICY", compact_trace["execution_policy"])
    _print_section("FINAL RESULT", compact_trace["final_result"])


def parse_cli_args() -> argparse.Namespace:
    # Parse the CLI inputs used for local Layer 1 inspection.

    parser = argparse.ArgumentParser(description="Run the layer 1 intake flow.")
    parser.add_argument(
        "--task",
        default=(
            "  hey , can u pls find me   official sources abt the digital markets act ??? "
            "and make a super short summary   "
        ),
        help="Raw task text to parse.",
    )
    parser.add_argument(
        "--context",
        default="Prefer official EU sources.",
        help="Optional context hint for the parser.",
    )
    parser.add_argument(
        "--policy-profile",
        default=None,
        help="Optional named execution policy profile.",
    )
    parser.add_argument(
        "--full-trace",
        action="store_true",
        help="Print the full raw trace instead of the compact summary.",
    )
    return parser.parse_args()


def main() -> None:
    # Run the local Layer 1 demo and choose between full trace and compact output.

    args = parse_cli_args()

    trace = run_layer1_flow(
        raw_task_text=args.task,
        context_hint=args.context,
        policy_profile=args.policy_profile,
    )

    if args.full_trace:
        print_layer1_trace(trace)
    else:
        print_layer1_summary(trace)


if __name__ == "__main__":
    main()
