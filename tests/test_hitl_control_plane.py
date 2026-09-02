from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from langgraph.types import Command

REPO_ROOT = Path(__file__).resolve().parents[1]
from layer1_intake import (
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
    BehavioralStoreSettings,
    CapabilityAccessMode,
    GovernanceDirective,
    GovernanceReasonCode,
    PlanStep,
    PlanStepStatus,
    PolicyDecision,
    RuntimeState,
    SQLiteBehavioralStore,
    TaskState,
    ToolName,
    ToolRequest,
)
from layer2_runtime_governance.hitl import build_human_review_request
from layer2_runtime_governance.runtime.governance_service import governance
from layer4_state_evidence_observability.infrastructure.review_decision_inbox import (
    SQLiteReviewDecisionInbox,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
)
from main_layer2 import _run_owned_cli_session, resume_layer2_flow
from main_layer2 import main as main_layer2_cli
from main_layer2_dashboard import build_dashboard_session_from_trace
from tests.layer2_runtime_fixtures import ReviewPolicyEngine


class HitlControlPlaneTests(unittest.TestCase):
    # Verify governance still emits a typed notification when human review is requested.
    def test_governance_notifies_when_human_review_is_requested(self) -> None:
        task_state = _build_actor_ready_state()
        notifications: list[object] = []
        temp_root = REPO_ROOT / "_test_behavioral_hitl_notify"
        behavioral_store = _build_empty_behavioral_store("hitl_notify")
        try:
            result = governance(
                task_state,
                policy_engine_service=ReviewPolicyEngine(),
                behavioral_store_service=behavioral_store,
                thread_id="thread-hitl",
                hitl_event_callback=notifications.append,
            )

            self.assertEqual(len(notifications), 1)
            self.assertEqual(result.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertIsNotNone(result.pending_human_review_request)
            self.assertEqual(notifications[0].current_state, RuntimeState.WAITING_HUMAN_REVIEW)
            self.assertIsNotNone(notifications[0].pending_human_review_request)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

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

    # A clarification stop is unusable without the agent's own question: the reason code
    # only names the policy that fired (B22 reached the operator as a bare label).
    def test_review_request_carries_the_actor_question(self) -> None:
        task_state = _build_actor_ready_state()
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.ASK_CLARIFICATION,
            summary="Devo accettare come valide anche le fonti senza data esplicita?",
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason="La finestra temporale non e' decidibile dalle evidenze raccolte.",
        )

        review_request = build_human_review_request(state=task_state, thread_id="thread-clarify")

        self.assertEqual(
            review_request.actor_summary,
            "Devo accettare come valide anche le fonti senza data esplicita?",
        )
        self.assertEqual(
            review_request.actor_blocking_reason,
            "La finestra temporale non e' decidibile dalle evidenze raccolte.",
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
            task_state = _waiting_review_state()
            task_state.current_state = RuntimeState.COMPLETED
            return {
                "thread_id": "cli-thread",
                "intake_result": _intake_result(),
                "final_result": {"task_state": task_state},
            }

        decision_inbox = mock.Mock()
        with mock.patch("main_layer2.parse_cli_args", side_effect=_fake_parse_args), mock.patch(
            "main_layer2.build_default_checkpointer",
            return_value=object(),
        ), mock.patch(
            "main_layer2.SQLiteReviewDecisionInbox",
            return_value=decision_inbox,
        ), mock.patch("main_layer2.run_layer2_flow", side_effect=_fake_run_layer2_flow), mock.patch(
            "main_layer2.write_layer2_trace_file"
        ), mock.patch("main_layer2.print_layer2_summary"), mock.patch(
            "main_layer2.close_checkpointer"
        ):
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

        with mock.patch("main_layer2.compose_layer2_runtime", return_value=_FakeGraph()):
            trace = resume_layer2_flow(
                thread_id="thread-resume-only",
                human_review_decision={
                    "request_id": "thread-resume-only:hitl",
                    "decision_type": "approve_continue",
                },
            )

        self.assertIs(trace["final_result"]["task_state"], resumed_state)
        self.assertEqual(trace["intake_result"], resumed_state.intake_payload)

    # Verify resume streams its command through the shared helper without changing its trace.
    def test_resume_layer2_flow_streams_command_with_resume_trace_tag(self) -> None:
        resumed_state = _waiting_review_state()
        resumed_state.current_state = RuntimeState.COMPLETED
        captured: dict[str, object] = {}
        progress_events: list[tuple[str, dict[str, object]]] = []

        class _FakeGraph:
            # Capture the exact graph input and yield one canonical runtime update.
            def stream(self, graph_input, *, config, stream_mode):
                captured["graph_input"] = graph_input
                captured["config"] = config
                captured["stream_mode"] = stream_mode
                yield {"finalize_bootstrap": {"task_state": resumed_state}}

            # Return the durable post-stream state used to build the public trace.
            def get_state(self, _config):
                return mock.Mock(values={"task_state": resumed_state}, interrupts=[])

        runtime_graph = _FakeGraph()
        artifact_store = mock.Mock(spec=RuntimeArtifactStore)
        artifact_store.capture_governance_exchange.return_value = mock.MagicMock()
        with (
            mock.patch(
                "main_layer2.compose_layer2_runtime",
                return_value=runtime_graph,
            ),
            mock.patch("main_layer2._print_live_trace_snapshot") as print_live_trace,
        ):
            trace = resume_layer2_flow(
                thread_id="thread-stream-resume",
                human_review_decision={
                    "request_id": "thread-stream-resume:hitl",
                    "decision_type": "approve_continue",
                },
                checkpointer=object(),
                artifact_store=artifact_store,
                progress_callback=lambda node, update: progress_events.append(
                    (node, update)
                ),
                live_trace=True,
            )

        self.assertIsInstance(captured["graph_input"], Command)
        self.assertEqual(captured["stream_mode"], "updates")
        self.assertEqual(
            progress_events,
            [("finalize_bootstrap", {"task_state": resumed_state})],
        )
        print_live_trace.assert_called_once_with(
            runtime_graph=runtime_graph,
            config={"configurable": {"thread_id": "thread-stream-resume"}},
            thread_id="thread-stream-resume",
            tag="resume_chunk",
        )
        self.assertIs(trace["final_result"]["task_state"], resumed_state)
        self.assertEqual(trace["intake_result"], resumed_state.intake_payload)

    # Verify resume persists its last checkpoint and releases default infrastructure on failure.
    def test_resume_layer2_flow_persists_failure_and_closes_default_session(
        self,
    ) -> None:
        thread_id = "thread-resume-failure"
        task_state = _waiting_review_state()
        temp_root = Path(
            tempfile.mkdtemp(prefix="_test_resume_failure_", dir=REPO_ROOT)
        )
        artifact_store = RuntimeArtifactStore(root_path=temp_root)
        default_checkpointer = object()

        class _FailingGraph:
            # Fail after session composition to exercise resume failure persistence.
            def invoke(self, _command, *, config):
                raise RuntimeError("synthetic resume failure")

            # Expose the last durable checkpoint for the diagnostic snapshot.
            def get_state(self, _config):
                return mock.Mock(values={"task_state": task_state}, interrupts=[])

        try:
            with (
                mock.patch(
                    "main_layer2.build_default_checkpointer",
                    return_value=default_checkpointer,
                ),
                mock.patch(
                    "main_layer2.compose_layer2_runtime",
                    return_value=_FailingGraph(),
                ),
                mock.patch(
                    "main_layer2._prune_expired_checkpoints"
                ) as prune_checkpoints,
                mock.patch(
                    "main_layer2.close_checkpointer"
                ) as close_runtime_checkpointer,
                self.assertRaisesRegex(
                    RuntimeError,
                    "synthetic resume failure",
                ),
            ):
                resume_layer2_flow(
                    thread_id=thread_id,
                    human_review_decision={
                        "request_id": f"{thread_id}:hitl",
                        "decision_type": "approve_continue",
                    },
                    artifact_store=artifact_store,
                )

            artifact_paths = artifact_store.build_paths(thread_id)
            failure_path = artifact_paths.thread_dir / "runtime_failure.json"
            failure_payload = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertTrue(artifact_paths.full_trace_path.exists())
            self.assertEqual(failure_payload["error_type"], "RuntimeError")
            self.assertEqual(failure_payload["runtime_state"], "waiting_human_review")
            prune_checkpoints.assert_called_once_with(
                checkpointer=default_checkpointer,
                artifact_store=artifact_store,
            )
            close_runtime_checkpointer.assert_called_once_with(default_checkpointer)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    # Verify the original CLI owner consumes the dashboard command and resumes with progress.
    def test_cli_owner_waits_for_dashboard_decision_and_resumes_in_place(self) -> None:
        temp_root = Path(tempfile.mkdtemp(prefix="_test_hitl_cli_owner_", dir=REPO_ROOT))
        inbox = SQLiteReviewDecisionInbox(storage_path=temp_root / "decisions.sqlite3")
        waiting_state = _waiting_review_state()
        waiting_state.pending_human_review_request = build_human_review_request(
            state=waiting_state,
            thread_id="cli-thread",
        )
        request_id = waiting_state.pending_human_review_request.request_id
        completed_state = _waiting_review_state()
        completed_state.current_state = RuntimeState.COMPLETED
        inbox.submit(
            thread_id="cli-thread",
            request_id=request_id,
            decision_type="approve_continue",
            decision_payload={
                "request_id": request_id,
                "decision_type": "approve_continue",
            },
            reviewer_id="operator-local",
            task_goal="Resume visibly.",
            review_summary="Approval required.",
        )

        class Args:
            task = "Resume visibly."
            context = None
            policy_profile = None
            thread_id = "cli-thread"
            demo_intake_profile = None
            quiet = False
            live_trace = False

        resumed_kwargs: dict[str, object] = {}

        def _fake_resume(**kwargs):
            resumed_kwargs.update(kwargs)
            return {
                "thread_id": "cli-thread",
                "intake_result": completed_state.intake_payload,
                "final_result": {"task_state": completed_state},
            }

        try:
            with mock.patch(
                "main_layer2.run_layer2_flow",
                return_value={
                    "thread_id": "cli-thread",
                    "intake_result": waiting_state.intake_payload,
                    "final_result": {"task_state": waiting_state},
                },
            ), mock.patch("main_layer2.resume_layer2_flow", side_effect=_fake_resume), mock.patch(
                "sys.stderr",
                new_callable=StringIO,
            ) as stderr:
                trace = _run_owned_cli_session(
                    args=Args(),
                    checkpointer=object(),
                    artifact_store=mock.Mock(),
                    decision_inbox=inbox,
                )

            self.assertEqual(
                trace["final_result"]["task_state"].current_state,
                RuntimeState.COMPLETED,
            )
            self.assertIsNotNone(resumed_kwargs["progress_callback"])
            self.assertIn("dashboard decision received", stderr.getvalue())
            submission = inbox.get_for_request(
                thread_id="cli-thread",
                request_id=request_id,
            )
            self.assertIsNotNone(submission)
            self.assertEqual(submission.status.value, "consumed")
            self.assertIsNone(inbox.get_live_owner(thread_id="cli-thread"))
        finally:
            inbox.close()
            shutil.rmtree(temp_root, ignore_errors=True)

    # Verify orphan recovery attaches to a checkpoint and never bootstraps a new run.
    def test_cli_attach_recovers_interrupted_thread_without_bootstrap(self) -> None:
        temp_root = Path(tempfile.mkdtemp(prefix="_test_hitl_cli_attach_", dir=REPO_ROOT))
        inbox = SQLiteReviewDecisionInbox(storage_path=temp_root / "decisions.sqlite3")
        waiting_state = _waiting_review_state()
        waiting_state.pending_human_review_request = build_human_review_request(
            state=waiting_state,
            thread_id="orphan-thread",
        )
        request_id = waiting_state.pending_human_review_request.request_id
        completed_state = _waiting_review_state()
        completed_state.current_state = RuntimeState.COMPLETED
        inbox.submit(
            thread_id="orphan-thread",
            request_id=request_id,
            decision_type="abort",
            decision_payload={"request_id": request_id, "decision_type": "abort"},
            reviewer_id="operator-local",
            task_goal="Recover safely.",
            review_summary="Owner unavailable.",
        )

        class Args:
            task = "ignored during attach"
            context = None
            policy_profile = None
            thread_id = None
            attach_thread = "orphan-thread"
            demo_intake_profile = None
            quiet = True
            live_trace = False

        waiting_trace = {
            "thread_id": "orphan-thread",
            "intake_result": waiting_state.intake_payload,
            "final_result": {"task_state": waiting_state},
        }
        completed_trace = {
            "thread_id": "orphan-thread",
            "intake_result": completed_state.intake_payload,
            "final_result": {"task_state": completed_state},
        }
        try:
            with mock.patch(
                "main_layer2._load_interrupted_trace_from_checkpoint",
                return_value=waiting_trace,
            ) as load_checkpoint, mock.patch(
                "main_layer2.run_layer2_flow",
            ) as bootstrap, mock.patch(
                "main_layer2.resume_layer2_flow",
                return_value=completed_trace,
            ):
                trace = _run_owned_cli_session(
                    args=Args(),
                    checkpointer=object(),
                    artifact_store=mock.Mock(),
                    decision_inbox=inbox,
                )

            load_checkpoint.assert_called_once()
            bootstrap.assert_not_called()
            self.assertEqual(
                trace["final_result"]["task_state"].current_state,
                RuntimeState.COMPLETED,
            )
        finally:
            inbox.close()
            shutil.rmtree(temp_root, ignore_errors=True)


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
                sensitivity=SensitivityLevel.MEDIUM,
                success_criteria=["Expose a typed dashboard snapshot."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
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
        self.reason_codes = ()
        self.evidence_quality = None
        self.operational_risk = None
        self.trajectory_health = None


if __name__ == "__main__":
    unittest.main()
