from __future__ import annotations

import json
import sys
import shutil
import unittest
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer2_runtime_governance.evaluation import (
    evaluate_layer2_trace_inputs,
    layer2_evaluation_report_to_dict,
    layer2_evaluation_report_to_markdown,
    write_layer2_evaluation_report,
)


class Layer2EvaluationTests(unittest.TestCase):
    def test_real_trace_export_is_evaluated_successfully(self) -> None:
        report = evaluate_layer2_trace_inputs([REPO_ROOT / "full-trace.json"])

        self.assertEqual(report.aggregate.parsed_runs, 1)
        self.assertEqual(report.runs[0].status, "ok")
        self.assertIsNotNone(report.runs[0].runtime_state)
        self.assertTrue(report.runs[0].tool_call_count >= 0)

    def test_directory_evaluation_aggregates_core_metrics(self) -> None:
        with _workspace_temp_dir() as temp_path:
            _write_trace_json(temp_path / "completed.json", _completed_trace_payload())
            _write_trace_json(temp_path / "review.json", _review_trace_payload())
            _write_trace_json(temp_path / "aborted.json", _aborted_trace_payload())

            report = evaluate_layer2_trace_inputs([temp_path])

        self.assertEqual(report.aggregate.total_inputs, 3)
        self.assertEqual(report.aggregate.parsed_runs, 3)
        self.assertEqual(report.aggregate.failed_runs, 0)
        self.assertEqual(
            report.aggregate.task_outcome_distribution,
            {
                "aborted": 1,
                "completed": 1,
                "waiting_human_review": 1,
            },
        )
        self.assertEqual(
            report.aggregate.governance_directive_distribution,
            {
                "continue": 1,
                "continue_read_only": 1,
                "stop": 1,
                "wait_human_review": 1,
            },
        )
        self.assertEqual(report.aggregate.governance_escalation_rate, 0.5)
        self.assertAlmostEqual(report.aggregate.human_review_frequency, 1 / 3)
        self.assertEqual(report.aggregate.human_review_decision_distribution, {
            "approve_continue": 1,
            "request_more_evidence": 1,
        })
        self.assertEqual(report.aggregate.unresolved_human_review_count, 1)
        self.assertEqual(
            report.aggregate.evidence_support_distribution,
            {
                "detailed_extracted": 1,
                "high_level_summary": 2,
                "primary_legal": 1,
            },
        )
        self.assertEqual(report.aggregate.tool_call_total, 4)
        self.assertEqual(report.aggregate.failed_tool_total, 1)
        self.assertEqual(report.aggregate.duplicate_block_total, 1)
        self.assertEqual(report.aggregate.failed_tool_rate, 0.25)
        self.assertEqual(report.aggregate.duplicate_block_rate, 0.25)
        self.assertEqual(report.aggregate.oversight_assessment_total, 4)
        self.assertEqual(report.aggregate.low_score_assessment_total, 2)
        self.assertEqual(report.aggregate.degraded_trajectory_assessment_total, 1)
        review_run = next(run for run in report.runs if run.runtime_state == "waiting_human_review")
        aborted_run = next(run for run in report.runs if run.runtime_state == "aborted")
        self.assertTrue(review_run.human_review_activity)
        self.assertEqual(aborted_run.duplicate_block_count, 1)

    def test_malformed_json_is_reported_not_raised(self) -> None:
        with _workspace_temp_dir() as temp_path:
            _write_trace_json(temp_path / "good.json", _completed_trace_payload())
            (temp_path / "bad.json").write_text("{ not valid json", encoding="utf-8")

            report = evaluate_layer2_trace_inputs([temp_path])

        self.assertEqual(report.aggregate.total_inputs, 2)
        self.assertEqual(report.aggregate.parsed_runs, 1)
        self.assertEqual(report.aggregate.failed_runs, 1)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].issue_type, "parse_failed")
        self.assertIn("bad.json", report.issues[0].source_path)
        self.assertIn("parse_failed", {run.status for run in report.runs})

    def test_report_serialization_is_stable_for_the_same_report(self) -> None:
        report = evaluate_layer2_trace_inputs([REPO_ROOT / "full-trace.json"])

        payload_one = layer2_evaluation_report_to_dict(report)
        payload_two = layer2_evaluation_report_to_dict(report)
        markdown_one = layer2_evaluation_report_to_markdown(report)
        markdown_two = layer2_evaluation_report_to_markdown(report)

        self.assertEqual(payload_one, payload_two)
        self.assertEqual(markdown_one, markdown_two)
        self.assertEqual(
            list(payload_one.keys()),
            [
                "report_version",
                "generated_at_utc",
                "input_paths",
                "discovered_files",
                "runs",
                "aggregate",
                "issues",
            ],
        )
        self.assertIn("Layer 2 Offline Evaluation Report", markdown_one)
        self.assertIn("Aggregate Metrics", markdown_one)

    def test_evaluation_report_can_be_written_to_disk(self) -> None:
        with _workspace_temp_dir() as temp_path:
            report = evaluate_layer2_trace_inputs([REPO_ROOT / "full-trace.json"])
            json_path = temp_path / "report.json"
            md_path = temp_path / "report.md"

            written_json, written_md = write_layer2_evaluation_report(
                report,
                output_json_path=json_path,
                output_markdown_path=md_path,
            )

            self.assertTrue(written_json.exists())
            self.assertTrue(written_md.exists())
            self.assertEqual(
                json.loads(written_json.read_text(encoding="utf-8"))["report_version"],
                "layer2_offline_evaluation_v2",
            )

    def test_requirement_mismatch_counts_distinguish_typed_and_heuristic_sources(self) -> None:
        with _workspace_temp_dir() as temp_path:
            _write_trace_json(temp_path / "typed.json", _typed_requirement_mismatch_payload())
            _write_trace_json(temp_path / "heuristic.json", _heuristic_requirement_mismatch_payload())

            report = evaluate_layer2_trace_inputs([temp_path])

        self.assertEqual(report.aggregate.parsed_runs, 2)
        self.assertEqual(report.aggregate.typed_requirement_mismatch_total, 1)
        self.assertEqual(report.aggregate.heuristic_requirement_mismatch_total, 1)


# Build a trace payload with deterministic metrics for the evaluation tests.
def _completed_trace_payload() -> dict[str, object]:
    return _base_trace_payload(
        thread_id="completed-trace",
        current_state="completed",
        governance_directives=["continue", "continue_read_only"],
        human_review={
            "pending_request": None,
            "last_request": None,
            "last_decision": None,
            "decision_history": [],
        },
        observed_evidence=[
            {"support_level": "high_level_summary"},
            {"support_level": "detailed_extracted"},
        ],
        tool_calls=[
            {"tool_name": "web_search", "success": True, "summary": "search ok", "error_message": None},
            {"tool_name": "fetch_url_text", "success": True, "summary": "fetch ok", "error_message": None},
        ],
        oversight_assessments=[
            {
                "evidence_quality": {"score": 0.8, "level": "strong"},
                "operational_risk": {"score": 0.7, "level": "medium"},
                "trajectory_health": {"score": 0.9, "level": "healthy"},
            },
            {
                "evidence_quality": {"score": 0.7, "level": "strong"},
                "operational_risk": {"score": 0.8, "level": "medium"},
                "trajectory_health": {"score": 0.85, "level": "healthy"},
            },
        ],
    )


# Build a trace payload that is paused for human review.
def _review_trace_payload() -> dict[str, object]:
    return _base_trace_payload(
        thread_id="review-trace",
        current_state="waiting_human_review",
        governance_directives=["wait_human_review"],
        human_review={
            "pending_request": {
                "request_id": "review-trace:human_review:1:step_1",
                "thread_id": "review-trace",
                "current_state": "waiting_human_review",
                "resume_state": "acting",
                "current_plan_step_id": "step_1",
            },
            "last_request": {
                "request_id": "review-trace:human_review:1:step_1",
                "thread_id": "review-trace",
                "current_state": "waiting_human_review",
                "resume_state": "acting",
                "current_plan_step_id": "step_1",
            },
            "last_decision": {
                "request_id": "review-trace:human_review:1:step_1",
                "decision_type": "approve_continue",
            },
            "decision_history": [
                {
                    "request_id": "review-trace:human_review:1:step_1",
                    "decision_type": "approve_continue",
                },
                {
                    "request_id": "review-trace:human_review:1:step_1",
                    "decision_type": "request_more_evidence",
                },
            ],
        },
        observed_evidence=[
            {"support_level": "high_level_summary"},
        ],
        tool_calls=[
            {"tool_name": "web_search", "success": True, "summary": "search ok", "error_message": None},
        ],
        oversight_assessments=[
            {
                "evidence_quality": {"score": 0.55, "level": "moderate"},
                "operational_risk": {"score": 0.3, "level": "low"},
                "trajectory_health": {"score": 0.5, "level": "watch"},
            },
        ],
    )


# Build a trace payload that was aborted after a duplicate tool attempt.
def _aborted_trace_payload() -> dict[str, object]:
    return _base_trace_payload(
        thread_id="aborted-trace",
        current_state="aborted",
        governance_directives=["stop"],
        human_review={
            "pending_request": None,
            "last_request": None,
            "last_decision": None,
            "decision_history": [],
        },
        observed_evidence=[
            {"support_level": "primary_legal"},
        ],
        tool_calls=[
            {
                "tool_name": "fetch_url_text",
                "success": False,
                "summary": "Duplicate tool request blocked before execution.",
                "error_message": "The same tool request was already executed for this plan step.",
            },
        ],
        oversight_assessments=[
            {
                "evidence_quality": {"score": 0.3, "level": "weak"},
                "operational_risk": {"score": 0.7, "level": "high"},
                "trajectory_health": {"score": 0.2, "level": "degraded"},
            },
        ],
    )


# Build the minimal JSON trace structure accepted by the offline evaluation pipeline.
def _base_trace_payload(
    *,
    thread_id: str,
    current_state: str,
    governance_directives: list[str],
    human_review: dict[str, object],
    observed_evidence: list[dict[str, object]],
    tool_calls: list[dict[str, object]],
    oversight_assessments: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "runtime": {
            "thread_id": thread_id,
            "current_state": current_state,
            "bootstrap_completed": True,
            "runtime_flags": [],
            "step_history": [],
            "actor_turn_count": 2,
            "tool_call_count": len(tool_calls),
        },
        "intake": {
            "goal": "Evaluate the governed runtime.",
            "context": "Use offline evaluation fixtures.",
            "domain_type": "analysis",
            "domain_label": "evaluation_test",
            "sensitivity": "low",
            "success_criteria": ["Produce an offline evaluation report."],
            "constraints": ["Stay read-only."],
            "parse_status": "complete",
            "parser_name": "evaluation_fixture",
            "prompt_version": "evaluation_v1",
            "schema_version": "1.0.0",
            "policy_profile_name": "evaluation_profile",
            "confidence_threshold": 0.65,
            "max_steps": 4,
            "max_tool_calls": 3,
            "read_only_mode": True,
        },
        "actor": {
            "result_type": "final_output" if current_state == "completed" else None,
        },
        "execution": {
            "final_output_text": "Done." if current_state == "completed" else None,
        },
        "plan_steps": [],
        "current_plan_step_id": None,
        "tool_calls": deepcopy(tool_calls),
        "observed_evidence": deepcopy(observed_evidence),
        "governance_decisions": [{"directive": directive} for directive in governance_directives],
        "oversight_assessments": deepcopy(oversight_assessments),
        "human_review": deepcopy(human_review),
    }


# Write a trace payload to disk for evaluation tests.
def _write_trace_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# Build a trace payload that should count as a typed requirement mismatch.
def _typed_requirement_mismatch_payload() -> dict[str, object]:
    payload = _base_trace_payload(
        thread_id="typed-mismatch",
        current_state="completed",
        governance_directives=["continue"],
        human_review={
            "pending_request": None,
            "last_request": None,
            "last_decision": None,
            "decision_history": [],
        },
        observed_evidence=[{"support_level": "local_only"}],
        tool_calls=[],
        oversight_assessments=[],
    )
    payload["plan_steps"] = [
        {
            "step_id": "step_1",
            "objective": "Produce a legally grounded answer.",
            "rationale": "The step is explicit about legal precision.",
            "expected_outcome": "A precise answer.",
            "step_kind": "compliance_or_legal",
            "evidence_requirement": "primary_legal",
            "claim_precision_ceiling": "primary_legal",
            "resolved_required_evidence_support_level": "primary_legal",
            "resolved_required_evidence_support_source": "plan_step_evidence_requirement",
            "status": "completed",
        }
    ]
    payload["current_plan_step_id"] = "step_1"
    return payload


# Build a trace payload that should count as a heuristic fallback mismatch.
def _heuristic_requirement_mismatch_payload() -> dict[str, object]:
    payload = _base_trace_payload(
        thread_id="heuristic-mismatch",
        current_state="completed",
        governance_directives=["continue"],
        human_review={
            "pending_request": None,
            "last_request": None,
            "last_decision": None,
            "decision_history": [],
        },
        observed_evidence=[{"support_level": "local_only"}],
        tool_calls=[],
        oversight_assessments=[],
    )
    payload["plan_steps"] = [
        {
            "step_id": "step_1",
            "objective": "Summarize the topic at a high level.",
            "rationale": "The step is only discoverable from wording.",
            "expected_outcome": "A concise summary.",
            "step_kind": None,
            "evidence_requirement": None,
            "claim_precision_ceiling": None,
            "resolved_required_evidence_support_level": "high_level_summary",
            "resolved_required_evidence_support_source": "heuristic_step",
            "status": "completed",
        }
    ]
    payload["current_plan_step_id"] = "step_1"
    return payload


# Use a workspace-local throwaway directory so the test environment does not depend on temp permissions.
@contextmanager
def _workspace_temp_dir() -> Path:
    temp_dir = REPO_ROOT / "artifacts" / "runtime" / f"_layer2_eval_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
