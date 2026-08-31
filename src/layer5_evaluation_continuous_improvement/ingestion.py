from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import EvaluationStore


TRACE_VERSION = "layer2_trace.v6"
RUN_RECORD_FILENAME = "run_record.json"

# The canonical trace deliberately carries no wall-clock run boundary: it is a record of
# what was decided, not of how long it took. The campaign runner does record that, plus
# the readable scenario identity, in a `run_record.json` written beside the trace. Without
# reading it the evaluation can only group runs by an opaque task-text hash and can never
# report duration - so per-family analysis and the cost of oversight both stay unavailable
# even though the data is already on disk. A run produced outside a campaign simply has no
# sibling record, and every field it would have supplied stays null.
_RUN_RECORD_VERSION = "evaluation_campaign_run.v1"


@dataclass(frozen=True)
class ImportResult:
    imported: tuple[Path, ...]
    skipped: tuple[Path, ...]
    failures: tuple[tuple[Path, str], ...]


class TraceImporter:
    # Bind trace ingestion to the dedicated evaluation projection.
    def __init__(self, store: EvaluationStore) -> None:
        self.store = store

    # Discover canonical Layer 4 traces below files or directories.
    def discover(self, paths: list[Path]) -> list[Path]:
        discovered: set[Path] = set()
        for candidate in paths:
            path = Path(candidate)
            if path.is_file():
                discovered.add(path.resolve())
            elif path.is_dir():
                discovered.update(item.resolve() for item in path.rglob("full-trace-2.json"))
        return sorted(discovered, key=str)

    # Import all discoverable traces while isolating individual file failures.
    def import_paths(
        self,
        paths: list[Path],
        *,
        experiment_id: str,
        experiment_name: str | None = None,
        experiment_description: str | None = None,
    ) -> ImportResult:
        now = _utc_now()
        self.store.ensure_experiment(
            experiment_id=experiment_id,
            name=experiment_name or experiment_id,
            description=experiment_description,
            created_at=now,
        )
        imported: list[Path] = []
        skipped: list[Path] = []
        failures: list[tuple[Path, str]] = []
        for path in self.discover(paths):
            try:
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if self.store.has_trace_digest(digest):
                    skipped.append(path)
                    continue
                payload = json.loads(raw.decode("utf-8"))
                _validate_trace(payload, source_path=path)
                projection = _project_trace(
                    payload,
                    source_path=path,
                    trace_digest=digest,
                    experiment_id=experiment_id,
                    imported_at=now,
                    run_record=_load_run_record(path),
                )
                self.store.replace_run_projection(**projection)
                imported.append(path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
                failures.append((path, str(exc)))
        return ImportResult(tuple(imported), tuple(skipped), tuple(failures))


# Load the campaign record written beside one trace, when the run came from a campaign.
def _load_run_record(trace_path: Path) -> dict[str, Any]:
    record_path = trace_path.parent / RUN_RECORD_FILENAME
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("record_version") != _RUN_RECORD_VERSION:
        return {}
    return payload


# Read one optional non-negative float from an untrusted campaign record.
def _optional_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


# Read one optional non-negative integer from an untrusted campaign record.
def _optional_index(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


# Validate the small stable boundary required by the importer.
def _validate_trace(payload: Any, *, source_path: Path) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"Trace root must be an object: {source_path}")
    if payload.get("trace_version") != TRACE_VERSION:
        raise ValueError(f"Unsupported trace version in {source_path}")
    for field in (
        "runtime",
        "intake",
        "actor",
        "tool_calls",
        "governance_passes",
        "human_review",
        "external_execution",
    ):
        if field not in payload:
            raise ValueError(f"Trace is missing '{field}': {source_path}")
    if not isinstance(payload["runtime"], dict) or not payload["runtime"].get("thread_id"):
        raise ValueError(f"Trace has no runtime thread id: {source_path}")
    if "termination_reason" not in payload["runtime"]:
        raise ValueError(f"Trace runtime is missing 'termination_reason': {source_path}")


# Project one canonical trace into the five evaluation tables.
def _project_trace(
    payload: dict[str, Any],
    *,
    source_path: Path,
    trace_digest: str,
    experiment_id: str,
    imported_at: str,
    run_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = run_record or {}
    runtime = payload["runtime"]
    intake = payload["intake"]
    run_id = str(runtime["thread_id"])
    tool_calls = _dict_list(payload.get("tool_calls"))
    governance_passes = _dict_list(payload.get("governance_passes"))
    review = payload.get("human_review") if isinstance(payload.get("human_review"), dict) else {}
    decisions = _dict_list(review.get("decision_history"))
    operations = _external_operation_rows(payload, run_id=run_id, decisions=decisions)
    run = {
        "run_id": run_id,
        "experiment_id": experiment_id,
        "task_case_id": _task_case_id(intake),
        "scenario_id": _optional_text(record.get("scenario_id")),
        "scenario_family": _optional_text(record.get("family")),
        "replica_index": _optional_index(record.get("replica_index")),
        "campaign_id": _optional_text(record.get("campaign_id")),
        "trace_path": str(source_path),
        "trace_digest": trace_digest,
        "trace_version": TRACE_VERSION,
        "configuration_fingerprint": _configuration_fingerprint(payload),
        "runtime_status": str(runtime.get("current_state") or "unknown"),
        "termination_reason": _optional_text(runtime.get("termination_reason")),
        "goal": str(intake.get("goal") or ""),
        "sensitivity": str(intake.get("sensitivity") or "unknown"),
        "execution_intent": str(intake.get("execution_intent") or "unknown"),
        "duration_seconds": _optional_seconds(record.get("elapsed_seconds")),
        "actor_turn_count": _non_negative_int(runtime.get("actor_turn_count")),
        "tool_call_count": len(tool_calls),
        "failed_tool_count": sum(item.get("success") is False for item in tool_calls),
        "replanning_count": sum(
            item.get("node_name") == "replanner" for item in _dict_list(runtime.get("step_history"))
        ),
        "governance_pass_count": len(governance_passes),
        "human_review_count": len(decisions),
        "external_operation_count": len(operations),
        "imported_at": imported_at,
    }
    governance_rows = _governance_rows(governance_passes, run_id=run_id)
    review_rows = _human_review_rows(decisions, governance_rows=governance_rows, run_id=run_id)
    return {
        "run": run,
        "governance_events": governance_rows,
        "human_reviews": review_rows,
        "external_operations": operations,
    }


# Convert governance passes into stable event rows.
def _governance_rows(items: list[dict[str, Any]], *, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(items, start=1):
        pass_index = _positive_int(item.get("pass_index"), fallback=position)
        rows.append(
            {
                "event_id": f"{run_id}:governance:{pass_index}",
                "run_id": run_id,
                "pass_index": pass_index,
                "directive": str(item.get("directive") or "unknown"),
                "policy_decision": str(item.get("policy_decision") or "unknown"),
                "reason_codes_json": json.dumps(item.get("reason_codes") or [], ensure_ascii=False),
                "requires_human_review": int(bool(item.get("requires_human_review"))),
                "human_review_was_needed": None,
            }
        )
    return rows


# Convert typed human decisions and link them to escalations in chronological order.
def _human_review_rows(
    decisions: list[dict[str, Any]],
    *,
    governance_rows: list[dict[str, Any]],
    run_id: str,
) -> list[dict[str, Any]]:
    escalations = [row for row in governance_rows if row["requires_human_review"]]
    rows: list[dict[str, Any]] = []
    for position, decision in enumerate(decisions, start=1):
        edit = decision.get("external_communication_edit") or decision.get("external_operation_edit")
        request_id = str(decision.get("request_id") or f"review-{position}")
        feedback = decision.get("note") or decision.get("clarification_text")
        rows.append(
            {
                "review_id": f"{run_id}:review:{position}:{request_id}",
                "run_id": run_id,
                "governance_event_id": (
                    escalations[position - 1]["event_id"] if position <= len(escalations) else None
                ),
                "request_id": request_id,
                "decision": str(decision.get("decision_type") or "unknown"),
                "reviewer_id": decision.get("reviewer_id"),
                "feedback": str(feedback) if feedback is not None else None,
                "human_modified": int(edit is not None),
                "review_time_seconds": None,
            }
        )
    return rows


# Extract governed external candidates and execution receipts without duplicating operations.
def _external_operation_rows(
    payload: dict[str, Any], *, run_id: str, decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    external_execution = payload.get("external_execution")
    summary = external_execution if isinstance(external_execution, dict) else {}
    receipts = _dict_list(summary.get("receipt_history"))
    modified_digests = {
        str(decision.get("approved_operation_digest"))
        for decision in decisions
        if decision.get("external_communication_edit") or decision.get("external_operation_edit")
    }
    rows: list[dict[str, Any]] = []
    for position, receipt in enumerate(receipts, start=1):
        started_at = _optional_text(receipt.get("started_at"))
        completed_at = _optional_text(receipt.get("completed_at"))
        payload_digest = _optional_text(receipt.get("payload_digest"))
        rows.append(
            {
                "row_id": f"{run_id}:external:{position}",
                "run_id": run_id,
                "operation_id": str(receipt.get("operation_id") or f"operation-{position}"),
                "capability": str(receipt.get("capability_id") or "unknown"),
                "payload_digest": payload_digest,
                "human_modified": int(payload_digest in modified_digests),
                "execution_status": str(receipt.get("status") or "unknown"),
                "operation_correct": None,
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_seconds": _duration_seconds(started_at, completed_at),
            }
        )
    if rows:
        return rows

    proposal = _governed_external_proposal(payload)
    if proposal is None:
        return []
    digest = _optional_text(proposal.get("payload_digest"))
    return [
        {
            "row_id": f"{run_id}:external:proposal",
            "run_id": run_id,
            "operation_id": str(proposal.get("operation_id") or "proposal"),
            "capability": str(proposal.get("capability_id") or "unknown"),
            "payload_digest": digest,
            "human_modified": int(digest in modified_digests),
            "execution_status": "proposed",
            "operation_correct": None,
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
        }
    ]


# Find only proposals that cross the governed external boundary.
def _governed_external_proposal(payload: dict[str, Any]) -> dict[str, Any] | None:
    actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
    candidates: list[Any] = [actor.get("external_operation")]
    review = payload.get("human_review") if isinstance(payload.get("human_review"), dict) else {}
    for key in ("pending_request", "last_request"):
        request = review.get(key)
        if isinstance(request, dict):
            candidates.append(request.get("external_operation_proposal"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("boundary") == "governed_external":
            return candidate
    return None


# Build a stable task case id from the canonical task instructions.
def _task_case_id(intake: dict[str, Any]) -> str:
    task_document = {
        "goal": intake.get("goal"),
        "context": intake.get("context"),
        "constraints": intake.get("constraints"),
        "success_criteria": intake.get("success_criteria"),
        "execution_intent": intake.get("execution_intent"),
    }
    digest = hashlib.sha256(_canonical_json(task_document).encode("utf-8")).hexdigest()
    return f"task-{digest[:16]}"


# Fingerprint the versions and policy settings that can explain run differences.
def _configuration_fingerprint(payload: dict[str, Any]) -> str:
    intake = payload.get("intake") if isinstance(payload.get("intake"), dict) else {}
    actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
    governance = _dict_list(payload.get("governance_passes"))
    configuration = {
        "planner_name": payload.get("planner_name"),
        "planner_prompt_version": payload.get("planner_prompt_version"),
        "actor_name": actor.get("actor_name"),
        "actor_prompt_version": actor.get("actor_prompt_version"),
        "policy_profile_name": intake.get("policy_profile_name"),
        "confidence_threshold": intake.get("confidence_threshold"),
        "read_only_mode": intake.get("read_only_mode"),
        "policy_bundle_versions": sorted(
            {str(item.get("policy_bundle_version")) for item in governance if item.get("policy_bundle_version")}
        ),
        "policy_config_versions": sorted(
            {str(item.get("policy_config_version")) for item in governance if item.get("policy_config_version")}
        ),
    }
    return hashlib.sha256(_canonical_json(configuration).encode("utf-8")).hexdigest()


# Serialize hashing inputs deterministically.
def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# Normalize a list of JSON objects while rejecting malformed retained items.
def _dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Expected a list of objects in canonical trace")
    return value


# Normalize counters without admitting negative or boolean values.
def _non_negative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# Normalize positive sequence numbers with a deterministic fallback.
def _positive_int(value: Any, *, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else fallback


# Normalize optional trace text.
def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# Calculate receipt duration only when both canonical timestamps are valid.
def _duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (completed - started).total_seconds())


# Produce one timezone-aware ingestion timestamp.
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
