from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


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
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    BehavioralStoreSettings,
    GovernanceDirective,
    GovernanceReasonCode,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    PolicyEvaluationResult,
    RuntimeState,
    SQLiteBehavioralStore,
    ToolName,
    ToolRequest,
    TaskState,
)
from layer2_runtime_governance.governance_components import PolicyEnginePort
from layer2_runtime_governance.hitl import build_human_review_request
from layer2_runtime_governance.runtime.governance_service import governance
from main_layer2 import main as main_layer2_cli
from main_layer2 import resume_layer2_flow
from main_layer2_dashboard import build_dashboard_session_from_trace


class HitlControlPlaneTests(unittest.TestCase):
    # Verify governance still emits a typed notification when human review is requested.
    def test_governance_notifies_when_human_review_is_requested(self) -> None:
        task_state = _build_actor_ready_state()
        notifications: list[object] = []
        behavioral_store = _build_empty_behavioral_store("hitl_notify")

        result = governance(
            task_state,
            policy_engine_service=_ReviewPolicyEngine(),
            behavioral_store_service=behavioral_store,
            thread_id="thread-hitl",
            hitl_event_callback=notifications.append,
        )

        self.assertEqual(len(notifications), 1)
        self.assertEqual(result.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(result.pending_human_review_request)
        self.assertEqual(notifications[0].current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(notifications[0].pending_human_review_request)

    # Verify the review summary remains deduplicated when governance and oversight overlap.
    def test_human_review_summary_is_not_duplicated(self) -> None:
        task_state = _build_actor_ready_state()
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.last_governance_decision = _ReviewGovernanceDecision(
            summary="Local output and replanning remain within policy. Evidence quality is weak (0.07), operational risk is high (0.61), and trajectory health is healthy (0.80).",
            oversight_summary="Evidence quality is weak (0.07), operational risk is high (0.61), and trajectory health is healthy (0.80).",
        )
        task_state.last_oversight_assessment = _ReviewOversightAssessment()

        review_request = build_human_review_request(state=task_state, thread_id="thread-review")

        self.assertEqual(
            review_request.review_summary.count(
                "Evidence quality is weak (0.07), operational risk is high (0.61), and trajectory health is healthy (0.80)."
            ),
            1,
        )

    # Verify the control-plane session can still be reconstructed directly from a trace.
    def test_dashboard_session_can_be_rebuilt_from_trace(self) -> None:
        trace = {
            "thread_id": "thread-trace",
            "intake_result": _intake_result(),
            "final_result": {"task_state": _waiting_review_state()},
        }

        session = build_dashboard_session_from_trace(trace, checkpointer=object())

        self.assertEqual(session.thread_id, "thread-trace")
        self.assertEqual(session.final_result["task_state"].current_state, RuntimeState.WAITING_HUMAN_REVIEW)

    # Verify the CLI no longer wires auto-launch tracking into run_layer2_flow.
    def test_main_layer2_cli_does_not_pass_hitl_autolaunch_callback(self) -> None:
        captured: dict[str, object] = {}

        def _fake_parse_args():
            class Args:
                task = "Inspect dashboard decoupling."
                context = "Stay local."
                policy_profile = None
                thread_id = "cli-thread"
                demo_intake_profile = "clarification"
                quiet = True
                live_trace = False
                trace_file = Path("full-trace-2.json")
                full_trace = False

            return Args()

        def _fake_run_layer2_flow(**kwargs):
            captured.update(kwargs)
            return {
                "thread_id": "cli-thread",
                "intake_result": _intake_result(),
                "final_result": {"task_state": _waiting_review_state()},
            }

        with mock.patch("main_layer2.parse_cli_args", side_effect=_fake_parse_args), mock.patch(
            "main_layer2.build_default_checkpointer",
            return_value=object(),
        ), mock.patch("main_layer2.run_layer2_flow", side_effect=_fake_run_layer2_flow), mock.patch(
            "main_layer2.write_layer2_trace_file"
        ), mock.patch("main_layer2.print_layer2_summary"):
            main_layer2_cli()

        self.assertNotIn("hitl_event_callback", captured)

    # Verify resume keeps the direct invoke result when the refreshed snapshot omits task_state.
    def test_resume_layer2_flow_prefers_invoke_result_over_partial_snapshot(self) -> None:
        resumed_state = _waiting_review_state()
        resumed_state.current_state = RuntimeState.COMPLETED

        class _FakeGraph:
            def invoke(self, _command, config=None):
                return {"task_state": resumed_state}

            def get_state(self, _config):
                return mock.Mock(values={"thread_id": "thread-resume-only"}, interrupts=[])

        with mock.patch("main_layer2.build_runtime_graph", return_value=_FakeGraph()):
            trace = resume_layer2_flow(
                thread_id="thread-resume-only",
                human_review_decision={
                    "request_id": "thread-resume-only:hitl",
                    "decision_type": "approve_continue",
                },
            )

        self.assertIs(trace["final_result"]["task_state"], resumed_state)
        self.assertEqual(trace["intake_result"], resumed_state.intake_payload)


# Build a TaskState that is ready for actor output and can be escalated into HITL.
def _build_actor_ready_state():
    intake_result = _intake_result()
    task_state = TaskState.from_intake(intake_result)
    task_state.current_state = RuntimeState.ACTING
    task_state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect official evidence.",
            rationale="Start with read-only grounding.",
            expected_outcome="Evidence gathered.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    task_state.current_plan_step_index = 0
    task_state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.PROPOSE_ACTION,
        summary="Request review before external capability use.",
        capability_mode=CapabilityAccessMode.READ_ONLY,
        external_impact=False,
        tool_request=ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Need an external source.",
            query="official source",
        ),
    )
    task_state.current_tool_request = task_state.actor_result.tool_request
    task_state.actor_turn_count = 1
    return task_state


# Build the runtime state used after governance asks for human review.
def _waiting_review_state():
    state = _build_actor_ready_state()
    state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
    return state


# Build a canonical IntakeResult for the HITL control-plane tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Inspect the HITL control plane contract.",
                context="Use deterministic fixtures.",
                constraints=["Stay read-only."],
                domain_type=DomainType.ANALYSIS,
                domain_label="hitl_control_plane_test",
                sensitivity=SensitivityLevel.MEDIUM,
                success_criteria=["Expose a typed dashboard snapshot."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=4, max_tool_calls=3),
                policy_profile_name="hitl_control_plane_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="hitl_control_plane_test_intake",
            prompt_version="hitl_control_plane_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Build an empty per-test behavioural store so control-plane tests stay deterministic.
def _build_empty_behavioral_store(suffix: str) -> SQLiteBehavioralStore:
    temp_root = REPO_ROOT / f"_test_behavioral_{suffix}"
    shutil.rmtree(temp_root, ignore_errors=True)
    return SQLiteBehavioralStore(
        settings=BehavioralStoreSettings(database_path=temp_root / "behavioral.sqlite3")
    )


# Fake policy engine that forces the runtime into the HITL branch.
class _ReviewPolicyEngine(PolicyEnginePort):
    policy_engine_name = "review_policy_engine"
    policy_bundle_version = "review_policy_v1"

    def evaluate(self, *, policy_input):
        return PolicyEvaluationResult(
            policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
            reason_codes=(GovernanceReasonCode.HUMAN_REVIEW_REQUIRED,),
            summary="Human review is required.",
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )


# Minimal governance stub for the review-summary deduplication test.
class _ReviewGovernanceDecision:
    def __init__(self, *, summary: str, oversight_summary: str) -> None:
        self.summary = summary
        self.oversight_summary = oversight_summary
        self.reason_codes = (GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED,)
        self.directive = GovernanceDirective.WAIT_HUMAN_REVIEW
        self.policy_decision = PolicyDecision.ALLOW
        self.constrains_capability_use = True
        self.evaluated_actor_result_type = ActorResultType.INTERMEDIATE_OUTPUT
        self.evaluated_tool_name = None
        self.policy_engine_name = "review_policy_engine"
        self.policy_bundle_version = "review_policy_v1"
        self.policy_directive = GovernanceDirective.CONTINUE
        self.oversight_recommended_directive = GovernanceDirective.WAIT_HUMAN_REVIEW


# Minimal oversight stub for the review-summary deduplication test.
class _ReviewOversightAssessment:
    def __init__(self) -> None:
        self.summary = "Evidence quality is weak (0.07), operational risk is high (0.61), and trajectory health is healthy (0.80)."
        self.reason_codes = tuple()
        self.evidence_quality = None
        self.operational_risk = None
        self.trajectory_health = None


if __name__ == "__main__":
    unittest.main()
