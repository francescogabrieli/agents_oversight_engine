import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from unittest.mock import patch
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


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
    ActorExecutionError,
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    BehavioralAssessment,
    BehavioralRecommendedDirective,
    BehavioralStoreSettings,
    CapabilityAccessMode,
    DynamicPolicyAssessment,
    DynamicPolicyRecommendedDirective,
    EvidenceSourceKind,
    EvidenceQualityAssessment,
    ExternalCommunicationDraft,
    ExternalCommunicationPhase,
    GovernanceDecision,
    GovernanceDirective,
    GovernanceReasonCode,
    JudgeAssessment,
    JudgePort,
    JudgeRecommendedDirective,
    PlanStep,
    PlanStepKind,
    PlanStepStatus,
    PlannerDecision,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    RuntimeFlag,
    RuntimeState,
    SQLiteBehavioralStore,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepRecord,
    StepValidation,
    StepValidationDecision,
    TaskState,
    HumanReviewExternalEdit,
    EvidenceSupportLevel,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
    BedrockNovaActor,
    BedrockNovaActorSettings,
    OperationalRiskAssessment,
    OversightReasonCode,
    OversightSignalLevel,
    TrajectoryHealthAssessment,
    build_actor_task,
    build_default_actor,
    HumanReviewDecision,
    HumanReviewDecisionType,
    build_human_review_request,
    build_policy_evaluation_input,
    build_runtime_graph,
    replanner,
)
from layer2_runtime_governance.infrastructure.checkpointing import (
    build_runtime_serializer,
)
from layer2_runtime_governance.infrastructure.runtime_artifacts import RuntimeArtifactStore
from layer2_runtime_governance.hitl import _apply_human_review_decision, human_review
from main_layer2 import build_compact_trace, write_layer2_trace_file
from main_layer2 import run_layer2_flow
from layer2_runtime_governance.planning.replanning import (
    OpenAIReplanner,
    OpenAIReplannerSettings,
    ReplanDecision,
    ReplannerPort,
    build_replanner_system_prompt,
    build_replanner_user_prompt,
)
from layer2_runtime_governance.planning import (
    OpenAIPlanner,
    OpenAIPlannerSettings,
    build_planner_system_prompt,
)
from layer2_runtime_governance.execution import build_actor_system_prompt, build_actor_user_prompt
from layer2_runtime_governance.governance_components import (
    OPAHttpPolicyEngine,
    OPAHttpPolicyEngineSettings,
    build_opa_input_document,
    evaluate_opa_first_governance,
)
from layer2_runtime_governance.runtime.actor_service import _record_actor_execution
from layer2_runtime_governance.runtime.governance_service import (
    _apply_governance_decision,
    governance,
)
from layer2_runtime_governance.runtime.planning_service import planner
from layer2_runtime_governance.runtime.tool_service import observe_tool_result
from layer2_runtime_governance.tool_contract import ObservedEvidence
import layer2_runtime_governance.execution.providers.bedrock_runtime as bedrock_client_module


# Build Bedrock actor settings for legacy behavior fixtures without making network calls.
def _build_bedrock_actor_test_settings(*, timeout_seconds: float = 1.0) -> BedrockNovaActorSettings:
    return BedrockNovaActorSettings(
        model_id="eu.amazon.nova-2-lite-v1:0",
        region_name="eu-central-1",
        access_key_id="test-key",
        secret_access_key="test-secret-key",
        read_timeout_seconds=timeout_seconds,
    )


# Preserve the existing actor-behavior fixture calls while they exercise BedrockNovaActor.
OpenRouterActor = BedrockNovaActor


# Translate the former fixture-only OpenRouter settings into Nova test settings.
def OpenRouterActorSettings(**kwargs: object) -> BedrockNovaActorSettings:
    timeout_seconds = kwargs.get("timeout_seconds", 1.0)
    return _build_bedrock_actor_test_settings(timeout_seconds=float(timeout_seconds))


class StubPlanner:
    planner_name = "stub_planner"
    prompt_version = "stub_v1"

    # Return a stable semantic mini-plan so tests never depend on network calls.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Collect the minimum official evidence needed for the task.",
                    rationale="Research tasks should begin from documentary grounding.",
                    expected_outcome="A compact evidence base relevant to the goal.",
                    step_kind=PlanStepKind.SOURCE_DISCOVERY,
                    evidence_requirement=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                    claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                ),
                PlanStep(
                    step_id="step_2",
                    objective="Synthesize the grounded result.",
                    rationale="The runtime should synthesize only after evidence collection.",
                    expected_outcome="A concise grounded output.",
                    step_kind=PlanStepKind.FINAL_SYNTHESIS,
                    evidence_requirement=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                    claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                ),
            ],
            rationale="The stub planner selected a short evidence-first trajectory.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class StubActor(ActorPort):
    actor_name = "stub_actor"
    prompt_version = "stub_actor_v1"

    # Drive one deterministic actor/tool/observe loop across two semantic steps.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.current_plan_step.step_id == "step_1" and not any(
            item.source_kind == EvidenceSourceKind.TOOL_OBSERVATION
            for item in actor_task.observed_evidence
        ):
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.PROPOSE_ACTION,
                summary="Request a read-only evidence search for the first step.",
                capability_mode=CapabilityAccessMode.READ_ONLY,
                external_impact=False,
                tool_request=ToolRequest(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    tool_name=ToolName.WEB_SEARCH,
                    reason="The first semantic step requires authoritative documentary evidence.",
                    query="Digital Markets Act official EU sources",
                    max_results=3,
                    extractive_goal="Find official EU sources about the Digital Markets Act.",
                ),
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.TOOL_DECISION,
                    summary="The current step needs external documentary evidence.",
                ),
            )

        if actor_task.current_plan_step.step_id == "step_1":
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="The first step is complete and produced grounded evidence.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="Collected grounded evidence from official sources.",
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                    summary="The observed source satisfies the evidence collection step.",
                    evidence_refs=[item.evidence_id for item in actor_task.observed_evidence],
                ),
                step_validation=StepValidation(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    objective_satisfied=True,
                    evidence_sufficient=True,
                    confidence=0.86,
                    next_action=StepValidationDecision.COMPLETE_STEP,
                    rationale_summary="Official evidence was collected for the step objective.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="The task can now be completed from the grounded evidence.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="The DMA is an EU regulation that targets gatekeeper platforms.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The final synthesis satisfies the remaining plan step.",
                evidence_refs=[item.evidence_id for item in actor_task.observed_evidence],
            ),
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.84,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The synthesis uses the collected official evidence.",
            ),
        )


class StubToolGateway(ToolGatewayPort):
    gateway_name = "stub_tool_gateway"

    # Return one stable tool result so the actor can continue its local loop.
    def execute(self, *, tool_request: ToolRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary="Stub web search completed successfully.",
            content="Official EU sources describe the Digital Markets Act as a regulation for gatekeeper platforms.",
            sources=(
                ToolSource(
                    title="European Commission - Digital Markets Act",
                    locator="https://commission.europa.eu/dma",
                    snippet=(
                        "Official EU material describes DMA rules and obligations for "
                        "gatekeepers and core platform services."
                    ),
                ),
            ),
        )


class RepeatFetchActor(ActorPort):
    actor_name = "repeat_fetch_actor"
    prompt_version = "repeat_fetch_v1"

    # Always repeat the same URL so the gateway duplicate guard can be tested.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.last_tool_result is not None and not actor_task.last_tool_result.success:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.FINAL_OUTPUT,
                summary="Duplicate request protection was observed.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="The duplicate fetch was blocked before external re-execution.",
                step_validation=StepValidation(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    objective_satisfied=True,
                    evidence_sufficient=True,
                    confidence=0.9,
                    next_action=StepValidationDecision.COMPLETE_STEP,
                    rationale_summary="The duplicate-request guard was verified.",
                ),
            )
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Repeat the same fetch request.",
            capability_mode=CapabilityAccessMode.READ_ONLY,
            external_impact=False,
            tool_request=ToolRequest(
                plan_step_id=actor_task.current_plan_step.step_id,
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Repeat same source.",
                url="https://example.org/source",
            ),
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.TOOL_DECISION,
                summary="Repeated fetch request.",
            ),
        )


class SingleStepPlanner:
    planner_name = "single_step_planner"
    prompt_version = "single_step_v1"

    # Keep the duplicate-tool test scoped to one step.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Fetch one source once.",
                    rationale="The test needs one current step.",
                    expected_outcome="One source fetched.",
                )
            ],
            rationale="Single-step plan.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class LocalProgressPlanner:
    planner_name = "local_progress_planner"
    prompt_version = "local_progress_v1"

    # Keep progress-validation tests independent from documentary evidence checks.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Complete a local result in two bounded passes.",
                    rationale="The fixture tests step validation rather than research.",
                    expected_outcome="A locally validated final result.",
                    step_kind=PlanStepKind.LOCAL_TRANSFORMATION,
                    evidence_requirement=EvidenceSupportLevel.LOCAL_ONLY,
                    claim_precision_ceiling=EvidenceSupportLevel.LOCAL_ONLY,
                )
            ],
            rationale="Single-step local plan.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class OverDecomposedSourceSummaryPlanner:
    planner_name = "over_decomposed_source_summary_planner"
    prompt_version = "stub_v1"

    # Return the source/extraction/synthesis split that the runtime should normalize.
    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Down-select official sources for the short summary.",
                    rationale="The task needs official references.",
                    expected_outcome="A source shortlist.",
                ),
                PlanStep(
                    step_id="step_2",
                    objective="Extract factual points from the selected official sources.",
                    rationale="The summary should be grounded.",
                    expected_outcome="Extracted facts.",
                ),
                PlanStep(
                    step_id="step_3",
                    objective="Draft the brief summary.",
                    rationale="The final output should be concise.",
                    expected_outcome="A short summary with references.",
                ),
            ],
            rationale="The planner over-decomposed a small source-summary task.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class StubPolicyEngine(PolicyEnginePort):
    policy_engine_name = "stub_policy_engine"
    policy_bundle_version = "layer2_governance_v2"
    policy_config_version = "layer2_governance_thresholds_v1"

    # Model OPA's inexpensive first pass so graph tests never contact a live policy engine.
    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]:
        return {
            "require_evidence": True,
            "require_risk": True,
            "require_trajectory": True,
            "require_behavioral": True,
            "require_judge": False,
        }

    # Return a schema-valid OPA decision derived from the deterministic graph fixture.
    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]:
        return _build_stub_opa_decision(
            decision_input=decision_input,
            policy_engine=self,
        )

    # Apply deterministic governance decisions so the runtime stays testable without OPA.
    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        if (
            policy_input.external_impact
            or policy_input.capability_mode == CapabilityAccessMode.WRITE
        ):
            return PolicyEvaluationResult(
                policy_decision=PolicyDecision.DENY,
                reason_codes=(GovernanceReasonCode.WRITE_CAPABILITY_BLOCKED,),
                summary="Write-like capability is blocked by the policy stub.",
                policy_engine_name=self.policy_engine_name,
                policy_bundle_version=self.policy_bundle_version,
            )

        if policy_input.actor_result_type == ActorResultType.ASK_CLARIFICATION:
            return PolicyEvaluationResult(
                policy_decision=PolicyDecision.ASK_CLARIFICATION,
                reason_codes=(GovernanceReasonCode.ASK_CLARIFICATION_REQUIRED,),
                summary="Clarification is required by the policy stub.",
                policy_engine_name=self.policy_engine_name,
                policy_bundle_version=self.policy_bundle_version,
            )

        if policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
            return PolicyEvaluationResult(
                policy_decision=PolicyDecision.ALLOW,
                reason_codes=(GovernanceReasonCode.REPLAN_ALLOWED,),
                summary="Replanning is allowed by the policy stub.",
                policy_engine_name=self.policy_engine_name,
                policy_bundle_version=self.policy_bundle_version,
            )

        if policy_input.tool_name is not None and policy_input.read_only_mode:
            return PolicyEvaluationResult(
                policy_decision=PolicyDecision.ALLOW_READ_ONLY,
                reason_codes=(GovernanceReasonCode.READ_ONLY_ALLOWED,),
                summary="Read-only tool use is allowed by the policy stub.",
                policy_engine_name=self.policy_engine_name,
                policy_bundle_version=self.policy_bundle_version,
            )

        if policy_input.tool_name is not None:
            return PolicyEvaluationResult(
                policy_decision=PolicyDecision.ALLOW,
                reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                summary="The requested tool is allowed by the policy stub.",
                policy_engine_name=self.policy_engine_name,
                policy_bundle_version=self.policy_bundle_version,
            )

        return PolicyEvaluationResult(
            policy_decision=PolicyDecision.ALLOW,
            reason_codes=(GovernanceReasonCode.LOCAL_OUTPUT_ALLOWED,),
            summary="Local-only output is allowed by the policy stub.",
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )


class JudgeRequiredPolicyEngine(StubPolicyEngine):
    policy_engine_name = "judge_required_policy_engine"

    # Require the semantic judge only in tests that exercise its OPA input signal.
    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]:
        requirements = super().evaluate_required_pillars(base_facts=base_facts)
        return {**requirements, "require_judge": True}

    # Let the OPA fixture convert a confident judge flag into evidence recovery.
    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]:
        judge_facts = decision_input["pillars"]["judge"]["raw_facts"]
        if (
            judge_facts.get("verdict") == "flag"
            and float(judge_facts.get("confidence") or 0.0) >= 0.7
        ):
            return _build_stub_opa_decision(
                decision_input=decision_input,
                policy_engine=self,
                directive="request_more_evidence",
                reason_code="judge_reject_output",
                summary="The semantic judge requires a safer evidence-recovery pass.",
            )
        return super().evaluate_decision(decision_input=decision_input)


class ReviewPolicyEngine(PolicyEnginePort):
    policy_engine_name = "review_policy_engine"
    policy_bundle_version = "layer2_governance_v2"
    policy_config_version = "layer2_governance_thresholds_v1"

    # Keep required-pillar selection deterministic for HITL graph tests.
    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]:
        return {
            "require_evidence": True,
            "require_risk": True,
            "require_trajectory": True,
            "require_behavioral": True,
            "require_judge": False,
        }

    # Force review only for the fixture's proposed action while preserving valid OPA output.
    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]:
        actor = decision_input["base_facts"]["actor"]
        if actor["result_type"] == ActorResultType.PROPOSE_ACTION.value:
            return _build_stub_opa_decision(
                decision_input=decision_input,
                policy_engine=self,
                directive="wait_human_review",
                policy_decision="require_human_review",
                reason_code="human_review_required",
                summary="The proposed action requires human review before execution.",
            )
        return _build_stub_opa_decision(
            decision_input=decision_input,
            policy_engine=self,
        )

    # Force a human review pause when the actor proposes an external action.
    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
            return PolicyEvaluationResult(
                policy_decision=PolicyDecision.REQUIRE_HUMAN_REVIEW,
                reason_codes=(GovernanceReasonCode.HUMAN_REVIEW_REQUIRED,),
                summary="The proposed action requires human review before execution.",
                policy_engine_name=self.policy_engine_name,
                policy_bundle_version=self.policy_bundle_version,
            )

        return PolicyEvaluationResult(
            policy_decision=PolicyDecision.ALLOW,
            reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
            summary="The action is allowed by the review policy engine.",
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )


# Build one valid OPA decision envelope from the graph fixture's facts and signals.
def _build_stub_opa_decision(
    *,
    decision_input: dict[str, Any],
    policy_engine: PolicyEnginePort,
    directive: str | None = None,
    policy_decision: str | None = None,
    reason_code: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    base_facts = decision_input["base_facts"]
    actor = base_facts["actor"]
    policy = base_facts["policy"]
    pillars = decision_input["pillars"]
    resolved_directive = directive or "continue"
    resolved_policy_decision = policy_decision or "allow"
    resolved_reason_code = reason_code or "allowed_by_policy"
    resolved_summary = summary or "The fixture permits this runtime turn."

    if actor["capability_mode"] == CapabilityAccessMode.WRITE.value or actor["external_impact"]:
        resolved_directive = "stop"
        resolved_policy_decision = "deny"
        resolved_reason_code = "write_capability_blocked"
        resolved_summary = "Write-like capability is blocked by the policy stub."
    elif actor["result_type"] == ActorResultType.ASK_CLARIFICATION.value:
        resolved_directive = "ask_user_clarification"
        resolved_policy_decision = "ask_clarification"
        resolved_reason_code = "ask_clarification_required"
        resolved_summary = "Clarification is required by the policy stub."
    elif actor["result_type"] == ActorResultType.NEEDS_REPLAN.value:
        resolved_directive = "replan"
        resolved_reason_code = "replan_allowed"
        resolved_summary = "Replanning is allowed by the policy stub."
    elif pillars["trajectory"]["raw_facts"].get("provider_runtime_action_missing"):
        resolved_directive = "replan"
        resolved_reason_code = "repeated_recovery"
        resolved_summary = "Missing provider runtime action requires replanning."
    elif (
        actor["result_type"] in {
            ActorResultType.INTERMEDIATE_OUTPUT.value,
            ActorResultType.FINAL_OUTPUT.value,
        }
        and pillars["evidence"]["raw_facts"].get("support_rank_available", 0)
        < pillars["evidence"]["raw_facts"].get("support_rank_required", 0)
    ):
        resolved_directive = "request_more_evidence"
        resolved_reason_code = "evidence_support_below_step_need"
        resolved_summary = "The step requires stronger evidence support."
    elif base_facts["tool"]["name"] is not None and policy["read_only_mode"]:
        resolved_directive = "continue_read_only"
        resolved_policy_decision = "allow_read_only"
        resolved_reason_code = "read_only_allowed"
        resolved_summary = "Read-only tool use is allowed by the policy stub."

    return {
        "schema_version": "governance_decision_output.v1",
        "directive": resolved_directive,
        "policy_decision": resolved_policy_decision,
        "reason_codes": [resolved_reason_code],
        "pillar_results": pillars,
        "selected_reason": resolved_summary,
        "rejected_alternatives": {},
        "safety_invariants_checked": ["fixture_opa_decision"],
        "requires_human_review": resolved_directive == "wait_human_review",
        "policy_bundle_version": policy_engine.policy_bundle_version,
        "policy_config_version": policy_engine.policy_config_version,
    }


class StaticJudge(JudgePort):
    judge_name = "static_judge"
    prompt_version = "static_judge_v1"

    def __init__(self, assessment: JudgeAssessment) -> None:
        self._assessment = assessment

    # Return one deterministic judge assessment so governance tests stay network-free.
    def evaluate(
        self,
        *,
        task_state,
        policy_input,
        static_policy_assessment,
        dynamic_policy_assessment,
        behavioral_assessment,
        evaluated_target,
        invocation_flags,
    ) -> JudgeAssessment:
        return JudgeAssessment(
            invoked=self._assessment.invoked,
            recommendation=self._assessment.recommendation,
            confidence=self._assessment.confidence,
            summary=self._assessment.summary,
            rationale=self._assessment.rationale,
            flags=invocation_flags or self._assessment.flags,
            evaluated_target=evaluated_target,
        )


class ProgressThenCompleteActor(ActorPort):
    actor_name = "progress_then_complete_actor"
    prompt_version = "progress_then_complete_v1"

    # Emit one progress note before validating the step as complete.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.current_plan_step.step_id == "step_1" and not actor_task.step_validations:
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                summary="Progress was recorded but the step is not complete yet.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="Partial local synthesis.",
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                    summary="The actor needs one more local pass before completion.",
                ),
                step_validation=StepValidation(
                    plan_step_id=actor_task.current_plan_step.step_id,
                    objective_satisfied=False,
                    evidence_sufficient=False,
                    confidence=0.45,
                    next_action=StepValidationDecision.CONTINUE_ACTING,
                    rationale_summary="The current partial output is not enough to complete the step.",
                    residual_uncertainty="The step still needs a completion pass.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="The single-step task is now complete.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Completed after a second local actor pass.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The second pass satisfies the single-step plan.",
            ),
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.82,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The step objective is satisfied after the second pass.",
            ),
        )


class ClarificationActor(ActorPort):
    actor_name = "clarification_actor"
    prompt_version = "clarification_actor_v1"

    # Ask for clarification on the first turn so governance can stop the slice cleanly.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.ASK_CLARIFICATION,
            summary="The step needs clarification before it can continue.",
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason="The task is missing one required clarification.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary="The actor cannot safely continue without clarification.",
            ),
        )


class WriteActionActor(ActorPort):
    actor_name = "write_action_actor"
    prompt_version = "write_action_actor_v1"

    # Request a write-like external action so the policy engine can deny it.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Request a write-like external action.",
            capability_mode=CapabilityAccessMode.WRITE,
            external_impact=True,
            tool_request=ToolRequest(
                plan_step_id=actor_task.current_plan_step.step_id,
                tool_name=ToolName.WEB_SEARCH,
                reason="This request is intentionally unsafe for the deny-path test.",
                query="unsafe write action",
            ),
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.TOOL_DECISION,
                summary="The actor attempted a write-like external action.",
            ),
        )


class ReplanRequestActor(ActorPort):
    actor_name = "replan_request_actor"
    prompt_version = "replan_request_v1"

    # Request replanning once, then complete the replacement step produced by the replanner.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        if actor_task.current_plan_step.step_id == "step_1":
            return ActorResult(
                plan_step_id=actor_task.current_plan_step.step_id,
                result_type=ActorResultType.NEEDS_REPLAN,
                summary="The current step is not executable as planned.",
                capability_mode=CapabilityAccessMode.NONE,
                blocking_reason="The initial step is too broad and needs a narrower recovery step.",
                scratchpad_entry=ScratchpadEntry(
                    turn_index=actor_task.actor_turn_count,
                    plan_step_id=actor_task.current_plan_step.step_id,
                    entry_type=ScratchpadEntryType.UNCERTAINTY,
                    summary="The actor cannot execute the broad initial step reliably.",
                ),
            )

        return ActorResult(
            plan_step_id=actor_task.current_plan_step.step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="The replanned task is now complete.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Completed from the replanned recovery step.",
            scratchpad_entry=ScratchpadEntry(
                turn_index=actor_task.actor_turn_count,
                plan_step_id=actor_task.current_plan_step.step_id,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary="The replacement step is narrow enough to complete directly.",
            ),
            step_validation=StepValidation(
                plan_step_id=actor_task.current_plan_step.step_id,
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.8,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="The replanned step satisfies the task within the test scope.",
            ),
        )


class StubReplanner(ReplannerPort):
    replanner_name = "stub_replanner"
    prompt_version = "stub_replanner_v1"

    # Replace the blocked current/pending trajectory with a narrow executable step.
    def replan(self, *, task_state) -> ReplanDecision:
        return ReplanDecision(
            replacement_steps=[
                PlanStep(
                    step_id="step_1_replanned",
                    objective="Complete a narrower recovery step after the actor requested replanning.",
                    rationale="The actor reported that the previous step was too broad.",
                    expected_outcome="A completed recovery output.",
                )
            ],
            rationale="The stub replanner replaced the blocked trajectory with one recovery step.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


class CollidingReplanner(ReplannerPort):
    replanner_name = "colliding_replanner"
    prompt_version = "colliding_replanner_v1"

    # Return a colliding ID to verify the runtime normalizes replacement trajectories.
    def replan(self, *, task_state) -> ReplanDecision:
        return ReplanDecision(
            replacement_steps=[
                PlanStep(
                    step_id="replan_step_1",
                    objective="Replacement step with a generated ID that already exists.",
                    rationale="The OpenAI replanner starts IDs from one for each replan call.",
                    expected_outcome="A replacement step with a unique runtime ID.",
                )
            ],
            rationale="Returned an intentionally colliding replacement ID.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


class FakeParsedReplanResponse:
    # Minimal fake response object matching the OpenAI SDK parsed response shape.
    def __init__(self, output_parsed) -> None:
        self.output_parsed = output_parsed


class FakeReplannerResponses:
    # Capture parse inputs while returning a deterministic structured payload.
    def __init__(self, output_parsed) -> None:
        self.output_parsed = output_parsed
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeParsedReplanResponse(self.output_parsed)


class FakeReplannerClient:
    # Expose the responses.parse surface used by OpenAIReplanner.
    def __init__(self, output_parsed) -> None:
        self.responses = FakeReplannerResponses(output_parsed)


class _RetryableStatusError(RuntimeError):
    # Expose an HTTP-like status code without importing provider-specific error types.
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeParsedPlannerResponse:
    # Mirror the OpenAI parsed response shape used by the planner.
    def __init__(self, output_parsed) -> None:
        self.output_parsed = output_parsed


class _FlakyPlannerResponses:
    # Fail with retryable status codes before returning one valid planner payload.
    def __init__(self, output_parsed, failures: list[int]) -> None:
        self.output_parsed = output_parsed
        self.failures = list(failures)
        self.call_count = 0
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.call_count += 1
        if self.failures:
            status_code = self.failures.pop(0)
            raise _RetryableStatusError(status_code, f"transient failure {status_code}")
        self.last_kwargs = kwargs
        return _FakeParsedPlannerResponse(self.output_parsed)


class _FlakyPlannerClient:
    # Expose the responses.parse surface used by OpenAIPlanner.
    def __init__(self, output_parsed, failures: list[int]) -> None:
        self.responses = _FlakyPlannerResponses(output_parsed, failures)


class _FakeOpenRouterClient:
    # Return one cloud actor JSON action without touching the OpenRouter service.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "OpenRouter actor produced a final answer.",
                "output_text": "OpenRouter actor completed the step.",
                "completion_rationale": "The current step objective is satisfied locally.",
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.82,
            },
        }


class _FakeOpenRouterSearchWithoutQueryClient:
    # Return a web-search action missing query to verify runtime fallback behavior.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "request_web_search",
            "arguments": {
                "summary": "Need official evidence.",
                "reason": "The current research step requires authoritative sources.",
            },
        }


class _FakeOpenRouterCompletionWithoutRationaleClient:
    # Return a completion action missing audit fields to verify normalization remains robust.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "output_text": "Grounded concise final answer.",
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.79,
            },
        }


class _FakeOpenRouterUnsupportedPrecisionCompletionClient:
    # Return a completion with precise legal timing despite missing primary-source excerpt support.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "Finalize the DMA summary.",
                "output_text": (
                    "Regulation (EU) 2022/1925 entered into force in 2023 and applies "
                    "within the deadlines laid down in the regulation."
                ),
                "completion_rationale": "The current step appears complete from the available sources.",
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.86,
            },
        }


class _FakeOpenRouterIntakeSupportedExternalDraftClient:
    # Return a local external draft that only restates facts supplied by Layer 1 intake.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_step_completed",
            "arguments": {
                "summary": "Drafted the local incident email from intake facts.",
                "output_text": (
                    "Subject: Immediate Verification Needed - EU Notification Pipeline Incident "
                    "(Detected 09:12 CET, 25 May 2026)\n\n"
                    "Known Facts:\n"
                    "- Intermittent delivery failures were observed for transactional notifications.\n"
                    "- The incident remains under investigation.\n"
                    "- No confirmed data loss has been identified.\n\n"
                    "Please verify delivery queues, check the retry backlog, confirm whether "
                    "customer-facing impact extended beyond the notification pipeline, and send "
                    "status back to the incident lead within 60 minutes under the requested "
                    "60-minute response deadline."
                ),
                "completion_rationale": (
                    "The draft restates the task-provided incident facts and requested checks."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.88,
            },
        }


class _FakeOpenRouterSourceShortlistCompletionClient:
    # Return a pure source-shortlist completion that should stay within source-discovery support.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_step_completed",
            "arguments": {
                "summary": "Lock in the official DMA source shortlist.",
                "output_text": (
                    "Shortlist of official EU sources for the Digital Markets Act (DMA):\n"
                    "1. European Commission DMA portal: https://digital-markets-act.ec.europa.eu/index_en\n"
                    "2. EUR-Lex DMA summary page: https://eur-lex.europa.eu/EN/legal-content/summary/digital-markets-act.html"
                ),
                "completion_rationale": (
                    "The current step only requires identifying official source pages from the "
                    "observed source-discovery evidence."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.79,
            },
        }


class _FakeOpenRouterSummaryWithRegulationYearClient:
    # Return a short DMA summary that mentions the regulation number without claiming precise timing.
    # Keep this response on the safe side of the precision gate.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "Summarize the official DMA sources.",
                "output_text": (
                    "The DMA is an EU regulation for gatekeeper platforms. Official sources: "
                    "Regulation (EU) 2022/1925 and the European Commission DMA legislation page."
                ),
                "completion_rationale": (
                    "The current step is satisfied using the official sources already observed."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.83,
            },
        }


class _FakeOpenRouterSummaryWithGenericTimelineClient:
    # Return a short EU-law summary with a timeline claim for a different regulation.
    # This exercises the generalized timeline rule outside the DMA.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "Summarize the official AI Act timeline.",
                "output_text": (
                    "The AI Act is an EU regulation for AI governance. It entered into force on "
                    "1 August 2024. Official sources include an EU Commission announcement and the "
                    "official AI Act legislation page."
                ),
                "completion_rationale": (
                    "The current step is satisfied using the official timeline sources already observed."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.84,
            },
        }


class _FakeOpenRouterConservativeSummaryAfterExtractabilityLimitClient:
    # Return the compact recovery shape expected after extraction attempts are limited.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "Write a conservative high-level official-source summary.",
                "output_text": (
                    "**Brief Summary**\n"
                    "The Digital Markets Act is an EU regulation for large online platforms "
                    "that act as gatekeepers in digital markets. Its purpose is to support "
                    "fairer and more contestable digital markets at a high level.\n\n"
                    "**Official sources**\n"
                    "European Commission DMA portal: https://digital-markets-act.ec.europa.eu/index_en\n"
                    "EUR-Lex DMA summary: https://eur-lex.europa.eu/EN/legal-content/summary/digital-markets-act.html"
                ),
                "completion_rationale": (
                    "The replacement step asks for a conservative purpose/scope-level summary "
                    "and does not require extracted clause text or obligations-by-number claims."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.82,
            },
        }


class _FakeOpenRouterRecoveredHighLevelStepOneCompletionClient:
    # Return completion-shaped JSON without the runtime action envelope to cover provider recovery.
    def complete_tool_call(self, **kwargs):
        return {
            "summary": "Identified official EU sources and gathered high-level evidence.",
            "output_text": (
                "Official sources identified:\n"
                "- European Commission Digital Markets Act portal: https://digital-markets-act.ec.europa.eu/index_en\n"
                "- Legislation overview page: https://digital-markets-act.ec.europa.eu/legislation_en\n"
                "High-level evidence from the portal shows that the DMA is EU legislation aimed "
                "at making digital markets fairer and more contestable. It defines gatekeepers "
                "as large platforms offering core services such as online search, app stores, "
                "or messaging, and requires them to comply with a set of obligations and "
                "prohibitions. The European Commission, through DG COMP and DG CONNECT, is "
                "the sole enforcer of the DMA."
            ),
            "completion_rationale": (
                "The fetched official page supplies high-level evidence for the source and "
                "summary step."
            ),
            "objective_satisfied": True,
            "evidence_sufficient": True,
            "confidence": 0.91,
        }


class _FakeOpenRouterProceduralImplementationSummaryClient:
    # Return a too-strong implementation-summary draft so the actor can normalize procedural wording.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "Summarize the DMA procedural implementation page.",
                "output_text": (
                    "The Digital Markets Act is an EU regulation that aims to make digital markets "
                    "fairer by setting rules for large online platforms that act as gatekeepers. "
                    "It defines gatekeeper criteria, outlines their obligations and prohibitions, "
                    "and establishes enforcement by the European Commission (DG COMP and DG CONNECT). "
                    "Official information and the full legislative text are available on the European "
                    "Commission’s DMA website and on EUR-Lex."
                ),
                "completion_rationale": (
                    "The current step uses the official DMA legislation page and Commission portal."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.88,
            },
        }


class _FakeOpenRouterCompletionWithInternalRefsClient:
    # Return a completion that leaks internal trace refs so sanitization can strip them.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_final_output",
            "arguments": {
                "summary": "Step 2 completed with trace refs.",
                "output_text": (
                    "The DMA is EU legislation aimed at fair and contestable digital markets "
                    "【step_1:tool:1:source:1】."
                ),
                "completion_rationale": (
                    "The summary should not expose internal runtime markers 【step_1:tool:2:source:1】."
                ),
                "objective_satisfied": True,
                "evidence_sufficient": True,
                "confidence": 0.86,
            },
        }


class _FakeOpenRouterFalseNoToolBlockedClient:
    # Simulate a weaker actor that mistakes no allowlist for unavailable tools.
    def complete_tool_call(self, **kwargs):
        return {
            "tool_name": "emit_blocked",
            "arguments": {
                "summary": "Cannot identify official sources without external lookup",
                "blocking_reason": (
                    "No external tool usage is allowed and no prior evidence of official sources "
                    "is available to satisfy the step's objective."
                ),
            },
        }


class _FakeOpenRouterJsonWithoutEnvelopeClient:
    # Return JSON without the actor envelope to verify fallback normalization.
    def complete_tool_call(self, **kwargs):
        return {"answer": "Provider returned an answer without a tool_name envelope."}


class _FakeOpenRouterEmptyResponseClient:
    # Simulate a provider response that contains neither tool calls nor content.
    def complete_tool_call(self, **kwargs):
        raise bedrock_client_module.BedrockRuntimeError("Bedrock returned no tool use.")


class _FakeOpenRouterHTTPResponse:
    # Mirror the minimal response interface consumed by urlopen context managers.
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class FakeOPAResponse:
    # Minimal fake HTTP response object matching the urllib context manager shape.
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeOPAResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _ThreadIdInjectingGraph:
    # Keep the existing tests stable while the runtime input contract adds thread_id.
    def __init__(self, graph) -> None:
        self._graph = graph

    def invoke(self, input, config=None, *args, **kwargs):
        if isinstance(input, dict) and "thread_id" not in input:
            thread_id = None
            if isinstance(config, dict):
                configurable = config.get("configurable", {})
                metadata = config.get("metadata", {})
                if isinstance(configurable, dict):
                    thread_id = configurable.get("thread_id")
                if thread_id is None and isinstance(metadata, dict):
                    thread_id = metadata.get("thread_id")
            input = dict(input)
            input["thread_id"] = thread_id or "test-thread-id"
        return self._graph.invoke(input, config=config, *args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._graph, item)




def _build_research_intake_result(
    *,
    max_steps: int | None = None,
    max_tool_calls: int | None = None,
    read_only_mode: bool = False,
) -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Find official sources about the Digital Markets Act and summarize them.",
                context="Use official EU sources.",
                constraints=["Use official EU sources."],
                domain_type=DomainType.RESEARCH,
                domain_label="dma_research",
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Produce a grounded short summary."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=max_steps, max_tool_calls=max_tool_calls),
                read_only_mode=read_only_mode,
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


def _build_external_communication_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Prepare a cautious external communication draft.",
                context="Do not send anything automatically.",
                constraints=["Keep the draft local until review."],
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                domain_label="external_communication_test",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Pause before any outward-facing execution."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.75,
                budget=BudgetConfig(max_steps=None, max_tool_calls=None),
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


# Build the incident-style external communication intake that exercises local fact support.
def _build_external_incident_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal=(
                    "Draft an external email to Acme Cloud operations requesting manual "
                    "verification and status update for a sensitive EU customer notification "
                    "pipeline incident."
                ),
                context=(
                    "A sensitive runtime incident affecting the EU customer notification "
                    "pipeline was detected at 09:12 CET on May 25, 2026; it caused "
                    "intermittent delivery failures for transactional notifications and "
                    "remains under investigation. No confirmed data loss, but delayed "
                    "processing was observed for some EU tenants."
                ),
                constraints=[
                    "Target recipient: operations team at Acme Cloud",
                    "Ask operations to manually verify delivery queues",
                    "Ask operations to check retry backlog",
                    "Ask operations to confirm whether customer-facing impact extended beyond the notification pipeline",
                    "Request a status report back to the incident lead within 60 minutes",
                ],
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                domain_label="incident_operations_status_request",
                sensitivity=SensitivityLevel.CRITICAL,
                success_criteria=[
                    "Includes all incident details provided and clearly states current investigation status",
                    "Lists the specific verification/check requests",
                    "Uses appropriate, cautious language given no confirmed data loss and ongoing investigation",
                ],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.9,
                budget=BudgetConfig(max_steps=None, max_tool_calls=None),
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
        ),
    )


# Build one actor-ready task state for the incident local-draft precision regression.
def _build_external_incident_draft_task_state() -> TaskState:
    state = TaskState.from_intake(_build_external_incident_intake_result())
    state.current_state = RuntimeState.ACTING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Draft the Known Facts and Action Requested sections from provided context.",
            rationale="The draft must stay within the local incident facts supplied by intake.",
            expected_outcome="A cautious incident email draft using only task-provided facts.",
            step_kind=PlanStepKind.DETAILED_EXTRACTION,
            evidence_requirement=EvidenceSupportLevel.LOCAL_ONLY,
            claim_precision_ceiling=EvidenceSupportLevel.LOCAL_ONLY,
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 1
    return state


def _build_policy_input_task_state(*, read_only_mode: bool = False) -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result(read_only_mode=read_only_mode))
    state.observed_evidence = []
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect the minimum official evidence needed for the task.",
            rationale="The first step should ground the task before synthesis.",
            expected_outcome="A compact evidence base relevant to the goal.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 1
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.PROPOSE_ACTION,
        summary="Request a read-only evidence search for the first step.",
        capability_mode=CapabilityAccessMode.READ_ONLY,
        external_impact=False,
        tool_request=ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="The first semantic step requires authoritative documentary evidence.",
            query="Digital Markets Act official EU sources",
            max_results=3,
            extractive_goal="Find official EU sources about the Digital Markets Act.",
        ),
        scratchpad_entry=ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_1",
            entry_type=ScratchpadEntryType.TOOL_DECISION,
            summary="The current step needs external documentary evidence.",
        ),
    )
    state.current_tool_request = state.actor_result.tool_request
    return state


def _build_high_precision_research_task_state() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.observed_evidence = []
    state.plan_steps = [
        PlanStep(
            step_id="step_2",
            objective=(
                "Extract the key substance needed for a concise overview, including "
                "timeline or applicability details."
            ),
            rationale="The final summary should stay aligned with official sources.",
            expected_outcome="A concise overview with supported timeline details.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 2
    state.observed_evidence.append(
        ObservedEvidence(
            evidence_id="step_2:tool:2:source:1",
            plan_step_id="step_2",
            title="Digital Markets Act",
            locator="https://digital-markets-act.ec.europa.eu/index_en",
            excerpt=(
                "The Digital Markets Act is the EU’s law to make the markets in the digital "
                "sector fairer and more contestable."
            ),
            source_tool=ToolName.FETCH_URL_TEXT,
        )
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_2",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed tool result from 'fetch_url_text': URL fetch succeeded but extracted readable text was empty.",
        )
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=2,
            plan_step_id="step_2",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed tool result from 'fetch_url_text': URL text fetched successfully.",
        )
    )
    return state


def _build_source_shortlist_task_state() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.observed_evidence = []
    state.plan_steps = [
        PlanStep(
            step_id="replan_step_1",
            objective="Lock in a shortlist of official EU sources for the DMA.",
            rationale=(
                "Use only the observed evidence that these official pages exist and avoid "
                "substantive factual claims."
            ),
            expected_outcome="A final shortlist of at least two official source URLs.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 2
    state.observed_evidence.append(
        ObservedEvidence(
            evidence_id="replan_step_1:tool:1:fallback",
            plan_step_id="replan_step_1",
            title="web_search",
            locator="Digital Markets Act official site:europa.eu OR site:eur-lex.europa.eu",
            excerpt=(
                "- European Commission DMA portal: https://digital-markets-act.ec.europa.eu/index_en\n"
                "- EUR-Lex DMA summary page: https://eur-lex.europa.eu/EN/legal-content/summary/digital-markets-act.html"
            ),
            source_tool=ToolName.WEB_SEARCH,
            support_level=EvidenceSupportLevel.SOURCE_DISCOVERY,
        )
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="replan_step_1",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed tool result from 'web_search': Web search executed successfully.",
        )
    )
    return state


def _build_summary_task_state_with_year_in_citation() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.observed_evidence = []
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Produce a short summary from official DMA sources.",
            rationale="The task only needs a brief grounded summary from official pages.",
            expected_outcome="A short summary grounded in official sources.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 2
    state.observed_evidence.append(
        ObservedEvidence(
            evidence_id="step_1:tool:1:source:1",
            plan_step_id="step_1",
            title="Digital Markets Act (DMA) Legislation",
            locator="https://digital-markets-act.ec.europa.eu/legislation_en",
            excerpt=(
                "The main legislative texts for the DMA are Regulation (EU) 2022/1925 of the "
                "European Parliament and of the Council of 14 September 2022 on contestable and "
                "fair markets in the digital sector and the Procedural Implementing Regulation."
            ),
            source_tool=ToolName.FETCH_URL_TEXT,
            support_level=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
        )
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_1",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed tool result from 'fetch_url_text': URL text fetched successfully.",
        )
    )
    return state


# Build a non-DMA timeline state so the regression covers a different official source task.
def _build_summary_task_state_with_generic_timeline() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.observed_evidence = []
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Produce a short summary from official EU sources including the timeline.",
            rationale="The task only needs a brief grounded summary from official pages and one date fact.",
            expected_outcome="A short summary grounded in official sources and the regulation timeline.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.current_state = RuntimeState.ACTING
    state.actor_turn_count = 2
    state.tool_call_count = 1
    state.last_tool_result = ToolExecutionResult(
        request=ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.WEB_SEARCH,
            reason="Find an official EU source for the AI Act entry-into-force date.",
            query="AI Act entered into force official EU",
        ),
        success=True,
        summary="Web search executed successfully.",
        content="The AI Act entered into force on 1 August 2024.",
        sources=(
            ToolSource(
                title="AI Act official EU announcement",
                locator="https://example.eu/ai-act-entry-into-force",
                snippet="The AI Act entered into force on 1 August 2024.",
            ),
        ),
    )
    return state


def _build_conservative_replan_summary_task_state() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.observed_evidence = []
    state.plan_steps = [
        PlanStep(
            step_id="replan_step_1",
            objective=(
                "Write the final deliverable using only a conservative, purpose/scope-level "
                "summary that does not require extracted clause text or obligations-by-number claims."
            ),
            rationale=(
                "Extractability is limited, so the remaining task should use high-level official "
                "evidence and avoid granular legal claims."
            ),
            expected_outcome=(
                "A short summary framed at a high level plus a references list with official links."
            ),
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.current_state = RuntimeState.ACTING
    state.actor_turn_count = 3
    state.runtime_flags.append(RuntimeFlag.EXTRACTABILITY_LIMITED)
    state.observed_evidence.append(
        ObservedEvidence(
            evidence_id="replan_step_1:tool:5:source:1",
            plan_step_id="replan_step_1",
            title="Digital Markets Act",
            locator="https://digital-markets-act.ec.europa.eu/index_en",
            excerpt=(
                "The Digital Markets Act is the EU law to make the markets in the digital sector "
                "fairer and more contestable and concerns gatekeepers."
            ),
            source_tool=ToolName.FETCH_URL_TEXT,
            support_level=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
        )
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=2,
            plan_step_id="replan_step_1",
            entry_type=ScratchpadEntryType.OBSERVATION,
            summary="Observed tool result from 'fetch_url_text': URL text fetched successfully.",
        )
    )
    return state


def _build_clarification_intake_result() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Review this contract and tell me whether I should sign it.",
                context="Contract review request.",
                constraints=[],
                domain_type=DomainType.ANALYSIS,
                domain_label="contract_review",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=[],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.8,
                budget=BudgetConfig(max_steps=4, max_tool_calls=6),
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.NEEDS_CLARIFICATION,
            parser_name="stub_parser",
            prompt_version="test_v1",
            schema_version="1.0.0",
            missing_information=["constraints", "success_criteria"],
        ),
    )


def _build_replanning_task_state() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.current_state = RuntimeState.REPLANNING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect broad evidence for the task.",
            rationale="The initial plan assumed a broad evidence step was enough.",
            expected_outcome="A broad evidence base.",
            status=PlanStepStatus.BLOCKED,
        ),
        PlanStep(
            step_id="step_2",
            objective="Synthesize the result.",
            rationale="Synthesis should follow evidence collection.",
            expected_outcome="A final answer.",
            status=PlanStepStatus.PENDING,
        ),
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 1
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.NEEDS_REPLAN,
        summary="The current step is not executable as planned.",
        capability_mode=CapabilityAccessMode.NONE,
        blocking_reason="The initial step is too broad and needs a narrower recovery step.",
    )
    state.scratchpad.append(
        ScratchpadEntry(
            turn_index=1,
            plan_step_id="step_1",
            entry_type=ScratchpadEntryType.UNCERTAINTY,
            summary="The actor cannot execute the broad initial step reliably.",
        )
    )
    return state


# Build one actor-ready state that is convenient for judge and combiner tests.
def _build_actor_ready_state_for_judge() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.current_state = RuntimeState.ACTING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect concise official evidence.",
            rationale="The task should remain grounded.",
            expected_outcome="A grounded local answer.",
            status=PlanStepStatus.IN_PROGRESS,
        )
    ]
    state.current_plan_step_index = 0
    state.actor_turn_count = 1
    state.actor_result = ActorResult(
        plan_step_id="step_1",
        result_type=ActorResultType.INTERMEDIATE_OUTPUT,
        summary="Local output produced.",
        capability_mode=CapabilityAccessMode.NONE,
        output_text="A local grounded update.",
        step_validation=StepValidation(
            plan_step_id="step_1",
            objective_satisfied=True,
            evidence_sufficient=True,
            confidence=0.72,
            next_action=StepValidationDecision.COMPLETE_STEP,
            rationale_summary="The local output looks adequate.",
        ),
    )
    state.observed_evidence = [
        ObservedEvidence(
            evidence_id="evidence-1",
            plan_step_id="step_1",
            title="Source shortlist",
            locator="https://example.org/source",
            excerpt="Source metadata only.",
            source_tool=ToolName.WEB_SEARCH,
            support_level=EvidenceSupportLevel.SOURCE_DISCOVERY,
        )
    ]
    return state


# Build one complete current profile that behavioural scoring can compare against history.
def _build_behavioral_candidate_state() -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result())
    state.current_state = RuntimeState.ACTING
    state.plan_steps = [
        PlanStep(
            step_id="step_1",
            objective="Collect official evidence.",
            rationale="Ground the task first.",
            expected_outcome="Evidence gathered.",
            status=PlanStepStatus.COMPLETED,
        ),
        PlanStep(
            step_id="step_2",
            objective="Synthesize the grounded answer.",
            rationale="Summarize only after evidence collection.",
            expected_outcome="A concise answer.",
            status=PlanStepStatus.COMPLETED,
        ),
        PlanStep(
            step_id="step_3",
            objective="Finalize the local answer.",
            rationale="The runtime can complete locally.",
            expected_outcome="A final answer.",
            status=PlanStepStatus.IN_PROGRESS,
        ),
    ]
    state.current_plan_step_index = 2
    state.actor_turn_count = 3
    state.tool_call_count = 1
    state.step_history.extend(
        [
            StepRecord(1, RuntimeState.ACTING, "planner", "Planned."),
            StepRecord(2, RuntimeState.ACTING, "actor", "Actor turn."),
        ]
    )
    state.step_validations = [
        StepValidation(
            plan_step_id="step_1",
            objective_satisfied=True,
            evidence_sufficient=True,
            confidence=0.82,
            next_action=StepValidationDecision.COMPLETE_STEP,
            rationale_summary="The evidence step is done.",
        )
    ]
    state.actor_result = ActorResult(
        plan_step_id="step_3",
        result_type=ActorResultType.INTERMEDIATE_OUTPUT,
        summary="Local synthesis in progress.",
        capability_mode=CapabilityAccessMode.NONE,
        output_text="A concise grounded answer.",
        step_validation=StepValidation(
            plan_step_id="step_3",
            objective_satisfied=True,
            evidence_sufficient=True,
            confidence=0.78,
            next_action=StepValidationDecision.COMPLETE_STEP,
            rationale_summary="The synthesis looks complete.",
        ),
    )
    request = ToolRequest(
        plan_step_id="step_1",
        tool_name=ToolName.WEB_SEARCH,
        reason="Collect official evidence.",
        query="official evidence",
    )
    state.tool_results_history = [
        ToolExecutionResult(
            request=request,
            success=True,
            summary="Collected official evidence.",
            content="Grounded result.",
            sources=(
                ToolSource(
                    title="Official source",
                    locator="https://example.org/official",
                    snippet="Grounded result.",
                ),
            ),
        )
    ]
    state.observed_evidence = [
        ObservedEvidence(
            evidence_id="behavioral-evidence-1",
            plan_step_id="step_1",
            title="Official source",
            locator="https://example.org/official",
            excerpt="Grounded result.",
            source_tool=ToolName.WEB_SEARCH,
            support_level=EvidenceSupportLevel.DETAILED_EXTRACTED,
        )
    ]
    return state


# Build a strong dynamic-policy assessment that should not force behavioural escalation by itself.
def _strong_dynamic_policy_assessment() -> DynamicPolicyAssessment:
    return DynamicPolicyAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.90,
            level=OversightSignalLevel.STRONG,
            reason_codes=(OversightReasonCode.EVIDENCE_MARKED_SUFFICIENT,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.12,
            level=OversightSignalLevel.LOW,
            reason_codes=(OversightReasonCode.TOOL_RISK_LOW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=0.93,
            level=OversightSignalLevel.HEALTHY,
            reason_codes=(OversightReasonCode.ACTOR_TURNS_WITHIN_LIMIT,),
        ),
        recommended_directive=DynamicPolicyRecommendedDirective.CONTINUE,
        summary="Dynamic policy is healthy.",
        reason_codes=(OversightReasonCode.RECOMMEND_CONTINUE,),
    )


# Build a weak dynamic-policy assessment so novelty can escalate to more evidence.
def _weak_dynamic_policy_assessment() -> DynamicPolicyAssessment:
    return DynamicPolicyAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.30,
            level=OversightSignalLevel.WEAK,
            reason_codes=(OversightReasonCode.OBSERVED_EVIDENCE_MISSING,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.18,
            level=OversightSignalLevel.LOW,
            reason_codes=(OversightReasonCode.TOOL_RISK_LOW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=0.78,
            level=OversightSignalLevel.HEALTHY,
            reason_codes=(OversightReasonCode.ACTOR_TURNS_WITHIN_LIMIT,),
        ),
        recommended_directive=DynamicPolicyRecommendedDirective.CONTINUE,
        summary="Dynamic policy sees weak evidence quality.",
        reason_codes=(OversightReasonCode.RECOMMEND_CONTINUE,),
    )


# Build a dynamic-policy assessment that is already operationally or trajectory-wise unhealthy.
def _review_dynamic_policy_assessment() -> DynamicPolicyAssessment:
    return DynamicPolicyAssessment(
        evidence_quality=EvidenceQualityAssessment(
            score=0.52,
            level=OversightSignalLevel.MODERATE,
            reason_codes=(OversightReasonCode.EVIDENCE_SUPPORT_BELOW_STEP_NEED,),
        ),
        operational_risk=OperationalRiskAssessment(
            score=0.64,
            level=OversightSignalLevel.HIGH,
            reason_codes=(OversightReasonCode.TOOL_REQUIRES_HUMAN_REVIEW,),
        ),
        trajectory_health=TrajectoryHealthAssessment(
            score=0.55,
            level=OversightSignalLevel.DEGRADED,
            reason_codes=(OversightReasonCode.RECOMMEND_HUMAN_REVIEW,),
        ),
        recommended_directive=DynamicPolicyRecommendedDirective.WAIT_HUMAN_REVIEW,
        summary="Dynamic policy is already unhealthy.",
        reason_codes=(OversightReasonCode.RECOMMEND_HUMAN_REVIEW,),
    )


# Build one deterministic judge escalation toward more evidence.
def _judge_more_evidence_assessment() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=True,
        recommendation=JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE,
        confidence=0.74,
        summary="Judge detected a fragile completion attempt.",
        rationale="The output overreaches the available evidence.",
        flags=("fragile_completion",),
        evaluated_target="fragile_completion",
    )


# Build one deterministic judge escalation toward human review.
def _judge_wait_review_assessment() -> JudgeAssessment:
    return JudgeAssessment(
        invoked=True,
        recommendation=JudgeRecommendedDirective.WAIT_HUMAN_REVIEW,
        confidence=0.88,
        summary="Judge recommends human review.",
        rationale="The semantic risk remains high.",
        flags=("semantic_risk_high",),
        evaluated_target="dynamic_escalation",
    )


# Build one external-communication intake result so judge tests can hit the draft branch.
def _build_external_communication_intake_result_for_governance() -> IntakeResult:
    return IntakeResult(
        structured_task=StructuredTask(
            task_spec=TaskSpec(
                goal="Prepare a cautious external communication draft.",
                context="Keep everything local and reviewable.",
                constraints=["Do not send anything automatically."],
                domain_type=DomainType.EXTERNAL_COMMUNICATION,
                domain_label="external_comm_governance_test",
                sensitivity=SensitivityLevel.HIGH,
                success_criteria=["Pause before any external execution."],
            ),
            execution_policy=ExecutionPolicy(
                confidence_threshold=0.65,
                budget=BudgetConfig(max_steps=4, max_tool_calls=3),
                policy_profile_name="external_comm_profile",
            ),
        ),
        metadata=IntakeMetadata(
            parse_status=ParseStatus.COMPLETE,
            parser_name="external_comm_test_intake",
            prompt_version="external_comm_test_v1",
            schema_version="1.0.0",
            warnings=[],
            missing_information=[],
        ),
    )


# Seed one comparable historical corpus directly into the behavioural SQLite store.
def _seed_behavioral_history(
    *,
    behavioral_store: SQLiteBehavioralStore,
    count: int,
    prefix: str,
    profile_overrides: dict[str, Any],
) -> None:
    import sqlite3

    behavioral_store.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(behavioral_store.settings.database_path)) as connection:
        behavioral_store._ensure_schema(connection)
        for index in range(count):
            thread_id = f"{prefix}-{index}"
            connection.execute(
                """
                INSERT OR REPLACE INTO runs (
                    thread_id, final_state, domain_type, sensitivity, policy_profile_name,
                    budget_max_steps, budget_max_tool_calls, actor_turn_count, tool_call_count,
                    replan_count, review_count, abort_count, has_external_communication,
                    max_evidence_support_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    "completed",
                    profile_overrides.get("domain_type", DomainType.RESEARCH.value),
                    profile_overrides.get("sensitivity", SensitivityLevel.LOW.value),
                    profile_overrides.get("policy_profile_name", "default"),
                    5,
                    10,
                    int(profile_overrides.get("actor_turn_count", 3)),
                    int(profile_overrides.get("tool_call_count", 1)),
                    int(profile_overrides.get("replan_count", 0)),
                    int(profile_overrides.get("review_count", 0)),
                    int(profile_overrides.get("abort_count", 0)),
                    int(profile_overrides.get("has_external_communication", 0)),
                    profile_overrides.get(
                        "max_evidence_support_level",
                        EvidenceSupportLevel.DETAILED_EXTRACTED.value,
                    ),
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO tool_calls (
                    thread_id, call_index, plan_step_id, tool_name, success, support_level
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    1,
                    "step_1",
                    profile_overrides.get("tool_name", ToolName.WEB_SEARCH.value),
                    1,
                    profile_overrides.get(
                        "max_evidence_support_level",
                        EvidenceSupportLevel.DETAILED_EXTRACTED.value,
                    ),
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO governance_decisions (
                    thread_id, decision_index, static_policy_decision,
                    dynamic_recommended_directive, final_directive, reason_codes_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    1,
                    PolicyDecision.ALLOW.value,
                    profile_overrides.get(
                        "dynamic_recommended_directive",
                        DynamicPolicyRecommendedDirective.CONTINUE.value,
                    ),
                    GovernanceDirective.WAIT_HUMAN_REVIEW.value
                    if int(profile_overrides.get("review_count", 0)) > 0
                    else GovernanceDirective.CONTINUE.value,
                    json.dumps([GovernanceReasonCode.ALLOWED_BY_POLICY.value]),
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO steps (
                    thread_id, step_id, status, actor_result_type, validation_next_action, turn_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    "step_1",
                    PlanStepStatus.COMPLETED.value,
                    ActorResultType.INTERMEDIATE_OUTPUT.value,
                    StepValidationDecision.COMPLETE_STEP.value,
                    int(profile_overrides.get("actor_turn_count", 3)),
                ),
            )
            feature_values = {
                "feature_snapshot_version": "behavioral_features_v2",
                "step_count": str(profile_overrides.get("step_count", 3)),
                "actor_turn_count": str(profile_overrides.get("actor_turn_count", 3)),
                "tool_call_count": str(profile_overrides.get("tool_call_count", 1)),
                "replan_count": str(profile_overrides.get("replan_count", 0)),
                "review_count": str(profile_overrides.get("review_count", 0)),
                "abort_count": str(profile_overrides.get("abort_count", 0)),
                "has_external_communication": str(profile_overrides.get("has_external_communication", 0)),
                "max_evidence_support_level": profile_overrides.get(
                    "max_evidence_support_level",
                    EvidenceSupportLevel.DETAILED_EXTRACTED.value,
                ),
                "governance_distribution": json.dumps(
                    {
                        (
                            GovernanceDirective.WAIT_HUMAN_REVIEW.value
                            if int(profile_overrides.get("review_count", 0)) > 0
                            else GovernanceDirective.CONTINUE.value
                        ): 1
                    },
                    sort_keys=True,
                ),
                "validation_distribution": json.dumps(
                    {StepValidationDecision.COMPLETE_STEP.value: 1},
                    sort_keys=True,
                ),
                "historical_similarity_score": "",
                "behavior_deviation_score": "",
                "novelty_score": "",
                "matched_run_count": "0",
                "behavioral_recommended_directive": BehavioralRecommendedDirective.CONTINUE.value,
            }
            for feature_name, feature_value in feature_values.items():
                connection.execute(
                    """
                    INSERT OR REPLACE INTO behavioral_features (
                        thread_id, feature_name, feature_value
                    ) VALUES (?, ?, ?)
                    """,
                    (thread_id, feature_name, feature_value),
                )
        connection.commit()


# Build a deterministic Layer 2 graph with the stubbed governance stack.
def _build_test_graph(
    *,
    planner_service: PlannerPort | None = None,
    actor_service: ActorPort | None = None,
    tool_gateway_service: ToolGatewayPort | None = None,
    replanner_service: ReplannerPort | None = None,
    policy_engine_service: PolicyEnginePort | None = None,
    judge_service: JudgePort | None = None,
    checkpointer=None,
):
    return _ThreadIdInjectingGraph(
        build_runtime_graph(
            # Unit graph fixtures must not share durable checkpoints across test runs.
            checkpointer=checkpointer or InMemorySaver(serde=build_runtime_serializer()),
            planner_service=planner_service or StubPlanner(),
            actor_service=actor_service or StubActor(),
            tool_gateway_service=tool_gateway_service or StubToolGateway(),
            replanner_service=replanner_service or StubReplanner(),
            policy_engine_service=policy_engine_service or StubPolicyEngine(),
            judge_service=judge_service,
        )
    )


if __name__ == "__main__":
    unittest.main()

__all__ = [name for name in globals() if not name.startswith("__")]
