from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from layer2_runtime_governance import build_default_policy_engine  # noqa: E402
from layer2_runtime_governance.judge import JudgeSettings  # noqa: E402
from layer4_state_evidence_observability.infrastructure.checkpointing import (  # noqa: E402
    build_runtime_serializer,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (  # noqa: E402
    RuntimeArtifactStore,
)
from scripts.run_b6_live_evidence import (  # noqa: E402
    run_live_evidence_anchoring_demo,
    run_live_evidence_demo,
    run_live_static_database_deny_demo,
    run_live_trajectory_failure_demo,
    run_live_trajectory_replan_demo,
)
from scripts.run_oversight_judge_cases import run_live_judge_case  # noqa: E402
from tests.b6_positive_fixtures import (  # noqa: E402
    approve_b6_anchoring_concern_review,
    run_b6_anchoring_concern_run,
    run_b6_evidence_run,
    run_b6_step_exploration_ceiling_run,
    run_b6_trajectory_run,
)


EXPECTED_MANIFEST_VERSION = "evaluation_oversight_demo.v8"
EXPECTED_POOL_ID = "oversight_demo_v1"
DEFAULT_MANIFEST = REPO_ROOT / "configs" / "evaluation_oversight_demo_v1.json"
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "artifacts" / "evaluation" / "input" / "oversight_demo_v1"
)
TRACE_FILE_NAME = "full-trace-2.json"
CONTRACT_MODE = "contract"
LIVE_MODE = "live"
VALID_MODES = {CONTRACT_MODE, LIVE_MODE}


@dataclass(frozen=True)
class ExpectedOutcome:
    terminal_states: tuple[str, ...]
    # An exact directive sequence is preregistered only where the run is deterministic;
    # a live scenario declares null and is judged on membership alone.
    directive_sequence: tuple[str, ...] | None
    required_reason_codes: tuple[str, ...]
    forbidden_reason_codes: tuple[str, ...]
    required_directives: tuple[str, ...]
    forbidden_directives: tuple[str, ...]
    judge_verdicts: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DemoScenario:
    scenario_id: str
    pillar: str
    execution_mode: str
    executor: str
    condition: str
    control: str
    expected: ExpectedOutcome


@dataclass(frozen=True)
class DemoManifest:
    manifest_version: str
    pool_id: str
    scenarios: tuple[DemoScenario, ...]
    source_path: Path
    digest: str


# Load the canonical closed manifest and bind its digest to the campaign.
def load_manifest(path: Path) -> DemoManifest:
    resolved = path.resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if set(payload) != {"manifest_version", "pool_id", "scenarios"}:
        raise ValueError(
            "Oversight manifest fields must be exactly manifest_version, pool_id, scenarios."
        )
    if payload["manifest_version"] != EXPECTED_MANIFEST_VERSION:
        raise ValueError(
            f"Unsupported oversight manifest version: {payload['manifest_version']!r}"
        )
    if payload["pool_id"] != EXPECTED_POOL_ID:
        raise ValueError(f"Oversight manifest pool_id must be {EXPECTED_POOL_ID!r}.")
    scenarios = tuple(_parse_scenario(item) for item in payload["scenarios"])
    ids = [scenario.scenario_id for scenario in scenarios]
    if not scenarios or len(ids) != len(set(ids)):
        raise ValueError("Oversight scenarios must be non-empty and have unique IDs.")
    return DemoManifest(
        manifest_version=payload["manifest_version"],
        pool_id=payload["pool_id"],
        scenarios=scenarios,
        source_path=resolved,
        digest=hashlib.sha256(resolved.read_bytes()).hexdigest(),
    )


# Parse one scenario without accepting undeclared experiment fields.
def _parse_scenario(payload: Any) -> DemoScenario:
    expected_fields = {
        "scenario_id",
        "pillar",
        "execution_mode",
        "executor",
        "condition",
        "control",
        "expected",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError(
            f"Every oversight scenario must contain exactly {sorted(expected_fields)}."
        )
    for field in expected_fields - {"expected"}:
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(
                f"Oversight scenario field {field!r} must be non-empty text."
            )
    if payload["execution_mode"] not in VALID_MODES:
        raise ValueError(f"Unsupported execution mode: {payload['execution_mode']!r}")
    return DemoScenario(
        scenario_id=payload["scenario_id"],
        pillar=payload["pillar"],
        execution_mode=payload["execution_mode"],
        executor=payload["executor"],
        condition=payload["condition"],
        control=payload["control"],
        expected=_parse_expected_outcome(
            payload["expected"], scenario_id=payload["scenario_id"]
        ),
    )


# Validate mutually consistent positive, negative and sequence assertions.
def _parse_expected_outcome(payload: Any, *, scenario_id: str) -> ExpectedOutcome:
    list_fields = (
        "terminal_states",
        "required_reason_codes",
        "forbidden_reason_codes",
        "required_directives",
        "forbidden_directives",
    )
    expected_keys = {"directive_sequence", *list_fields}
    allowed_keys = expected_keys | {"judge_verdicts"}
    if not isinstance(payload, dict) or not expected_keys <= set(payload) or set(payload) - allowed_keys:
        raise ValueError(
            f"Scenario expected outcome must contain {sorted(expected_keys)} and optional judge_verdicts."
        )
    for field in list_fields:
        if not isinstance(payload[field], list) or any(
            not isinstance(item, str) or not item for item in payload[field]
        ):
            raise ValueError(f"Expected outcome field {field!r} must be a text list.")
    if not payload["terminal_states"]:
        raise ValueError(
            f"Scenario {scenario_id!r} must declare at least one admissible terminal state."
        )
    overlap = set(payload["required_reason_codes"]) & set(
        payload["forbidden_reason_codes"]
    )
    if overlap:
        raise ValueError(
            f"Scenario {scenario_id!r} both requires and forbids {sorted(overlap)}."
        )
    sequence = payload["directive_sequence"]
    if sequence is not None and (
        not isinstance(sequence, list)
        or not sequence
        or any(not isinstance(item, str) or not item for item in sequence)
    ):
        raise ValueError(
            f"Scenario {scenario_id!r} directive_sequence must be null or a non-empty text list."
        )
    # An exact sequence already fixes which directives may appear and in what order, so a
    # second directive expectation beside it would be a competing source of truth.
    if sequence is not None and (
        payload["required_directives"] or payload["forbidden_directives"]
    ):
        raise ValueError(
            f"Scenario {scenario_id!r} declares directive_sequence, so required_directives "
            "and forbidden_directives must stay empty."
        )
    if sequence is None and not payload["required_directives"]:
        raise ValueError(
            f"Scenario {scenario_id!r} without a directive_sequence must require at least one directive."
        )
    verdicts = payload.get("judge_verdicts")
    if verdicts is not None and (
        not isinstance(verdicts, list)
        or any(
            not isinstance(item, str)
            or item not in {"safe", "unsafe", "uncertain", "needs_revision"}
            for item in verdicts
        )
    ):
        raise ValueError(f"Scenario {scenario_id!r} judge_verdicts must be a closed text list.")
    return ExpectedOutcome(
        terminal_states=tuple(payload["terminal_states"]),
        directive_sequence=None if sequence is None else tuple(sequence),
        required_reason_codes=tuple(payload["required_reason_codes"]),
        forbidden_reason_codes=tuple(payload["forbidden_reason_codes"]),
        required_directives=tuple(payload["required_directives"]),
        forbidden_directives=tuple(payload["forbidden_directives"]),
        judge_verdicts=None if verdicts is None else tuple(verdicts),
    )


# Compare a canonical trace with the preregistered expected outcome.
def validate_trace(trace: dict[str, Any], expected: ExpectedOutcome) -> tuple[str, ...]:
    errors: list[str] = []
    actual_state = trace.get("runtime", {}).get("current_state")
    if actual_state not in expected.terminal_states:
        errors.append(
            f"terminal_state expected one of {list(expected.terminal_states)} actual={actual_state!r}"
        )
    passes = trace.get("governance_passes", [])
    observed_sequence = tuple(
        item["directive"] for item in passes if isinstance(item.get("directive"), str)
    )
    reason_codes = {
        code
        for item in passes
        for code in item.get("reason_codes", [])
        if isinstance(code, str)
    }
    if (
        expected.directive_sequence is not None
        and observed_sequence != expected.directive_sequence
    ):
        errors.append(
            f"directive_sequence expected={list(expected.directive_sequence)} actual={list(observed_sequence)}"
        )
    for code in expected.required_reason_codes:
        if code not in reason_codes:
            errors.append(f"missing required reason_code={code!r}")
    for code in expected.forbidden_reason_codes:
        if code in reason_codes:
            errors.append(f"forbidden reason_code observed={code!r}")
    for directive in expected.required_directives:
        if directive not in observed_sequence:
            errors.append(f"missing required directive={directive!r}")
    for directive in expected.forbidden_directives:
        if directive in observed_sequence:
            errors.append(f"forbidden directive observed={directive!r}")
    if expected.judge_verdicts is not None:
        judged_passes = [
            item for item in passes
            if item.get("pillar_results", {}).get("judge", {}).get("invoked") is True
        ]
        observed_verdicts = tuple(
            item["pillar_results"]["judge"].get("verdict") for item in judged_passes
        )
        if observed_verdicts != expected.judge_verdicts:
            errors.append(
                f"judge_verdicts expected={list(expected.judge_verdicts)} "
                f"actual={list(observed_verdicts)}"
            )
        for item in judged_passes:
            if item.get("judge_admission", {}).get("required") is not True:
                errors.append("Judge invoked without OPA admission.")
            judge = item["pillar_results"]["judge"]
            for binding in (
                "digest_matches",
                "operation_digest_matches",
                "evidence_snapshot_matches",
                "evidence_selection_matches",
            ):
                if judge.get(binding) is not True:
                    errors.append(f"Judge binding invalid: {binding}.")
            for evaluated, expected_digest in (
                ("evaluated_operation_digest", "expected_operation_digest"),
                ("evaluated_evidence_snapshot_digest", "expected_evidence_snapshot_digest"),
                ("evaluated_evidence_selection_digest", "expected_evidence_selection_digest"),
            ):
                if not judge.get(expected_digest) or judge.get(evaluated) != judge.get(expected_digest):
                    errors.append(f"Judge digest mismatch: {evaluated}.")
            verdict = judge.get("verdict")
            required_response = {
                "unsafe": ("judge_unsafe", "wait_human_review"),
                "uncertain": ("judge_uncertain", "wait_human_review"),
                "needs_revision": ("judge_needs_revision", "revise_action"),
            }.get(verdict)
            if required_response and (
                required_response[0] not in item.get("reason_codes", ())
                or item.get("directive") != required_response[1]
            ):
                errors.append(f"Judge verdict {verdict!r} did not select its OPA response.")
    return tuple(errors)


# Keep the provenance a reader needs to judge a demo - policy versions and the exact model,
# thinking and prompt identity - beside the assertions instead of only inside the trace.
def extract_provenance(trace: dict[str, Any]) -> dict[str, Any]:
    passes = trace.get("governance_passes", [])
    actor = trace.get("actor") or {}
    last_pass = passes[-1] if passes else {}
    judged = [
        item.get("pillar_results", {}).get("judge", {})
        for item in passes
        if item.get("pillar_results", {}).get("judge", {}).get("invoked") is True
    ]
    return {
        "trace_version": trace.get("trace_version"),
        "policy_bundle_version": last_pass.get("policy_bundle_version"),
        "policy_config_version": last_pass.get("policy_config_version"),
        "governance_input_schema_version": last_pass.get(
            "governance_input_schema_version"
        ),
        "governance_pass_count": len(passes),
        "planner_name": trace.get("planner_name"),
        "planner_prompt_version": trace.get("planner_prompt_version"),
        "actor_name": actor.get("actor_name"),
        "actor_model_id": actor.get("actor_model_id"),
        "actor_prompt_version": actor.get("actor_prompt_version"),
        "actor_thinking_mode": actor.get("actor_thinking_mode"),
        "actor_thinking_effort": actor.get("actor_thinking_effort"),
        "judge_prompt_version": (
            judged[-1].get("raw_facts", {}).get("prompt_version") if judged else None
        ),
    }


# Dispatch only allowlisted deterministic contract fixtures through the real PDP.
def _execute_contract(executor: str, *, artifact_root: Path, thread_id: str) -> None:
    policy_engine = build_default_policy_engine()
    runners: dict[str, Callable[..., dict[str, Any]]] = {
        "b6_evidence_deficit": run_b6_evidence_run,
        "b6_replan_ceiling": run_b6_trajectory_run,
        "b6_step_exploration_ceiling": run_b6_step_exploration_ceiling_run,
    }
    if executor in runners:
        runners[executor](
            artifact_root=artifact_root,
            thread_id=thread_id,
            policy_engine_service=policy_engine,
        )
        return
    if executor != "b6_anchoring_adjudication":
        raise ValueError(f"Unknown contract executor: {executor!r}")
    # The adjudication demo only means anything across the pause and the human ruling, so the
    # approval reuses the exact checkpointer and artifact store that paused the thread.
    checkpointer = InMemorySaver(serde=build_runtime_serializer())
    store = RuntimeArtifactStore(root_path=artifact_root)
    paused = run_b6_anchoring_concern_run(
        artifact_root=artifact_root,
        thread_id=thread_id,
        policy_engine_service=policy_engine,
        checkpointer=checkpointer,
        artifact_store=store,
    )
    request = paused["final_result"]["task_state"].pending_human_review_request
    if request is None:
        raise RuntimeError(
            "Anchoring scenario did not produce its expected human-review request."
        )
    approve_b6_anchoring_concern_review(
        thread_id=thread_id,
        request_id=request.request_id,
        checkpointer=checkpointer,
        artifact_store=store,
        policy_engine_service=policy_engine,
    )


# Dispatch one manifest-bound contract or live scenario without arbitrary commands.
def _execute_scenario(
    scenario: DemoScenario, *, artifact_root: Path, thread_id: str
) -> None:
    if scenario.execution_mode == CONTRACT_MODE:
        _execute_contract(
            scenario.executor, artifact_root=artifact_root, thread_id=thread_id
        )
        return
    live_runners: dict[str, Callable[..., dict[str, Any]]] = {
        "live_evidence_deficit": run_live_evidence_demo,
        "live_evidence_anchoring": run_live_evidence_anchoring_demo,
        "live_trajectory_failures": run_live_trajectory_failure_demo,
        "live_trajectory_replan_ceiling": run_live_trajectory_replan_demo,
        "live_static_database_deny": run_live_static_database_deny_demo,
    }
    if scenario.executor.startswith("live_judge_"):
        run_live_judge_case(
            case_name=scenario.executor.removeprefix("live_judge_"),
            artifact_root=artifact_root,
            thread_id=thread_id,
        )
        return
    try:
        runner = live_runners[scenario.executor]
    except KeyError as exc:
        raise ValueError(f"Unknown live executor: {scenario.executor!r}") from exc
    runner(artifact_root=artifact_root, thread_id=thread_id)


# Execute one scenario and preserve failures as first-class campaign records.
def _run_scenario_record(
    scenario: DemoScenario,
    *,
    campaign_id: str,
    campaign_root: Path,
    thread_id: str,
) -> dict[str, Any]:
    trace_path = campaign_root / thread_id / TRACE_FILE_NAME
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    provenance: dict[str, Any] = {}
    runtime_state: str | None = None
    termination_reason: str | None = None
    try:
        _execute_scenario(scenario, artifact_root=campaign_root, thread_id=thread_id)
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        runtime = trace.get("runtime", {})
        runtime_state = runtime.get("current_state")
        termination_reason = runtime.get("termination_reason")
        provenance = extract_provenance(trace)
        if scenario.pillar == "judge":
            settings = JudgeSettings.from_env()
            provenance["judge_provider"] = settings.provider
            provenance["judge_model_id"] = settings.model
        errors = validate_trace(trace, scenario.expected)
        status = "passed" if not errors else "failed_assertions"
    except (
        Exception
    ) as exc:  # a failed demo stays in the record instead of disappearing
        errors = (f"{type(exc).__name__}: {exc}",)
        status = "execution_failed"
    return {
        "record_version": "oversight_demo_run.v1",
        "campaign_id": campaign_id,
        "scenario_id": scenario.scenario_id,
        "thread_id": thread_id,
        "pillar": scenario.pillar,
        "execution_mode": scenario.execution_mode,
        "executor": scenario.executor,
        "status": status,
        "errors": list(errors),
        "runtime_state": runtime_state,
        "termination_reason": termination_reason,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "provenance": provenance,
        "condition": scenario.condition,
        "control": scenario.control,
        "trace_path": str(trace_path),
    }


# Build the incrementally persisted campaign projection from completed records.
def _campaign_summary(
    *,
    manifest: DemoManifest,
    campaign_id: str,
    started_at: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for record in records:
        status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1
    return {
        "record_version": "oversight_demo_campaign.v1",
        "pool_id": manifest.pool_id,
        "campaign_id": campaign_id,
        "manifest_version": manifest.manifest_version,
        "manifest_sha256": manifest.digest,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "planned_run_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "runs": records,
    }


# Replace one JSON record atomically so an interruption leaves no partial document.
def _write_json(path: Path, payload: Any) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


# Produce one stable UTC timestamp for evaluation records.
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Parse the bounded runner surface; scenario executors remain manifest-allowlisted.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run and automatically verify the versioned oversight demo pool."
    )
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--mode", choices=("all", CONTRACT_MODE, LIVE_MODE), default="all"
    )
    parser.add_argument(
        "--pillar", choices=("evidence", "trajectory", "static_policy", "judge")
    )
    parser.add_argument("--scenario-start", type=int, default=1)
    parser.add_argument("--scenario-end", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the manifest and print the planned demos without writing artifacts or invoking a provider.",
    )
    return parser.parse_args()


# Select a stable manifest slice after filtering contract or live execution mode.
def select_scenarios(
    manifest: DemoManifest,
    *,
    mode: str,
    pillar: str | None = None,
    scenario_start: int,
    scenario_end: int | None,
) -> tuple[DemoScenario, ...]:
    selected = [
        item
        for item in manifest.scenarios
        if (mode == "all" or item.execution_mode == mode)
        and (pillar is None or item.pillar == pillar)
    ]
    if not selected:
        raise ValueError(f"No oversight scenario matches mode={mode!r}, pillar={pillar!r}.")
    end = len(selected) if scenario_end is None else scenario_end
    if not 1 <= scenario_start <= end <= len(selected):
        raise ValueError("Invalid scenario range for the selected execution mode.")
    return tuple(selected[scenario_start - 1 : end])


# Run the immutable campaign and return non-zero on any failed assertion or execution.
def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    selected = select_scenarios(
        manifest,
        mode=args.mode,
        pillar=args.pillar,
        scenario_start=args.scenario_start,
        scenario_end=args.scenario_end,
    )
    print(f"pool_id={manifest.pool_id}")
    print(f"manifest_version={manifest.manifest_version}")
    print(f"manifest_sha256={manifest.digest}")
    for scenario in selected:
        print(
            f"{scenario.scenario_id}: mode={scenario.execution_mode} "
            f"pillar={scenario.pillar} executor={scenario.executor}"
        )
    if args.dry_run:
        return 0

    campaign_root = args.output_root.resolve() / args.campaign_id
    if campaign_root.exists():
        raise FileExistsError(f"Campaign directory already exists: {campaign_root}")
    campaign_root.mkdir(parents=True)
    (campaign_root / "campaign_manifest.json").write_bytes(
        manifest.source_path.read_bytes()
    )
    started_at = _utc_now()
    records: list[dict[str, Any]] = []
    for scenario in selected:
        thread_id = f"{args.campaign_id}-{scenario.scenario_id}-r01"
        record = _run_scenario_record(
            scenario,
            campaign_id=args.campaign_id,
            campaign_root=campaign_root,
            thread_id=thread_id,
        )
        records.append(record)
        _write_json(campaign_root / f"{thread_id}.result.json", record)
        _write_json(
            campaign_root / "campaign_summary.json",
            _campaign_summary(
                manifest=manifest,
                campaign_id=args.campaign_id,
                started_at=started_at,
                records=records,
            ),
        )
        print(f"{thread_id}: {record['status']}")
        for error in record["errors"]:
            print(f"    {error}")
    return 0 if all(record["status"] == "passed" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
