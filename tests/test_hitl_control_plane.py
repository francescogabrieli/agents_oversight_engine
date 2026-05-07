from __future__ import annotations

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
from layer2_runtime_governance import (
    ActorResult,
    ActorResultType,
    CapabilityAccessMode,
    GovernanceDirective,
    GovernanceReasonCode,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    PolicyEvaluationResult,
    RuntimeState,
    ToolName,
    ToolRequest,
    TaskState,
)
from layer2_runtime_governance.governance import PolicyEnginePort
from layer2_runtime_governance.hitl import build_human_review_request
from layer2_runtime_governance.nodes import governance
from main_layer2_dashboard import (
    DashboardSession,
    build_dashboard_session_from_trace,
    launch_dashboard_session,
)


class AutomaticHitlControlPlaneTests(unittest.TestCase):
    def test_governance_notifies_when_human_review_is_requested(self) -> None:
        task_state = _build_actor_ready_state()
        notifications: list[object] = []

        result = governance(
            task_state,
            policy_engine_service=_ReviewPolicyEngine(),
            thread_id="thread-hitl",
            hitl_event_callback=notifications.append,
        )

        self.assertEqual(len(notifications), 1)
        self.assertEqual(result.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(result.pending_human_review_request)
        self.assertEqual(notifications[0].current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(notifications[0].pending_human_review_request)

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

    def test_governance_does_not_notify_when_review_is_not_required(self) -> None:
        task_state = _build_actor_ready_state()
        notifications: list[object] = []

        result = governance(
            task_state,
            policy_engine_service=_AllowPolicyEngine(),
            thread_id="thread-live",
            hitl_event_callback=notifications.append,
        )

        self.assertEqual(len(notifications), 0)
        self.assertEqual(result.current_state, RuntimeState.READY_TOOL_EXECUTION)
        self.assertIsNone(result.pending_human_review_request)

    def test_dashboard_launcher_is_idempotent_for_one_thread_id(self) -> None:
        session = DashboardSession(
            thread_id="thread-launch-1",
            intake_result=_intake_result(),
            final_result={"task_state": _waiting_review_state()},
            checkpointer=object(),
        )
        calls: list[tuple[str, int, bool]] = []

        def _fake_serve_dashboard(*, session, host, port, open_browser):
            calls.append((session.thread_id, port, open_browser))

        handle_one = launch_dashboard_session(
            session=session,
            open_browser=False,
            serve_dashboard_fn=_fake_serve_dashboard,
        )
        handle_one.thread.join(timeout=1)

        handle_two = launch_dashboard_session(
            session=session,
            open_browser=False,
            serve_dashboard_fn=_fake_serve_dashboard,
        )

        self.assertIs(handle_one, handle_two)
        self.assertEqual(calls, [("thread-launch-1", 8022, False)])

    def test_dashboard_session_can_be_rebuilt_from_trace(self) -> None:
        trace = {
            "thread_id": "thread-trace",
            "intake_result": _intake_result(),
            "final_result": {"task_state": _waiting_review_state()},
        }

        session = build_dashboard_session_from_trace(trace, checkpointer=object())

        self.assertEqual(session.thread_id, "thread-trace")
        self.assertEqual(session.final_result["task_state"].current_state, RuntimeState.WAITING_HUMAN_REVIEW)


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


# Build a canonical IntakeResult for the HITL auto-launch tests.
def _intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Inspect the HITL auto-launch contract.",
                context="Use deterministic fixtures.",
                constraints=["Stay read-only."],
                domain_type=DomainType.ANALYSIS,
                domain_label="hitl_autolaunch_test",
                sensitivity=SensitivityLevel.MEDIUM,
                success_criteria=["Expose a typed dashboard snapshot."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=4, max_tool_calls=3),
                policy_profile_name="hitl_autolaunch_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="hitl_autolaunch_test_intake",
            prompt_version="hitl_autolaunch_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
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


# Fake policy engine that keeps the runtime in the normal autonomous path.
class _AllowPolicyEngine(PolicyEnginePort):
    policy_engine_name = "allow_policy_engine"
    policy_bundle_version = "allow_policy_v1"

    def evaluate(self, *, policy_input):
        return PolicyEvaluationResult(
            policy_decision=PolicyDecision.ALLOW,
            reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
            summary="Autonomous continuation is allowed.",
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
