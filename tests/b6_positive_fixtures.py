from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from main_layer2 import run_layer2_flow
from layer2_runtime_governance import (
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    BehavioralRecommendedDirective,
    CapabilityAccessMode,
    EvidenceBurden,
    PlanStep,
    PlannerDecision,
    PolicyEnginePort,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolObservation,
    ToolRequest,
)
from layer2_runtime_governance.domain import TaskState
from layer2_runtime_governance.planning.replanning import ReplanDecision, ReplannerPort
from layer4_state_evidence_observability.behavioral import (
    BehavioralAssessment,
    BehavioralStorePort,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (
    build_runtime_serializer,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (
    RuntimeArtifactStore,
)

from tests.layer2_runtime_fixtures import StubPolicyEngine, _build_stub_opa_decision


B6_EVIDENCE_THREAD_ID = "b6-evidence-burden-insufficient"
B6_TRAJECTORY_THREAD_ID = "b6-trajectory-replan-ceiling"


class B6EvidencePlanner:
    planner_name = "b6_evidence_fixture_planner"
    prompt_version = "b6_evidence_fixture_v1"

    # Declare a two-source burden so one deterministic observation is insufficient.
    def plan(self, *, task_state: TaskState) -> PlannerDecision:
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="b6-evidence-step",
                    objective="Produce a factual claim grounded in independent sources.",
                    rationale="The B6 fixture deliberately declares a two-source burden.",
                    expected_outcome="A factual claim with at least two independent sources.",
                    produces_factual_claims=True,
                    evidence_burden=EvidenceBurden(
                        min_independent_sources=2,
                        require_retrieved_content=True,
                    ),
                )
            ],
            rationale="The fixture planner declares the Evidence burden explicitly.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class B6EvidenceActor(ActorPort):
    actor_name = "b6_evidence_fixture_actor"
    prompt_version = "b6_evidence_fixture_v1"

    # Request one source, then expose the deliberately incomplete selection to governance.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if _has_feedback_code(
            actor_task,
            code="insufficient_independent_sources",
        ):
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.ASK_CLARIFICATION,
                summary="The actor cannot satisfy the declared two-source burden from one source.",
                blocking_reason=(
                    "The Evidence request identified insufficient independent sources; "
                    "a human must decide whether to provide another source or relax the task."
                ),
                capability_mode=CapabilityAccessMode.NONE,
            )

        if not actor_task.observations:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.PROPOSE_ACTION,
                summary="Request one deterministic source for the factual claim.",
                capability_mode=CapabilityAccessMode.READ_ONLY,
                tool_request=ToolRequest(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    tool_name=ToolName.WEB_SEARCH,
                    request_id="b6-evidence-request-1",
                    reason="The factual step needs documentary evidence.",
                    query="B6 evidence source A",
                    max_results=1,
                    extractive_goal="Retrieve concrete source content for the claim.",
                ),
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.TOOL_DECISION,
                    summary="The fixture requests the first source only.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Submit the factual claim with the one available source.",
            output_text="The claim is supported by the available documentary source.",
            capability_mode=CapabilityAccessMode.NONE,
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                actor_self_confidence=0.82,
                evidence_refs=tuple(item.observation_id for item in actor_task.observations),
                rationale_summary="The actor selects the only retrieved source.",
            ),
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The completion intentionally selects one source against a two-source burden.",
                evidence_refs=[item.observation_id for item in actor_task.observations],
            ),
        )


class B6EvidenceToolGateway(ToolGatewayPort):
    gateway_name = "b6_evidence_fixture_gateway"

    # Return exactly one full-content observation from one origin.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="The fixture returned one concrete source.",
            content="The documentary source contains the factual claim used by the actor.",
            observations=(
                ToolObservation(
                    observation_id="b6-evidence-observation-1",
                    locator="https://b6.example/source-a",
                    content="The documentary source contains the factual claim used by the actor.",
                ),
            ),
        )


class B6TrajectoryPlanner:
    planner_name = "b6_trajectory_fixture_planner"
    prompt_version = "b6_trajectory_fixture_v1"

    # Keep the trajectory semantic and minimal; repeated replans are supplied by the actor.
    def plan(self, *, task_state: TaskState) -> PlannerDecision:
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="b6-trajectory-step-0",
                    objective="Reach a stable result through observable tool attempts.",
                    rationale="The fixture exercises the configured replan ceiling.",
                    expected_outcome="A converged result after bounded recovery.",
                )
            ],
            rationale="The fixture planner leaves convergence pressure to the runtime.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class B6TrajectoryActor(ActorPort):
    actor_name = "b6_trajectory_fixture_actor"
    prompt_version = "b6_trajectory_fixture_v1"

    # Alternate a successful observation with a typed replan request until OPA reaches the ceiling.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        step_id = actor_task.current_plan_step.step_id
        if actor_task.last_tool_result is None:
            attempt_number = actor_task.tool_call_count + 1
            return ActorResult(
                plan_step_id=step_id,
                result_type=ActorResultType.PROPOSE_ACTION,
                summary=f"Request observable trajectory basis {attempt_number}.",
                capability_mode=CapabilityAccessMode.READ_ONLY,
                tool_request=ToolRequest(
                    plan_step_id=step_id,
                    tool_name=ToolName.WEB_SEARCH,
                    request_id=f"b6-trajectory-request-{attempt_number}",
                    reason="The current semantic trajectory needs a new observable basis.",
                    query=f"B6 trajectory basis {attempt_number}",
                    max_results=1,
                    extractive_goal="Return a distinct concrete observation for convergence testing.",
                ),
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=step_id,
                    entry_type=ScratchpadEntryType.TOOL_DECISION,
                    summary=f"The fixture requests trajectory basis {attempt_number}.",
                ),
            )

        return ActorResult(
            plan_step_id=step_id,
            result_type=ActorResultType.NEEDS_REPLAN,
            summary="The actor requests a replacement trajectory after the observed attempt.",
            blocking_reason=(
                "The current semantic trajectory remains blocked after a concrete tool observation; "
                "request a replacement trajectory."
            ),
            capability_mode=CapabilityAccessMode.NONE,
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=step_id,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary="A new semantic trajectory is required after the observed basis.",
            ),
        )


class B6TrajectoryToolGateway(ToolGatewayPort):
    gateway_name = "b6_trajectory_fixture_gateway"

    # Return a distinct full-content observation for every bounded trajectory attempt.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        attempt = tool_request.request_id.rsplit("-", 1)[-1]
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary=f"The fixture returned trajectory basis {attempt}.",
            content=f"Observable trajectory basis {attempt}.",
            observations=(
                ToolObservation(
                    observation_id=f"b6-trajectory-observation-{attempt}",
                    locator=f"https://b6.example/trajectory/{attempt}",
                    content=f"Observable trajectory basis {attempt}.",
                ),
            ),
        )


class B6Replanner(ReplannerPort):
    replanner_name = "b6_fixture_replanner"
    prompt_version = "b6_fixture_replanner_v1"

    # Track the number of replacement trajectories installed by the fixture.
    def __init__(self) -> None:
        self.calls = 0

    # Install one uniquely identified replacement step per accepted replan.
    def replan(self, *, task_state: TaskState) -> ReplanDecision:
        self.calls += 1
        return ReplanDecision(
            replacement_steps=[
                PlanStep(
                    step_id=f"b6-trajectory-replan-step-{self.calls}",
                    objective="Continue from the latest observable trajectory basis.",
                    rationale="The fixture advances one bounded semantic recovery step.",
                    expected_outcome="A new observable basis is produced.",
                )
            ],
            rationale=f"Installed deterministic replacement trajectory {self.calls}.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


class B6PolicyEngine(StubPolicyEngine):
    policy_engine_name = "b6_fixture_policy_engine"

    # Keep the active Evidence and Trajectory pillars enabled in every fixture turn.
    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]:
        return {
            "require_evidence": True,
            "require_risk": False,
            "require_trajectory": True,
            "require_behavioral": False,
            "require_judge": False,
        }

    # Mirror the active OPA precedence while preserving exact pillar facts in the output.
    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]:
        actor = decision_input["base_facts"]["actor"]
        pillars = decision_input["pillars"]
        evidence_facts = pillars["evidence"].get("raw_facts", {})
        trajectory_facts = pillars["trajectory"].get("raw_facts", {})

        if (
            actor["result_type"] == ActorResultType.STEP_COMPLETED.value
            and evidence_facts.get("coverage_evaluated") is True
            and evidence_facts.get("burden_satisfied") is False
        ):
            output = _build_stub_opa_decision(
                decision_input=decision_input,
                policy_engine=self,
                directive="request_more_evidence",
                reason_code="invalid_or_missing_selected_evidence",
                summary="The declared Evidence burden is not satisfied by the selected sources.",
            )
            _annotate_evidence_gap(output=output, evidence_facts=evidence_facts)
            return output

        if (
            actor["result_type"] == ActorResultType.NEEDS_REPLAN.value
            and trajectory_facts.get("replan_count", 0) >= 3
        ):
            output = _build_stub_opa_decision(
                decision_input=decision_input,
                policy_engine=self,
                directive="wait_human_review",
                policy_decision="require_human_review",
                reason_code="replan_ceiling_reached",
                summary="The configured replan ceiling was reached after observable recovery attempts.",
            )
            _annotate_replan_ceiling(output=output)
            return output

        return super().evaluate_decision(decision_input=decision_input)


class B6BehavioralStore(BehavioralStorePort):
    # Keep deterministic B6 runs free of an unrelated durable behavioural database.
    def build_behavioral_assessment(
        self,
        *,
        thread_id: str,
        task_state: TaskState,
    ) -> BehavioralAssessment:
        return BehavioralAssessment(
            history_available=False,
            historical_run_count=0,
            feature_snapshot_version="b6_fixture_behavioral_v1",
            summary="The B6 deterministic run uses no historical behavioural sample.",
            notes=(),
            behavioral_recommended_directive=BehavioralRecommendedDirective.CONTINUE,
        )

    # The B6 artifact should contain only the runtime evidence under test.
    def persist_behavioral_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: Any,
        task_state: TaskState,
    ) -> None:
        return None

    # The B6 artifact should not create a separate judge read model.
    def persist_judge_snapshot(
        self,
        *,
        thread_id: str,
        task_state: TaskState,
    ) -> None:
        return None

    # Satisfy the combined persistence port without adding non-B6 files.
    def persist_runtime_snapshot(
        self,
        *,
        thread_id: str,
        intake_result: Any,
        task_state: TaskState,
    ) -> None:
        return None


# Detect the exact governance feedback that caused the Evidence recovery turn.
def _has_feedback_code(actor_task: ActorTask, *, code: str) -> bool:
    return any(code in feedback.finding_codes for feedback in actor_task.governance_feedback)


# Preserve the exact Evidence deficit code in the OPA-shaped pillar result.
def _annotate_evidence_gap(*, output: dict[str, Any], evidence_facts: dict[str, Any]) -> None:
    evidence = dict(output["pillar_results"]["evidence"])
    evidence["evidence_gap"] = True
    evidence["reason_codes"] = list(evidence_facts.get("deficit_codes", []))
    output["pillar_results"]["evidence"] = evidence


# Preserve the Trajectory threshold fact in the OPA-shaped pillar result.
def _annotate_replan_ceiling(*, output: dict[str, Any]) -> None:
    output["directive"] = "wait_human_review"
    output["policy_decision"] = "require_human_review"
    output["reason_codes"] = ["replan_ceiling_reached"]
    output["selected_reason"] = (
        "The configured replan ceiling was reached after observable recovery attempts."
    )
    output["requires_human_review"] = True
    trajectory = dict(output["pillar_results"]["trajectory"])
    trajectory["replan_ceiling_reached"] = True
    trajectory["reason_codes"] = ["replan_ceiling_reached"]
    output["pillar_results"]["trajectory"] = trajectory


# Run the deterministic Evidence-positive scenario with an injectable policy boundary.
def run_b6_evidence_run(
    *,
    artifact_root: Path,
    thread_id: str = B6_EVIDENCE_THREAD_ID,
    policy_engine_service: PolicyEnginePort | None = None,
) -> dict[str, Any]:
    resolved_policy_engine = policy_engine_service or B6PolicyEngine()
    return run_layer2_flow(
        raw_task_text="Produce a factual result that requires independent documentary sources.",
        context_hint="The deterministic B6 fixture supplies one source against a two-source burden.",
        thread_id=thread_id,
        demo_intake_profile="research",
        planner_service=B6EvidencePlanner(),
        actor_service=B6EvidenceActor(),
        tool_gateway_service=B6EvidenceToolGateway(),
        replanner_service=B6Replanner(),
        policy_engine_service=resolved_policy_engine,
        behavioral_store_service=B6BehavioralStore(),
        checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        artifact_store=RuntimeArtifactStore(root_path=artifact_root),
    )


# Run the deterministic Trajectory-positive scenario with an injectable policy boundary.
def run_b6_trajectory_run(
    *,
    artifact_root: Path,
    thread_id: str = B6_TRAJECTORY_THREAD_ID,
    policy_engine_service: PolicyEnginePort | None = None,
) -> dict[str, Any]:
    resolved_policy_engine = policy_engine_service or B6PolicyEngine()
    return run_layer2_flow(
        raw_task_text="Reach a stable result despite repeated bounded recovery attempts.",
        context_hint="The deterministic B6 actor emits a new observation before every replan.",
        thread_id=thread_id,
        demo_intake_profile="research",
        planner_service=B6TrajectoryPlanner(),
        actor_service=B6TrajectoryActor(),
        tool_gateway_service=B6TrajectoryToolGateway(),
        replanner_service=B6Replanner(),
        policy_engine_service=resolved_policy_engine,
        behavioral_store_service=B6BehavioralStore(),
        checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        artifact_store=RuntimeArtifactStore(root_path=artifact_root),
    )


__all__ = [
    "B6_EVIDENCE_THREAD_ID",
    "B6_TRAJECTORY_THREAD_ID",
    "run_b6_evidence_run",
    "run_b6_trajectory_run",
]
