from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

from layer1_intake import (
    BudgetConfig,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance.actor_contract import ActorResult, ActorResultType
from layer2_runtime_governance.governance_components import (
    GovernanceDirective,
    GovernanceReasonCode,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    evaluate_opa_first_governance,
)
from layer2_runtime_governance.domain import PlanStep, PlanStepStatus, RuntimeState, TaskState
from layer4_state_evidence_observability.oversight import OversightSignalLevel


class OPAFirstGovernanceContractTests(unittest.TestCase):
    # Importing the runtime package must not load the retired Python decision combiner.
    def test_runtime_package_does_not_load_legacy_combiner(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SRC_ROOT)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import layer2_runtime_governance; "
                    "raise SystemExit('layer2_runtime_governance.decision_combiner' in sys.modules)"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    # GovernanceDecisionInput should be versioned and include score components for deterministic pillars.
    def test_governance_decision_input_contains_required_pillar_components(self) -> None:
        state = _actor_ready_state()
        result = evaluate_opa_first_governance(
            task_state=state,
            policy_engine_service=_AllowPolicyEngine(),
            thread_id="opa-first-contract",
        )

        self.assertEqual(result.decision_input["schema_version"], "governance_decision_input.v1")
        for pillar_name in ("evidence", "risk", "trajectory"):
            pillar = result.decision_input["pillars"][pillar_name]
            self.assertIn("raw_facts", pillar)
            self.assertIn("score_components", pillar)
            self.assertIn("score_formula_version", pillar)
            self.assertIn("thresholds_version", pillar)

    # OPA required_pillars is consulted before the judge-compatible decision call.
    def test_required_pillars_runs_before_decision(self) -> None:
        state = _actor_ready_state()
        engine = _AllowPolicyEngine()

        evaluate_opa_first_governance(
            task_state=state,
            policy_engine_service=engine,
            thread_id="opa-first-order",
        )

        self.assertEqual(engine.calls[0], "required_pillars")
        self.assertIn("decision", engine.calls)

    # Invalid OPA output should fail closed without falling back to the Python combiner.
    def test_invalid_opa_output_fails_closed(self) -> None:
        state = _actor_ready_state()
        result = evaluate_opa_first_governance(
            task_state=state,
            policy_engine_service=_InvalidDecisionPolicyEngine(),
            thread_id="opa-first-invalid",
        )

        self.assertEqual(result.governance_decision.directive, GovernanceDirective.WAIT_HUMAN_REVIEW)
        self.assertTrue(result.governance_decision.requires_human_review)
        self.assertIn(GovernanceReasonCode.OPA_FAILURE_FAIL_CLOSED, result.governance_decision.reason_codes)

    # A partial preflight response cannot silently disable a required pillar.
    def test_incomplete_required_pillars_response_fails_closed(self) -> None:
        state = _actor_ready_state()
        engine = _IncompletePreflightPolicyEngine()

        result = evaluate_opa_first_governance(
            task_state=state,
            policy_engine_service=engine,
            thread_id="opa-first-incomplete-preflight",
        )

        self.assertEqual(
            result.governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertIn(
            GovernanceReasonCode.OPA_FAILURE_FAIL_CLOSED,
            result.governance_decision.reason_codes,
        )
        self.assertNotIn("decision", engine.calls)

    # Final diagnostics and the governance trace must use the scored OPA pillar output.
    def test_scored_opa_levels_drive_dynamic_and_governance_summaries(self) -> None:
        result = evaluate_opa_first_governance(
            task_state=_actor_ready_state(),
            policy_engine_service=_ScoredAllowPolicyEngine(),
            thread_id="opa-first-scored-diagnostics",
        )

        self.assertEqual(result.dynamic_policy_assessment.evidence_quality.score, 0.93)
        self.assertEqual(
            result.dynamic_policy_assessment.evidence_quality.level,
            OversightSignalLevel.STRONG,
        )
        self.assertIn("evidence strong (0.93)", result.governance_decision.dynamic_policy_summary)
        self.assertNotIn("evidence weak (0.00)", result.governance_decision.dynamic_policy_summary)
        self.assertFalse(result.governance_decision.requires_human_review)


class _AllowPolicyEngine(PolicyEnginePort):
    policy_engine_name = "test_opa_v3_engine"
    policy_bundle_version = "layer2_governance_v4"
    policy_config_version = "layer2_governance_thresholds_v2"

    def __init__(self) -> None:
        self.calls: list[str] = []

    # Record first-pass pillar selection.
    def evaluate_required_pillars(self, *, base_facts):
        self.calls.append("required_pillars")
        return {
            "schema_version": "governance_decision_output.v1",
            "require_evidence": True,
            "require_risk": False,
            "require_trajectory": True,
            "require_behavioral": False,
            "require_judge": False,
        }

    # Return a schema-valid final decision.
    def evaluate_decision(self, *, decision_input):
        self.calls.append("decision")
        operation = decision_input["base_facts"]["external_operation"]
        judge_required = bool(
            operation["execution_candidate"] is True
            and operation["boundary"] == "governed_external"
        )
        return {
            "schema_version": "governance_decision_output.v1",
            "directive": "continue",
            "policy_decision": "allow",
            "reason_codes": ["allowed_by_policy"],
            "judge_admission": {
                "required": judge_required,
                "reason": (
                    "governed_external_semantic_candidate"
                    if judge_required
                    else "deterministic_gate_not_admissible"
                ),
            },
            "pillar_results": {
                "static_policy": {
                    "hard_deny": False,
                    "constraints": [],
                    "violations": [],
                    "reason_codes": [],
                    "summary": "Static deterministic guardrails passed.",
                    "read_only_required": False,
                    "clarification_signaled": False,
                    "mode": "active",
                    "decision_influence": True,
                },
                "evidence": decision_input["pillars"]["evidence"],
                "risk": decision_input["pillars"]["risk"],
                "trajectory": decision_input["pillars"]["trajectory"],
                "behavioral": decision_input["pillars"]["behavioral"],
                "judge": decision_input["pillars"]["judge"],
            },
            "selected_reason": "Allowed by test policy.",
            "rejected_alternatives": {},
            "safety_invariants_checked": ["deny_is_absorbing"],
            "requires_human_review": False,
            "policy_bundle_version": self.policy_bundle_version,
            "policy_config_version": self.policy_config_version,
        }


class _IncompletePreflightPolicyEngine(_AllowPolicyEngine):
    # Omit one mandatory boolean to exercise strict preflight validation.
    def evaluate_required_pillars(self, *, base_facts):
        self.calls.append("required_pillars")
        return {
            "schema_version": "governance_decision_output.v1",
            "require_evidence": True,
            "require_risk": False,
            "require_trajectory": True,
            "require_behavioral": False,
        }

    # Keep compatibility with the legacy protocol method.
    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            policy_decision=PolicyDecision.ALLOW,
            reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
            summary="Allowed by test policy.",
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )


class _InvalidDecisionPolicyEngine(_AllowPolicyEngine):
    # Return output that violates GovernanceDecisionOutput.
    def evaluate_decision(self, *, decision_input):
        self.calls.append("decision")
        return {"directive": "continue"}


class _ScoredAllowPolicyEngine(_AllowPolicyEngine):
    # Return the same scored pillar shape produced by the real OPA bundle.
    def evaluate_decision(self, *, decision_input):
        output = super().evaluate_decision(decision_input=decision_input)
        output["pillar_results"]["evidence"].update({"score": 0.93, "level": "strong"})
        output["pillar_results"]["risk"].update({"score": 0.0, "level": "low"})
        output["pillar_results"]["trajectory"].update({"score": 1.0, "level": "healthy"})
        return output


# Build a compact actor-ready TaskState for OPA-first contract tests.
def _actor_ready_state() -> TaskState:
    state = TaskState.from_intake(_intake_result())
    state.current_state = RuntimeState.ACTING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Produce a local answer.",
            rationale="The test only needs one actor output.",
            expected_outcome="A local result.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 1
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.INTERMEDIATE_OUTPUT,
        summary="Local output produced.",
        output_text="Local answer.",
    )
    return state


# Build deterministic Layer 1 intake for focused governance tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Produce a local answer.",
                context="Use local material only.",
                constraints=["Stay conservative."],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Return a traceable local result."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=3, max_tool_calls=2),
                policy_profile_name="test_profile",
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="test_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


if __name__ == "__main__":
    unittest.main()
