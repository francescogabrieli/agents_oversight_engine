from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

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
from layer2_runtime_governance.actor_contract import ActorResult, ActorResultType
from layer2_runtime_governance.governance import (
    GovernanceDirective,
    GovernanceReasonCode,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    evaluate_opa_first_governance,
)
from layer2_runtime_governance.state import PlanStep, PlanStepStatus, RuntimeState, TaskState


class OPAFirstGovernanceContractTests(unittest.TestCase):
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
        self.assertIn(GovernanceReasonCode.OPA_FAILURE_FAIL_CLOSED, result.governance_decision.reason_codes)


class EvaluationServiceDecouplingTests(unittest.TestCase):
    # The standalone evaluation service must not import runtime dataclasses or governance modules.
    def test_evaluation_service_has_no_runtime_imports(self) -> None:
        forbidden_roots = {"layer2_runtime_governance"}
        for path in (SRC_ROOT / "evaluation_service").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertTrue(forbidden_roots.isdisjoint(imported), path)
                if isinstance(node, ast.ImportFrom) and node.module is not None:
                    self.assertNotIn(node.module.split(".", 1)[0], forbidden_roots, path)


class _AllowPolicyEngine(PolicyEnginePort):
    policy_engine_name = "test_opa_v2_engine"
    policy_bundle_version = "layer2_governance_v2"
    policy_config_version = "layer2_governance_thresholds_v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    # Record first-pass pillar selection.
    def evaluate_required_pillars(self, *, base_facts):
        self.calls.append("required_pillars")
        return {
            "schema_version": "governance_decision_output.v1",
            "require_evidence": True,
            "require_risk": True,
            "require_trajectory": True,
            "require_behavioral": True,
            "require_judge": False,
        }

    # Return a schema-valid final decision.
    def evaluate_decision(self, *, decision_input):
        self.calls.append("decision")
        return {
            "directive": "continue",
            "policy_decision": "allow",
            "reason_codes": ["allowed_by_policy"],
            "pillar_results": {
                "static_policy": {},
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
                domain_type=DomainType.RESEARCH,
                domain_label="opa_first_test",
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
