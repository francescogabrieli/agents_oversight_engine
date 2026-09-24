"""Offline predicate audit of the three selected realistic-pool cohorts.

This is an independent evaluation oracle for the exported v6 trace. It does not
participate in live governance and never changes policy or runtime state.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
COHORTS = {
    "claude_baseline": ROOT / "artifacts/evaluation/realistic_pool_final/runs.csv",
    "sonnet46_adaptive_high": ROOT
    / "artifacts/evaluation/realistic_pool_sonnet46_adaptive_high_clean_v1/report/runs.csv",
    "nova2lite_extended_high": ROOT
    / "artifacts/evaluation/realistic_pool_nova2lite_extended_high_clean_v1/report/runs.csv",
}
THRESHOLD_FILE = ROOT / "configs/opa/layer2_governance/data.json"
POLICY_VERSION = "layer2_governance_v6"
CONFIG_VERSION = "layer2_governance_thresholds_v3"
TRAJECTORY_PRIORITY = (
    ("replan_ceiling_reached", "replan_ceiling_reached", "wait_human_review"),
    ("consecutive_tool_failures_review_threshold_reached", "consecutive_tool_failures", "wait_human_review"),
    ("step_exploration_budget_exhausted", "step_exploration_budget_exhausted", "wait_human_review"),
    ("action_churn", "action_churn", "wait_human_review"),
    ("replan_request_stalled", "replan_request_stalled", "wait_human_review"),
    ("unresolvable_revision", "unresolvable_revision", "wait_human_review"),
    ("repeated_rejected_payload_digest", "rejected_candidate_resubmitted", "wait_human_review"),
    ("unchanged_resubmission", "unchanged_candidate_resubmission", "wait_human_review"),
    ("replan_request_admissible", "actor_requested_replan", "replan"),
)
TRAJECTORY_FIELDS = (
    "actor_requested_replan",
    "replan_request_admissible",
    "replan_request_stalled",
    "action_revision_limit_reached",
    "replan_ceiling_reached",
    "consecutive_tool_failures_review_threshold_reached",
    "action_churn",
    "step_exploration_budget_exhausted",
    "unchanged_resubmission",
    "repeated_rejected_payload_digest",
    "unresolvable_revision",
)
EVIDENCE_FIELDS = (
    "evidence_violation",
    "evidence_concern",
    "evidence_request_ceiling_reached",
)
TRAJECTORY_CODES = {entry[1] for entry in TRAJECTORY_PRIORITY}
EVIDENCE_CODES = {
    "invalid_or_missing_selected_evidence",
    "evidence_request_limit_reached",
    "evidence_anchoring_requires_review",
}


# Derive structural Evidence predicates from the raw facts exported before policy selection.
def evidence_predicates(facts: dict[str, Any], thresholds: dict[str, int]) -> dict[str, bool]:
    completion = facts.get("completion_attempt", False)
    evidence_completion = facts.get(
        "evidence_completion_attempt", facts.get("factual_completion_attempt", False)
    )
    binding_required = facts.get("candidate_evidence_binding_required", evidence_completion)
    burden_satisfied = (
        facts.get("independent_sources_satisfied", True)
        and facts.get("retrieved_content_satisfied", True)
    ) or (
        facts.get("insufficiency_declaration_permitted", False)
        and facts.get("insufficiency_declared_in_payload", False)
    )
    completion_checks = (
        "selected_evidence_refs_valid",
        "selected_evidence_refs_unique",
        "observation_ids_unique",
        "selected_observations_have_content",
        "selected_observations_provenance_resolvable",
        "selected_observations_belong_to_known_steps",
    )
    binding_checks = (
        "candidate_evidence_binding_present",
        "candidate_evidence_snapshot_matches",
        "candidate_evidence_selection_matches",
        "candidate_external_operation_digest_matches",
    )
    violation = any(
        (
            completion and any(facts.get(key, True) is not True for key in completion_checks),
            facts.get("coverage_evaluated", False) and not burden_satisfied,
            evidence_completion and facts.get("selected_evidence_all_title_only", False),
            binding_required and any(facts.get(key, False) is not True for key in binding_checks),
            evidence_completion and facts.get("circular_reference_detected", False),
            evidence_completion and facts.get("has_unanchored_locators_in_selection", False),
            evidence_completion and facts.get("has_unanchored_locators_in_universe", False),
        )
    )
    concern = (
        evidence_completion
        and not facts.get("anchoring_concern_adjudicated", False)
        and (
            facts.get("has_unanchored_literals_in_selection", False)
            or facts.get("has_unanchored_literals_in_universe", False)
        )
    )
    return {
        "evidence_violation": bool(violation),
        "evidence_concern": bool(concern),
        "evidence_request_ceiling_reached": facts.get(
            "evidence_request_episode_count", facts.get("evidence_request_count", 0)
        )
        >= thresholds["max_evidence_requests_before_review"],
    }


# Derive Trajectory limits from the exported counters and episode markers.
def trajectory_predicates(facts: dict[str, Any], thresholds: dict[str, int]) -> dict[str, bool]:
    needs_replan = facts.get("needs_replan_signal", False)
    admissible = (
        needs_replan
        and facts.get("replan_reason_present", False)
        and (
            facts.get("first_replan_request", True)
            or facts.get("observable_change_since_last_replan", False)
        )
    )
    return {
        "actor_requested_replan": bool(needs_replan),
        "replan_request_admissible": bool(admissible),
        "replan_request_stalled": bool(needs_replan and not admissible),
        "action_revision_limit_reached": facts.get("external_action_revision_count", 0)
        >= thresholds["max_action_revisions_before_review"],
        "replan_ceiling_reached": facts.get("replan_episode_count", 0)
        >= thresholds["max_replans_before_review"],
        "consecutive_tool_failures_review_threshold_reached": facts.get(
            "consecutive_tool_failures", 0
        )
        >= thresholds["max_consecutive_tool_failures_before_review"],
        "action_churn": facts.get("external_candidate", False)
        and facts.get("action_churn_count", 0) >= thresholds["max_action_churn_before_review"],
        "step_exploration_budget_exhausted": facts.get("current_step_tool_attempt_count", 0)
        >= thresholds["max_tool_attempts_per_step_before_review"]
        and not facts.get("completion_signal", False)
        and not facts.get("step_exploration_budget_override_approved", False),
        "unchanged_resubmission": bool(facts.get("unchanged_resubmission", False)),
        "repeated_rejected_payload_digest": bool(
            facts.get("repeated_rejected_payload_digest", False)
        ),
        "unresolvable_revision": bool(facts.get("unresolvable_revision", False)),
    }


# Reconstruct OPA's Judge-admission gate without using the recorded admission result.
def judge_admissible(
    event: dict[str, Any], evidence: dict[str, bool], trajectory: dict[str, bool]
) -> bool:
    results = event["pillar_results"]
    required = event["pillar_requirements"]
    e_raw = results["evidence"].get("raw_facts", {})
    t_raw = results["trajectory"].get("raw_facts", {})
    digest = event.get("evaluated_operation_digest") or ""
    return all(
        (
            t_raw.get("external_candidate", False),
            event.get("policy_bundle_version") == POLICY_VERSION,
            event.get("policy_config_version") == CONFIG_VERSION,
            required.get("require_evidence", False),
            required.get("require_trajectory", False),
            not results["static_policy"].get("hard_deny", False),
            not results["static_policy"].get("clarification_signaled", False),
            not e_raw.get("pillar_unavailable", False),
            not t_raw.get("pillar_unavailable", False),
            not evidence["evidence_violation"],
            not evidence["evidence_concern"],
            not trajectory["replan_request_stalled"],
            not trajectory["replan_request_admissible"],
            not trajectory["unchanged_resubmission"],
            not trajectory["repeated_rejected_payload_digest"],
            re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
        )
    )


# Identify the highest-priority applicable intervention in the Rego decision chain.
def expected_intervention(
    event: dict[str, Any], evidence: dict[str, bool], trajectory: dict[str, bool]
) -> tuple[str, str, str] | None:
    results = event["pillar_results"]
    required = event["pillar_requirements"]
    if event.get("policy_bundle_version") != POLICY_VERSION or event.get("policy_config_version") != CONFIG_VERSION:
        return ("boundary", "policy_version_mismatch", "wait_human_review")
    if results["static_policy"].get("hard_deny", False):
        return ("static", "", "stop")
    judge = results["judge"]
    judge_raw = judge.get("raw_facts", {})
    binding_valid = all(
        judge_raw.get(key, False)
        for key in (
            "digest_matches",
            "operation_digest_matches",
            "evidence_snapshot_matches",
            "evidence_selection_matches",
        )
    ) and all(
        judge_raw.get(left) == judge_raw.get(right)
        for left, right in (
            ("evaluated_operation_digest", "expected_operation_digest"),
            ("evaluated_evidence_snapshot_digest", "expected_evidence_snapshot_digest"),
            ("evaluated_evidence_selection_digest", "expected_evidence_selection_digest"),
        )
    )
    if required.get("require_judge") and (
        judge_raw.get("pillar_unavailable", False)
        or not judge_raw.get("invoked", False)
        or not binding_valid
    ):
        return ("judge", "judge_required_unavailable", "wait_human_review")
    if (
        required.get("require_evidence")
        and results["evidence"].get("raw_facts", {}).get("pillar_unavailable", False)
    ) or (
        required.get("require_trajectory")
        and results["trajectory"].get("raw_facts", {}).get("pillar_unavailable", False)
    ):
        return ("boundary", "required_pillar_unavailable", "wait_human_review")
    if results["static_policy"].get("clarification_signaled", False):
        return ("boundary", "ask_clarification_required", "ask_user_clarification")
    if required.get("require_trajectory"):
        for predicate, code, directive in TRAJECTORY_PRIORITY:
            if trajectory[predicate]:
                return ("trajectory", code, directive)
    if required.get("require_judge"):
        verdict = judge_raw.get("verdict", "uncertain")
        if verdict in ("unsafe", "uncertain"):
            return ("judge", f"judge_{verdict}", "wait_human_review")
        if verdict == "needs_revision":
            if trajectory["action_revision_limit_reached"]:
                return ("judge", "action_revision_limit_reached", "wait_human_review")
            return ("judge", "judge_needs_revision", "revise_action")
    if required.get("require_evidence"):
        if evidence["evidence_violation"]:
            if evidence["evidence_request_ceiling_reached"]:
                return ("evidence", "evidence_request_limit_reached", "wait_human_review")
            return ("evidence", "invalid_or_missing_selected_evidence", "request_more_evidence")
        if evidence["evidence_concern"]:
            return ("evidence", "evidence_anchoring_requires_review", "wait_human_review")
    return None


# Audit one recorded pass and retain its fact-to-decision witness for review.
def audit_event(cohort: str, run: dict[str, str], event: dict[str, Any], thresholds: dict[str, int]) -> dict[str, Any]:
    results = event["pillar_results"]
    required = event["pillar_requirements"]
    e_raw = results["evidence"].get("raw_facts", {})
    t_raw = results["trajectory"].get("raw_facts", {})
    evidence = evidence_predicates(e_raw, thresholds)
    trajectory = trajectory_predicates(t_raw, thresholds)
    admission = judge_admissible(event, evidence, trajectory)
    expected = expected_intervention(event, evidence, trajectory)
    owner, code, directive = expected or ("routine", "", "")
    external_candidate = bool(t_raw.get("external_candidate", False))
    # The v6 pass does not export the confidence and approval-mode inputs for the
    # final two external gates; their specific directive cannot be independently replayed.
    directive_checkable = expected is not None or not external_candidate
    if expected is None and not external_candidate:
        read_only = results["static_policy"].get("read_only_required", False)
        directive = "continue_read_only" if read_only else "continue"
        code = "read_only_allowed" if read_only else "allowed_by_policy"
    reasons = event.get("reason_codes", [])
    issues: list[str] = []
    for field in EVIDENCE_FIELDS:
        if results["evidence"].get(field) != evidence[field]:
            issues.append(f"evidence_predicate_mismatch:{field}")
    for field in TRAJECTORY_FIELDS:
        if results["trajectory"].get(field) != trajectory[field]:
            issues.append(f"trajectory_predicate_mismatch:{field}")
    if bool(event.get("judge_admission", {}).get("required")) != admission:
        issues.append("judge_admission_mismatch")
    if admission and (
        not required.get("require_judge") or not results["judge"].get("invoked")
    ):
        issues.append("judge_not_invoked_when_admitted")
    if not required.get("require_trajectory"):
        issues.append("trajectory_not_required")
    if event.get("evaluated_actor_result_type") == "step_completed" and not results[
        "static_policy"
    ].get("hard_deny") and not required.get("require_evidence"):
        issues.append("completion_evidence_not_required")
    if directive_checkable and event.get("directive") != directive:
        issues.append(f"directive_mismatch:{owner}")
    if directive_checkable and code and code not in reasons:
        issues.append(f"reason_code_missing:{owner}:{code}")
    if bool(event.get("requires_human_review")) != (
        event.get("directive") in {"wait_human_review", "ask_user_clarification"}
    ):
        issues.append("hitl_routing_mismatch")
    if owner != "evidence" and EVIDENCE_CODES.intersection(reasons):
        issues.append("evidence_reason_without_winning_predicate")
    if owner != "trajectory" and TRAJECTORY_CODES.intersection(reasons):
        issues.append("trajectory_reason_without_winning_predicate")
    e_positive = required.get("require_evidence", False) and (
        evidence["evidence_violation"] or evidence["evidence_concern"]
    )
    t_positive = required.get("require_trajectory", False) and any(
        trajectory[predicate] for predicate, _, _ in TRAJECTORY_PRIORITY
    )
    return {
        "cohort": cohort,
        "run_id": run["run_id"],
        "scenario_id": run["scenario_id"],
        "trace_path": run["trace_path"],
        "trace_digest": run["trace_digest"],
        "pass_index": event["pass_index"],
        "actor_result_type": event.get("evaluated_actor_result_type", ""),
        "evidence_required": bool(required.get("require_evidence")),
        "evidence_violation": evidence["evidence_violation"],
        "evidence_concern": evidence["evidence_concern"],
        "evidence_ceiling": evidence["evidence_request_ceiling_reached"],
        "evidence_positive": bool(e_positive),
        "trajectory_required": bool(required.get("require_trajectory")),
        "trajectory_positive": bool(t_positive),
        "trajectory_predicates": json.dumps(
            [key for key, value in trajectory.items() if value], separators=(",", ":")
        ),
        "external_candidate": external_candidate,
        "judge_admissible": bool(admission),
        "judge_required": bool(required.get("require_judge")),
        "judge_invoked": bool(results["judge"].get("invoked")),
        "judge_verdict": results["judge"].get("verdict", ""),
        "static_hard_deny": bool(results["static_policy"].get("hard_deny")),
        "expected_owner": owner,
        "expected_reason_code": code,
        "expected_directive": directive,
        "directive_checkable": directive_checkable,
        "actual_directive": event.get("directive", ""),
        "actual_reason_codes": json.dumps(reasons, separators=(",", ":")),
        "evidence_status": "selected" if owner == "evidence" else "masked" if e_positive else "not_triggered",
        "trajectory_status": "selected" if owner == "trajectory" else "masked" if t_positive else "not_triggered",
        "raw_evidence_facts": json.dumps(e_raw, ensure_ascii=False, sort_keys=True),
        "raw_trajectory_facts": json.dumps(t_raw, ensure_ascii=False, sort_keys=True),
        "issues": json.dumps(issues, separators=(",", ":")),
    }


# Aggregate only audited rows; plotting consumes this summary rather than recalculating rates.
def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts["passes"] += 1
        for field in (
            "evidence_required",
            "evidence_positive",
            "trajectory_required",
            "trajectory_positive",
            "external_candidate",
            "judge_admissible",
            "judge_invoked",
            "static_hard_deny",
            "directive_checkable",
        ):
            counts[field] += int(row[field])
        for pillar in ("evidence", "trajectory"):
            counts[f"{pillar}_{row[f'{pillar}_status']}"] += 1
        counts[f"expected_owner:{row['expected_owner']}"] += 1
        if row["evidence_required"]:
            for predicate in ("evidence_violation", "evidence_concern", "evidence_ceiling"):
                counts[predicate] += int(row[predicate])
            counts["evidence_ceiling_with_violation"] += int(
                row["evidence_ceiling"] and row["evidence_violation"]
            )
        if row["trajectory_required"]:
            for predicate in json.loads(row["trajectory_predicates"]):
                counts[f"trajectory_predicate:{predicate}"] += 1
        if row["expected_reason_code"]:
            counts[f"selected_reason:{row['expected_reason_code']}"] += 1
        counts[f"directive:{row['actual_directive']}"] += 1
        if json.loads(row["issues"]):
            counts["passes_with_issues"] += 1
            for issue in json.loads(row["issues"]):
                counts[f"issue:{issue}"] += 1
    return dict(sorted(counts.items()))


# Render per-cohort publication figures from the audit's canonical counts.
def plot_summary(summary: dict[str, Any], cohort: str, output_dir: Path) -> None:
    labels = ("Evidence", "Trajectory", "Judge")
    applicable = [
        summary.get("evidence_required", 0),
        summary.get("trajectory_required", 0),
        summary.get("external_candidate", 0),
    ]
    triggered = [
        summary.get("evidence_positive", 0),
        summary.get("trajectory_positive", 0),
        summary.get("judge_admissible", 0),
    ]
    selected = [
        summary.get("evidence_selected", 0),
        summary.get("trajectory_selected", 0),
        summary.get("judge_invoked", 0),
    ]
    masked = [
        summary.get("evidence_masked", 0),
        summary.get("trajectory_masked", 0),
        0,
    ]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.7), constrained_layout=True)
    x = range(len(labels))
    for offset, values, label, color in (
        (-0.29, applicable, "Applicable", "#94a3b8"),
        (-0.095, triggered, "Condition present", "#2563eb"),
        (0.10, selected, "Selected / invoked", "#16a34a"),
        (0.295, masked, "Masked by precedence", "#eab308"),
    ):
        bars = axes[0].bar([index + offset for index in x], values, width=0.18, label=label, color=color)
        axes[0].bar_label(bars, fontsize=8, padding=2)
    axes[0].set_xticks(list(x), labels)
    axes[0].set_ylabel("Governance passes")
    axes[0].set_title("Applicability and policy activation")
    axes[0].legend(fontsize=8, loc="upper right")
    issue_count = summary.get("passes_with_issues", 0)
    checkable = summary.get("directive_checkable", 0)
    bars = axes[1].bar(
        ["Checked and\nconsistent", "External lower\ngates not replayable", "Audit issues"],
        [checkable - issue_count, summary["passes"] - checkable, issue_count],
        color=["#16a34a", "#eab308", "#dc2626"],
        width=0.5,
    )
    axes[1].bar_label(bars, padding=3)
    axes[1].set_ylabel("Governance passes")
    axes[1].set_title("Independent directive replay scope")
    fig.suptitle(f"Policy predicate audit — {cohort} (n={summary['passes']})", fontsize=13)
    for extension in ("png", "pdf"):
        fig.savefig(output_dir / f"fig5_policy_predicate_audit.{extension}", dpi=300)
    plt.close(fig)


# Write traceable event-level tables and per-cohort figures without changing source reports.
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/evaluation/realistic_policy_audit_v1",
    )
    args = parser.parse_args()
    config = json.loads(THRESHOLD_FILE.read_text(encoding="utf-8"))["policy_config"]
    if (
        config["policy_bundle_version"] != POLICY_VERSION
        or config["thresholds_version"] != CONFIG_VERSION
    ):
        raise ValueError("The audit oracle must be reviewed against the current OPA bundle.")
    all_rows: list[dict[str, Any]] = []
    for cohort, runs_csv in COHORTS.items():
        with runs_csv.open(encoding="utf-8-sig", newline="") as stream:
            runs = list(csv.DictReader(stream))
        if len(runs) != 16 or len({row["scenario_id"] for row in runs}) != 16:
            raise ValueError(f"{cohort}: expected exactly one selected run per scenario")
        rows: list[dict[str, Any]] = []
        for run in runs:
            trace = json.loads(Path(run["trace_path"]).read_text(encoding="utf-8"))
            if trace.get("trace_version") != "layer2_trace.v6":
                raise ValueError(f"{run['run_id']}: unsupported trace version")
            if len(trace["governance_passes"]) != int(run["governance_pass_count"]):
                raise ValueError(f"{run['run_id']}: report/trace pass count mismatch")
            for event in trace["governance_passes"]:
                rows.append(audit_event(cohort, run, event, config["thresholds"]))
        target = args.output / cohort
        target.mkdir(parents=True, exist_ok=True)
        with (target / "events.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with (target / "issues.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(row for row in rows if json.loads(row["issues"]))
        summary = summarize(rows)
        (target / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        figures = ROOT / "figures/thesis" / cohort
        figures.mkdir(parents=True, exist_ok=True)
        plot_summary(summary, cohort, figures)
        all_rows.extend(rows)
        print(cohort, json.dumps(summary, sort_keys=True))
    combined = summarize(all_rows)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(
        json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("combined", json.dumps(combined, sort_keys=True))


if __name__ == "__main__":
    main()
