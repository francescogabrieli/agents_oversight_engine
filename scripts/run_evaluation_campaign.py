from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Protocol


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from layer5_evaluation_continuous_improvement.ingestion import TraceImporter  # noqa: E402
from layer5_evaluation_continuous_improvement.store import (  # noqa: E402
    DEFAULT_DATABASE_PATH,
    EvaluationStore,
)

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "evaluation_realistic_pool_v1.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "evaluation" / "input" / "realistic_pool_v1"
EXPECTED_MANIFEST_VERSION = "evaluation_realistic_pool.v1"
EXPECTED_POOL_ID = "realistic_pool_v1"
SCENARIO_ID_PREFIXES = tuple(f"rp{index:02d}_" for index in range(1, 17))


class ExternalExecutionKind(str, Enum):
    NONE = "none"
    EMAIL_SEND = "email_send"


class PlannedRunAction(str, Enum):
    EXECUTE = "execute"
    SKIP_EXTERNAL_EXECUTION = "skip_external_execution"


@dataclass(frozen=True)
class EvaluationScenario:
    scenario_id: str
    family: str
    task: str
    context: str
    policy_profile: str | None
    required_tools: tuple[str, ...]
    external_execution: ExternalExecutionKind
    test_recipient: str | None
    minimum_success_criterion: str


@dataclass(frozen=True)
class CampaignManifest:
    manifest_version: str
    pool_id: str
    scenarios: tuple[EvaluationScenario, ...]
    source_path: Path
    digest: str


@dataclass(frozen=True)
class PlannedRun:
    scenario: EvaluationScenario
    replica_index: int
    thread_id: str
    output_directory: Path
    action: PlannedRunAction
    # Carried explicitly so the per-run record states its campaign instead of leaving
    # Layer 5 to parse it back out of the thread id.
    campaign_id: str


@dataclass(frozen=True)
class CampaignRunResult:
    scenario_id: str
    replica_index: int
    thread_id: str
    action: PlannedRunAction
    status: str
    runtime_state: str | None
    termination_reason: str | None
    elapsed_seconds: float | None
    output_directory: Path
    error_type: str | None = None


class CommandRunnerPort(Protocol):
    # Execute one complete CLI-owned runtime session, including dashboard HITL resumes.
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[Any]: ...


# Parse the bounded command surface for a reproducible realistic-pool campaign.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the versioned realistic evaluation pool sequentially and export "
            "one canonical Layer 2 trace per scenario and replica."
        )
    )
    parser.add_argument(
        "--pool",
        choices=["realistic"],
        required=True,
        help="Evaluation pool selector; the current runner intentionally supports realistic only.",
    )
    parser.add_argument(
        "--campaign-id",
        required=True,
        help="New immutable campaign identifier, for example realistic_pilot_v1.",
    )
    parser.add_argument(
        "--replicas",
        type=_positive_int,
        required=True,
        help="Number of repetitions to execute for every scenario.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Versioned realistic-pool manifest to execute.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root below which a new campaign directory is created.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=REPO_ROOT / DEFAULT_DATABASE_PATH,
        help="SQLite evaluation database updated before and after every run.",
    )
    parser.add_argument(
        "--allow-external-execution",
        action="store_true",
        help=(
            "Allow the two SMTP scenarios, including real delivery to the manifest recipient. Without this flag they are "
            "recorded as skipped and never started."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the manifest and print the planned runs without writing or invoking Layer 2.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop the campaign after the first runtime failure; failures remain recorded.",
    )
    return parser.parse_args()


# Enforce a positive explicit repetition count before any campaign output is created.
def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("replicas must be at least 1")
    return parsed


# Load and validate the canonical realistic-pool manifest rather than duplicating tasks in Python.
def load_campaign_manifest(path: Path) -> CampaignManifest:
    resolved_path = Path(path).resolve()
    raw_bytes = resolved_path.read_bytes()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Campaign manifest is not valid UTF-8 JSON: {resolved_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Campaign manifest root must be an object.")
    _validate_manifest_root(payload)
    scenarios = tuple(_parse_scenario(item) for item in payload["scenarios"])
    _validate_scenario_collection(scenarios)
    return CampaignManifest(
        manifest_version=payload["manifest_version"],
        pool_id=payload["pool_id"],
        scenarios=scenarios,
        source_path=resolved_path,
        digest=hashlib.sha256(raw_bytes).hexdigest(),
    )


# Reject a wrong pool or an unversioned manifest before an external provider can be called.
def _validate_manifest_root(payload: dict[str, Any]) -> None:
    expected_keys = {"manifest_version", "pool_id", "scenarios"}
    if set(payload) != expected_keys:
        raise ValueError(
            "Campaign manifest fields must be exactly: manifest_version, pool_id, scenarios."
        )
    if payload["manifest_version"] != EXPECTED_MANIFEST_VERSION:
        raise ValueError(
            f"Unsupported campaign manifest version: {payload['manifest_version']!r}"
        )
    if payload["pool_id"] != EXPECTED_POOL_ID:
        raise ValueError(f"Unsupported campaign pool: {payload['pool_id']!r}")
    if not isinstance(payload["scenarios"], list) or not payload["scenarios"]:
        raise ValueError("Campaign manifest must contain at least one scenario.")


# Parse one scenario through a closed manifest contract so fixed experiment inputs cannot drift silently.
def _parse_scenario(payload: Any) -> EvaluationScenario:
    if not isinstance(payload, dict):
        raise ValueError("Every campaign scenario must be an object.")
    expected_keys = {
        "scenario_id",
        "family",
        "task",
        "context",
        "policy_profile",
        "required_tools",
        "external_execution",
        "test_recipient",
        "minimum_success_criterion",
    }
    if set(payload) != expected_keys:
        raise ValueError(
            "Campaign scenario fields must be exactly: "
            "scenario_id, family, task, context, policy_profile, required_tools, "
            "external_execution, test_recipient, minimum_success_criterion."
        )
    for field in ("scenario_id", "family", "task", "context", "minimum_success_criterion"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"Campaign scenario field '{field}' must be non-empty text.")
    policy_profile = payload["policy_profile"]
    if policy_profile is not None and (
        not isinstance(policy_profile, str) or not policy_profile.strip()
    ):
        raise ValueError("Campaign scenario policy_profile must be null or non-empty text.")
    required_tools = payload["required_tools"]
    if (
        not isinstance(required_tools, list)
        or not required_tools
        or any(not isinstance(item, str) or not item.strip() for item in required_tools)
    ):
        raise ValueError("Campaign scenario required_tools must be a non-empty text list.")
    try:
        external_execution = ExternalExecutionKind(payload["external_execution"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Campaign scenario external_execution is not supported.") from exc
    recipient = payload["test_recipient"]
    if recipient is not None and (not isinstance(recipient, str) or not recipient.strip()):
        raise ValueError("Campaign scenario test_recipient must be null or non-empty text.")
    return EvaluationScenario(
        scenario_id=payload["scenario_id"].strip(),
        family=payload["family"].strip(),
        task=payload["task"].strip(),
        context=payload["context"].strip(),
        policy_profile=policy_profile.strip() if isinstance(policy_profile, str) else None,
        required_tools=tuple(item.strip() for item in required_tools),
        external_execution=external_execution,
        test_recipient=recipient.strip() if isinstance(recipient, str) else None,
        minimum_success_criterion=payload["minimum_success_criterion"].strip(),
    )


# Enforce unique, stable IDs and keep SMTP scenarios confined to an explicit test address.
def _validate_scenario_collection(scenarios: tuple[EvaluationScenario, ...]) -> None:
    scenario_ids = [scenario.scenario_id for scenario in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("Campaign scenario_id values must be unique.")
    if len(scenario_ids) != len(SCENARIO_ID_PREFIXES) or any(
        not scenario_id.startswith(prefix)
        for scenario_id, prefix in zip(scenario_ids, SCENARIO_ID_PREFIXES, strict=True)
    ):
        raise ValueError("Realistic campaign scenarios must be the ordered rp01 through rp16 set.")
    for scenario in scenarios:
        if scenario.external_execution == ExternalExecutionKind.EMAIL_SEND:
            if scenario.test_recipient is None:
                raise ValueError(
                    f"SMTP scenario '{scenario.scenario_id}' must declare its evaluation recipient."
                )
            if scenario.test_recipient not in scenario.task:
                raise ValueError(
                    f"SMTP scenario '{scenario.scenario_id}' must name its test recipient in the task."
                )
        elif scenario.test_recipient is not None:
            raise ValueError(
                f"Non-SMTP scenario '{scenario.scenario_id}' cannot declare a test recipient."
            )


# Turn each fixed scenario and replica into one deterministic thread and artifact location.
def plan_campaign_runs(
    *,
    manifest: CampaignManifest,
    campaign_id: str,
    replicas: int,
    output_root: Path,
    allow_external_execution: bool,
) -> tuple[PlannedRun, ...]:
    _validate_campaign_id(campaign_id)
    if replicas < 1:
        raise ValueError("replicas must be at least 1")
    campaign_root = _campaign_root(output_root=output_root, campaign_id=campaign_id)
    planned_runs: list[PlannedRun] = []
    for scenario in manifest.scenarios:
        action = (
            PlannedRunAction.EXECUTE
            if allow_external_execution
            or scenario.external_execution == ExternalExecutionKind.NONE
            else PlannedRunAction.SKIP_EXTERNAL_EXECUTION
        )
        for replica_index in range(1, replicas + 1):
            thread_id = f"{campaign_id}-{scenario.scenario_id}-r{replica_index:02d}"
            planned_runs.append(
                PlannedRun(
                    scenario=scenario,
                    replica_index=replica_index,
                    thread_id=thread_id,
                    output_directory=campaign_root / thread_id,
                    action=action,
                    campaign_id=campaign_id,
                )
            )
    return tuple(planned_runs)


# Limit campaign IDs to path-safe stable identifiers before deriving thread IDs from them.
def _validate_campaign_id(campaign_id: str) -> None:
    normalized = campaign_id.strip()
    if not normalized or normalized != campaign_id:
        raise ValueError("campaign_id must be non-empty and cannot have surrounding whitespace.")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in campaign_id):
        raise ValueError(
            "campaign_id may contain only lowercase letters, digits, underscores, and hyphens."
        )


# Resolve the campaign root underneath the caller-selected artifact root without path traversal.
def _campaign_root(*, output_root: Path, campaign_id: str) -> Path:
    resolved_root = Path(output_root).resolve()
    campaign_root = (resolved_root / campaign_id).resolve()
    if campaign_root.parent != resolved_root:
        raise ValueError("campaign_id resolves outside the requested output root.")
    return campaign_root


# Execute the planned runs sequentially and preserve both successful and failed observations.
def execute_campaign(
    *,
    manifest: CampaignManifest,
    planned_runs: tuple[PlannedRun, ...],
    allow_external_execution: bool,
    fail_fast: bool,
    database_path: Path = REPO_ROOT / DEFAULT_DATABASE_PATH,
    command_runner: CommandRunnerPort = subprocess.run,
) -> tuple[CampaignRunResult, ...]:
    _validate_execution_permission(
        planned_runs=planned_runs,
        allow_external_execution=allow_external_execution,
    )
    campaign_root = _initialize_campaign_directory(
        manifest=manifest,
        planned_runs=planned_runs,
        allow_external_execution=allow_external_execution,
    )
    results: list[CampaignRunResult] = []
    with EvaluationStore(database_path) as store:
        def sync_database() -> None:
            imported = TraceImporter(store).import_paths(
                [campaign_root], experiment_id=planned_runs[0].campaign_id,
            )
            if imported.failures:
                raise RuntimeError(f"Evaluation import failed: {imported.failures}")

        sync_database()
        try:
            for planned_run in planned_runs:
                result = _execute_planned_run(
                    planned_run=planned_run,
                    command_runner=command_runner,
                    on_started=sync_database,
                )
                results.append(result)
                sync_database()
                _write_json(campaign_root / "campaign_summary.json", _campaign_summary(
                    manifest=manifest,
                    planned_runs=planned_runs,
                    allow_external_execution=allow_external_execution,
                    in_progress=True,
                ))
                if fail_fast and result.status == "failed":
                    break
        finally:
            _write_json(campaign_root / "campaign_summary.json", _campaign_summary(
                manifest=manifest,
                planned_runs=planned_runs,
                allow_external_execution=allow_external_execution,
                in_progress=False,
            ))
            sync_database()
    return tuple(results)


# Keep the execution opt-in authoritative even when this API is called outside the CLI.
def _validate_execution_permission(
    *,
    planned_runs: tuple[PlannedRun, ...],
    allow_external_execution: bool,
) -> None:
    for planned_run in planned_runs:
        expected_action = (
            PlannedRunAction.EXECUTE
            if allow_external_execution
            or planned_run.scenario.external_execution == ExternalExecutionKind.NONE
            else PlannedRunAction.SKIP_EXTERNAL_EXECUTION
        )
        if planned_run.action != expected_action:
            raise ValueError(
                "Planned run action does not match the supplied external-execution permission."
            )


# Create a new immutable campaign directory and bind it to the exact manifest digest.
def _initialize_campaign_directory(
    *,
    manifest: CampaignManifest,
    planned_runs: tuple[PlannedRun, ...],
    allow_external_execution: bool,
) -> Path:
    if not planned_runs:
        raise ValueError("Campaign must contain at least one planned run.")
    campaign_root = planned_runs[0].output_directory.parent
    if campaign_root.exists():
        raise FileExistsError(
            f"Refusing to reuse existing campaign directory: {campaign_root}"
        )
    campaign_root.mkdir(parents=True, exist_ok=False)
    for planned_run in planned_runs:
        planned_run.output_directory.mkdir()
        status = (
            "skipped_external_execution_disabled"
            if planned_run.action == PlannedRunAction.SKIP_EXTERNAL_EXECUTION
            else "not_started"
        )
        _write_json(planned_run.output_directory / "run_record.json", _run_record_payload(
            planned_run=planned_run, status=status, started_at=None, completed_at=None,
        ))
    (campaign_root / "campaign_manifest.json").write_bytes(manifest.source_path.read_bytes())
    _write_json(campaign_root / "campaign_summary.json", _campaign_summary(
        manifest=manifest,
        planned_runs=planned_runs,
        allow_external_execution=allow_external_execution,
        in_progress=True,
    ))
    return campaign_root


# Run one scenario once, then export the canonical trace or a bounded failure record.
def _execute_planned_run(
    *,
    planned_run: PlannedRun,
    command_runner: CommandRunnerPort,
    on_started: Callable[[], None],
) -> CampaignRunResult:
    started_at = _utc_now()
    if planned_run.action == PlannedRunAction.SKIP_EXTERNAL_EXECUTION:
        result = CampaignRunResult(
            scenario_id=planned_run.scenario.scenario_id,
            replica_index=planned_run.replica_index,
            thread_id=planned_run.thread_id,
            action=planned_run.action,
            status="skipped_external_execution_disabled",
            runtime_state=None,
            termination_reason=None,
            elapsed_seconds=None,
            output_directory=planned_run.output_directory,
        )
        _write_run_record(planned_run=planned_run, result=result, started_at=started_at)
        return result

    _write_json(
        planned_run.output_directory / "run_record.json",
        _run_record_payload(
            planned_run=planned_run,
            status="started",
            started_at=started_at,
            completed_at=None,
        ),
    )
    started_clock = perf_counter()
    on_started()
    try:
        completed = command_runner(
            _layer2_cli_command(planned_run),
            cwd=REPO_ROOT,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Layer 2 CLI exited with status {completed.returncode}.")
        trace_path = planned_run.output_directory / "full-trace-2.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except (Exception, KeyboardInterrupt) as exc:  # campaign failures are experimental observations.
        result = CampaignRunResult(
            scenario_id=planned_run.scenario.scenario_id,
            replica_index=planned_run.replica_index,
            thread_id=planned_run.thread_id,
            action=planned_run.action,
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            runtime_state=None,
            termination_reason=None,
            elapsed_seconds=round(perf_counter() - started_clock, 3),
            output_directory=planned_run.output_directory,
            error_type=type(exc).__name__,
        )
        _write_run_record(planned_run=planned_run, result=result, started_at=started_at)
        if isinstance(exc, KeyboardInterrupt):
            raise
        return result

    runtime = trace.get("runtime") if isinstance(trace.get("runtime"), dict) else {}
    runtime_state = _enum_text(runtime.get("current_state"))
    termination_reason = _enum_text(runtime.get("termination_reason"))
    result = CampaignRunResult(
        scenario_id=planned_run.scenario.scenario_id,
        replica_index=planned_run.replica_index,
        thread_id=planned_run.thread_id,
        action=planned_run.action,
        status=(
            "waiting_human_review"
            if runtime_state == "waiting_human_review"
            else "finished"
        ),
        runtime_state=runtime_state,
        termination_reason=termination_reason,
        elapsed_seconds=round(perf_counter() - started_clock, 3),
        output_directory=planned_run.output_directory,
    )
    _write_run_record(planned_run=planned_run, result=result, started_at=started_at)
    return result


# Build the same long-lived CLI command used for successful interactive runs such as B33.
def _layer2_cli_command(planned_run: PlannedRun) -> list[str]:
    command = [
        sys.executable,
        str(SRC_ROOT / "main_layer2.py"),
        "--thread-id",
        planned_run.thread_id,
        "--task",
        planned_run.scenario.task,
        "--trace-file",
        str(planned_run.output_directory / "full-trace-2.json"),
    ]
    if planned_run.scenario.context:
        command.extend(("--context", planned_run.scenario.context))
    if planned_run.scenario.policy_profile:
        command.extend(("--policy-profile", planned_run.scenario.policy_profile))
    return command


# Normalize enum-backed runtime fields into stable JSON and console values.
def _enum_text(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", value)
    return str(enum_value)


# Write the terminal run record without serializing runtime exception text or credentials.
def _write_run_record(
    *,
    planned_run: PlannedRun,
    result: CampaignRunResult,
    started_at: str,
) -> None:
    _write_json(
        planned_run.output_directory / "run_record.json",
        _run_record_payload(
            planned_run=planned_run,
            status=result.status,
            started_at=started_at,
            completed_at=_utc_now(),
            runtime_state=result.runtime_state,
            termination_reason=result.termination_reason,
            elapsed_seconds=result.elapsed_seconds,
            error_type=result.error_type,
        ),
    )


# Keep per-run metadata independent from the trace contract while preserving experiment provenance.
def _run_record_payload(
    *,
    planned_run: PlannedRun,
    status: str,
    started_at: str | None,
    completed_at: str | None,
    runtime_state: str | None = None,
    termination_reason: str | None = None,
    elapsed_seconds: float | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "record_version": "evaluation_campaign_run.v1",
        "campaign_id": planned_run.campaign_id,
        "scenario_id": planned_run.scenario.scenario_id,
        "family": planned_run.scenario.family,
        "replica_index": planned_run.replica_index,
        "thread_id": planned_run.thread_id,
        "action": planned_run.action.value,
        "status": status,
        "runtime_state": runtime_state,
        "termination_reason": termination_reason,
        "elapsed_seconds": elapsed_seconds,
        "error_type": error_type,
        "started_at": started_at,
        "completed_at": completed_at,
        "required_tools": list(planned_run.scenario.required_tools),
        "external_execution": planned_run.scenario.external_execution.value,
        "minimum_success_criterion": planned_run.scenario.minimum_success_criterion,
        "task": planned_run.scenario.task,
        "context": planned_run.scenario.context,
        "policy_profile": planned_run.scenario.policy_profile,
    }


# Build a stable campaign-level view that includes skipped and failed planned runs.
def _campaign_summary(
    *,
    manifest: CampaignManifest,
    planned_runs: tuple[PlannedRun, ...],
    allow_external_execution: bool,
    in_progress: bool,
) -> dict[str, Any]:
    records = [
        json.loads((run.output_directory / "run_record.json").read_text(encoding="utf-8"))
        for run in planned_runs
    ]
    status_counts: dict[str, int] = {}
    for record in records:
        status = record["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "record_version": "evaluation_campaign_summary.v1",
        "pool_id": manifest.pool_id,
        "manifest_version": manifest.manifest_version,
        "manifest_sha256": manifest.digest,
        "allow_external_execution": allow_external_execution,
        "planned_run_count": len(planned_runs),
        "completed_run_count": sum(
            record["status"] not in {"not_started", "started", "waiting_human_review"}
            for record in records
        ),
        "in_progress": in_progress,
        "status_counts": dict(sorted(status_counts.items())),
        "runs": records,
    }


# Count terminal campaign records without imposing a thesis interpretation on runtime outcomes.
def _status_counts(results: tuple[CampaignRunResult, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return dict(sorted(counts.items()))


# Emit UTF-8 JSON in one place so campaign records use a consistent audit-safe format.
def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


# Produce ISO timestamps in UTC for campaign records without relying on local terminal time.
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Print the plan before executing it so external SMTP scenarios are plainly visible to the operator.
def _print_plan(*, manifest: CampaignManifest, planned_runs: tuple[PlannedRun, ...]) -> None:
    print(f"pool_id={manifest.pool_id}")
    print(f"manifest_sha256={manifest.digest}")
    print(f"planned_runs={len(planned_runs)}")
    for planned_run in planned_runs:
        print(
            " | ".join(
                (
                    planned_run.thread_id,
                    planned_run.action.value,
                    planned_run.scenario.external_execution.value,
                )
            )
        )


# Print a compact batch outcome and return a non-zero result only for actual runtime failures.
def _print_result_summary(results: tuple[CampaignRunResult, ...]) -> int:
    counts = _status_counts(results)
    print(f"executed_records={len(results)}")
    print("status_counts=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    paused = [result.thread_id for result in results if result.status == "waiting_human_review"]
    if paused:
        print("waiting_human_review=" + ",".join(paused))
    return 1 if counts.get("failed", 0) else 0


# Run the selected campaign with no hidden retries, no auto-resume, and no simulated reviewer.
def main() -> int:
    args = parse_args()
    manifest = load_campaign_manifest(args.manifest)
    planned_runs = plan_campaign_runs(
        manifest=manifest,
        campaign_id=args.campaign_id,
        replicas=args.replicas,
        output_root=args.output_root,
        allow_external_execution=args.allow_external_execution,
    )
    _print_plan(manifest=manifest, planned_runs=planned_runs)
    if args.dry_run:
        return 0
    results = execute_campaign(
        manifest=manifest,
        planned_runs=planned_runs,
        allow_external_execution=args.allow_external_execution,
        fail_fast=args.fail_fast,
        database_path=args.database,
    )
    return _print_result_summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
