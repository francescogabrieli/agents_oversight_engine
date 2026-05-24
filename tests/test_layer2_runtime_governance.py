import json
import os
import shutil
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    OpenRouterActor,
    OpenRouterActorSettings,
    OperationalRiskAssessment,
    OversightReasonCode,
    OversightSignalLevel,
    TrajectoryHealthAssessment,
    build_default_checkpointer,
    build_actor_task,
    build_default_actor,
    HumanReviewDecision,
    HumanReviewDecisionType,
    build_human_review_request,
    build_governance_decision,
    build_policy_evaluation_input,
    build_runtime_graph,
    replanner,
)
from layer2_runtime_governance.persistence import RuntimeArtifactStore
from layer2_runtime_governance.hitl import _apply_human_review_decision, human_review
from main_layer2 import build_compact_trace, write_layer2_trace_file
from main_layer2 import run_layer2_flow
from layer2_runtime_governance.replanning import (
    OpenAIReplanner,
    OpenAIReplannerSettings,
    ReplanDecision,
    ReplannerPort,
    build_replanner_system_prompt,
    build_replanner_user_prompt,
)
from layer2_runtime_governance.planning import OpenAIPlannerSettings, build_planner_system_prompt
from layer2_runtime_governance.acting import build_actor_system_prompt, build_actor_user_prompt
from layer2_runtime_governance.governance import (
    OPAHttpPolicyEngine,
    OPAHttpPolicyEngineSettings,
    build_opa_input_document,
)
from layer2_runtime_governance.nodes import governance, observe_tool_result
from layer2_runtime_governance.tool_contract import ObservedEvidence
import layer2_runtime_governance.openrouter_client as openrouter_client_module


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
                ),
                PlanStep(
                    step_id="step_2",
                    objective="Synthesize the grounded result.",
                    rationale="The runtime should synthesize only after evidence collection.",
                    expected_outcome="A concise grounded output.",
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
        if actor_task.current_plan_step.step_id == "step_1" and not actor_task.observed_evidence:
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
                    snippet="The DMA sets rules for gatekeeper platforms.",
                ),
            ),
        )


class RepeatFetchActor(ActorPort):
    actor_name = "repeat_fetch_actor"
    prompt_version = "repeat_fetch_v1"

    # Always repeat the same URL so the gateway duplicate guard can be tested.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
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
    policy_bundle_version = "stub_policy_bundle_v1"

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


class ReviewPolicyEngine(PolicyEnginePort):
    policy_engine_name = "review_policy_engine"
    policy_bundle_version = "review_policy_bundle_v1"

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
        raise openrouter_client_module.OpenRouterRuntimeError(
            "OpenRouter returned no tool call and no assistant content. finish_reason='stop'."
        )


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


class Layer2RuntimeGraphTests(unittest.TestCase):
    # These tests verify the bootstrap-plus-planner-plus-actor/tool behavior.
    def test_complete_intake_reaches_completed_state(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(len(task_state.step_history), 11)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "tool_gateway",
                "observe_tool_result",
                "actor",
                "governance",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.runtime_flags, [])
        self.assertEqual(len(task_state.plan_steps), 2)
        self.assertIsNone(task_state.current_plan_step_index)
        self.assertIsNone(task_state.current_plan_step)
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(task_state.plan_steps[1].status, PlanStepStatus.COMPLETED)
        self.assertEqual(task_state.planner_name, "stub_planner")
        self.assertEqual(task_state.planner_prompt_version, "stub_v1")
        self.assertEqual(task_state.actor_name, "stub_actor")
        self.assertEqual(task_state.actor_prompt_version, "stub_actor_v1")
        self.assertIsNotNone(task_state.actor_result)
        self.assertEqual(task_state.actor_result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertEqual(task_state.actor_result.capability_mode, CapabilityAccessMode.NONE)
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(task_state.actor_turn_count, 3)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observed_evidence), 1)
        self.assertEqual(len(task_state.scratchpad), 4)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertEqual(len(task_state.governance_history), 3)
        self.assertIsNotNone(task_state.last_governance_decision)
        self.assertEqual(
            task_state.last_governance_decision.directive,
            GovernanceDirective.CONTINUE,
        )
        self.assertEqual(
            task_state.last_governance_decision.policy_decision,
            PolicyDecision.ALLOW,
        )
        self.assertTrue(all(item.confidence > 0 for item in task_state.step_validations))
        self.assertEqual(
            task_state.final_output_text,
            "The DMA is an EU regulation that targets gatekeeper platforms.",
        )

    def test_needs_clarification_skips_actor(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-ready-to-execute-clarification"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertIn(RuntimeFlag.CLARIFICATION_REQUIRED, task_state.runtime_flags)
        self.assertIn(RuntimeFlag.HIGH_SENSITIVITY, task_state.runtime_flags)
        self.assertEqual(task_state.step_history[0].runtime_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(task_state.plan_steps, [])
        self.assertIsNone(task_state.current_plan_step_index)
        self.assertIsNone(task_state.planner_rationale)
        self.assertIsNone(task_state.actor_result)
        self.assertEqual(task_state.governance_history, [])
        self.assertIsNone(task_state.last_governance_decision)

    def test_build_actor_task_exposes_full_plan_and_current_step(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-complete"}},
        )

        task_state = result["task_state"]
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(len(task_state.observed_evidence), 1)

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)

    def test_build_actor_task_requires_current_plan_step(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_clarification_intake_result()},
            config={"configurable": {"thread_id": "layer2-actor-contract-clarification"}},
        )

        task_state = result["task_state"]

        with self.assertRaises(ValueError):
            build_actor_task(task_state=task_state)

    def test_complete_intake_supports_unbounded_tool_budget(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_tool_calls=None)},
            config={"configurable": {"thread_id": "layer2-unbounded-tool-budget"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observed_evidence), 1)

    def test_governance_input_uses_evaluated_step_context(self) -> None:
        task_state = _build_policy_input_task_state(read_only_mode=True)

        policy_input = build_policy_evaluation_input(task_state=task_state)

        self.assertEqual(policy_input.task_domain_type, DomainType.RESEARCH)
        self.assertEqual(policy_input.task_domain_label, "dma_research")
        self.assertEqual(policy_input.task_sensitivity, SensitivityLevel.LOW)
        self.assertTrue(policy_input.read_only_mode)
        self.assertEqual(policy_input.current_plan_step_id, "step_1")
        self.assertEqual(policy_input.current_plan_step_objective, "Collect the minimum official evidence needed for the task.")
        self.assertEqual(policy_input.actor_result_type, ActorResultType.PROPOSE_ACTION)
        self.assertEqual(policy_input.capability_mode, CapabilityAccessMode.READ_ONLY)
        self.assertEqual(policy_input.tool_name, ToolName.WEB_SEARCH)
        self.assertTrue(policy_input.tool_has_query)
        self.assertFalse(policy_input.tool_has_url)
        self.assertEqual(policy_input.tool_call_count, 0)
        self.assertEqual(policy_input.observed_evidence_count, 0)

    def test_opa_http_policy_engine_parses_structured_response_without_network(self) -> None:
        import json
        from unittest.mock import patch

        task_state = _build_policy_input_task_state(read_only_mode=True)
        policy_input = build_policy_evaluation_input(task_state=task_state)
        response_body = json.dumps(
            {
                "result": {
                    "policy_decision": "allow_read_only",
                    "reason_codes": ["read_only_allowed"],
                    "summary": "Read-only capability use is allowed.",
                }
            }
        ).encode("utf-8")

        engine = OPAHttpPolicyEngine(
            settings=OPAHttpPolicyEngineSettings(
                base_url="http://policy.test",
                decision_path="/v1/data/layer2/governance/decision",
                timeout_seconds=1.0,
            )
        )

        with patch("layer2_runtime_governance.governance.urlopen", return_value=FakeOPAResponse(response_body)):
            result = engine.evaluate(policy_input=policy_input)

        self.assertEqual(result.policy_decision, PolicyDecision.ALLOW_READ_ONLY)
        self.assertEqual(result.reason_codes, (GovernanceReasonCode.READ_ONLY_ALLOWED,))
        self.assertEqual(result.summary, "Read-only capability use is allowed.")
        self.assertEqual(result.policy_engine_name, "opa_http_policy_engine")
        self.assertEqual(result.policy_bundle_version, "layer2_governance_v1")

    def test_read_only_tool_request_routes_through_governance(self) -> None:
        graph = _build_test_graph(policy_engine_service=StubPolicyEngine())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(read_only_mode=True)},
            config={"configurable": {"thread_id": "layer2-read-only-governance"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.governance_history), 3)
        self.assertEqual(task_state.governance_history[0].policy_decision, PolicyDecision.ALLOW_READ_ONLY)
        self.assertEqual(task_state.governance_history[0].directive, GovernanceDirective.CONTINUE_READ_ONLY)
        self.assertEqual(task_state.last_governance_decision.policy_decision, PolicyDecision.ALLOW)
        self.assertEqual(task_state.last_governance_decision.directive, GovernanceDirective.CONTINUE)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "tool_gateway",
                "observe_tool_result",
                "actor",
                "governance",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )

    def test_duplicate_fetch_request_is_blocked_before_reexecution(self) -> None:
        graph = _build_test_graph(
            planner_service=SingleStepPlanner(),
            actor_service=RepeatFetchActor(),
            policy_engine_service=StubPolicyEngine(),
        )
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=1, max_tool_calls=10)},
            config={"configurable": {"thread_id": "layer2-duplicate-fetch-blocked"}},
        )

        task_state = result["task_state"]
        self.assertGreaterEqual(len(task_state.tool_results_history), 2)
        successful_results = [result for result in task_state.tool_results_history if result.success]
        failed_results = [result for result in task_state.tool_results_history if not result.success]

        self.assertGreaterEqual(len(successful_results), 1)
        self.assertGreaterEqual(len(failed_results), 1)
        self.assertTrue(
            all(
                sum(
                    1
                    for result in successful_results
                    if result.request.plan_step_id == plan_step_id
                )
                == 1
                for plan_step_id in {result.request.plan_step_id for result in successful_results}
            )
        )
        self.assertTrue(
            all(
                "same tool request" in (result.error_message or "")
                for result in failed_results
            )
        )

    def test_write_action_is_denied_by_governance(self) -> None:
        graph = _build_test_graph(actor_service=WriteActionActor())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-write-denied"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.ABORTED)
        self.assertEqual(task_state.tool_call_count, 0)
        self.assertEqual(task_state.last_governance_decision.policy_decision, PolicyDecision.DENY)
        self.assertEqual(task_state.last_governance_decision.directive, GovernanceDirective.STOP)
        self.assertEqual(len(task_state.governance_history), 1)
        self.assertEqual(task_state.governance_history[0].policy_decision, PolicyDecision.DENY)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )

    # Verify that the real OPA bundle blocks write capability even without external impact.
    def test_opa_policy_denies_write_capability_without_external_impact(self) -> None:
        opa_path = shutil.which("opa")
        if opa_path is None:
            self.skipTest("OPA executable is not available.")

        policy_input = build_policy_evaluation_input(task_state=_build_policy_input_task_state())
        opa_input = build_opa_input_document(policy_input=policy_input)
        opa_input["actor"]["capability_mode"] = "write"
        opa_input["actor"]["external_impact"] = False

        # Exercise the actual Rego bundle so the runtime policy and Python stub stay aligned.
        completed = subprocess.run(
            [
                opa_path,
                "eval",
                "--format",
                "json",
                "--data",
                str(REPO_ROOT / "configs" / "opa" / "layer2_governance" / "decision.rego"),
                "--stdin-input",
                "data.layer2.governance.decision",
            ],
            input=json.dumps(opa_input),
            text=True,
            capture_output=True,
            check=True,
        )
        parsed = json.loads(completed.stdout)
        decision = parsed["result"][0]["expressions"][0]["value"]

        self.assertEqual(decision["policy_decision"], "deny")
        self.assertIn("write_capability_blocked", decision["reason_codes"])

    def test_opa_input_marks_external_communication_draft_phase(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.external_communication.phase = ExternalCommunicationPhase.DRAFT_PROPOSAL
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft the cautious external email.",
                rationale="Keep the draft local before review.",
                expected_outcome="A local draft proposal.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.actor_turn_count = 1
        task_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="A local external communication draft is ready for review.",
            capability_mode=CapabilityAccessMode.NONE,
            external_impact=False,
            output_text="Draft email body.",
        )

        opa_input = build_opa_input_document(
            policy_input=build_policy_evaluation_input(task_state=task_state)
        )

        self.assertEqual(opa_input["external_communication"]["phase"], "draft_proposal")
        self.assertTrue(opa_input["actor"]["has_output_text"])

    def test_actor_asks_for_clarification_routes_to_waiting_review(self) -> None:
        graph = _build_test_graph(actor_service=ClarificationActor())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-clarification-governance"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(task_state.pending_human_review_request)
        self.assertIsNone(task_state.last_human_review_decision)
        self.assertIn("__interrupt__", result)
        interrupt_payload = result["__interrupt__"][0].value
        self.assertEqual(interrupt_payload["current_state"], "waiting_human_review")
        self.assertEqual(interrupt_payload["resume_state"], "acting")
        self.assertIn("ask_clarification", interrupt_payload["allowed_decisions"])
        self.assertEqual(
            task_state.last_governance_decision.policy_decision,
            PolicyDecision.ASK_CLARIFICATION,
        )
        self.assertEqual(
            task_state.last_governance_decision.directive,
            GovernanceDirective.ASK_USER_CLARIFICATION,
        )
        self.assertEqual(len(task_state.governance_history), 1)
        self.assertEqual(
            task_state.governance_history[0].policy_decision,
            PolicyDecision.ASK_CLARIFICATION,
        )
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
            ],
        )

    def test_human_review_interrupts_and_resumes_with_approval(self) -> None:
        graph = _build_test_graph(policy_engine_service=ReviewPolicyEngine())
        thread_id = "layer2-hitl-approval"
        initial = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": thread_id}},
        )

        task_state = initial["task_state"]
        interrupt_payload = initial["__interrupt__"][0].value
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertIsNotNone(task_state.pending_human_review_request)
        self.assertIsNone(task_state.last_human_review_decision)
        self.assertEqual(interrupt_payload["thread_id"], thread_id)
        self.assertEqual(interrupt_payload["current_state"], "waiting_human_review")
        self.assertEqual(interrupt_payload["resume_state"], "ready_tool_execution")
        self.assertIn("approve_continue", interrupt_payload["allowed_decisions"])

        resumed = graph.invoke(
            Command(
                resume={
                    "request_id": interrupt_payload["request_id"],
                    "decision_type": "approve_continue",
                    "note": "Approved to continue.",
                }
            ),
            config={"configurable": {"thread_id": thread_id}},
        )

        resumed_state = resumed["task_state"]
        self.assertEqual(resumed_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(
            resumed_state.last_human_review_decision.decision_type,
            HumanReviewDecisionType.APPROVE_CONTINUE,
        )
        self.assertEqual(
            resumed_state.last_human_review_request.request_id,
            interrupt_payload["request_id"],
        )
        self.assertEqual(len(resumed_state.human_review_history), 1)
        self.assertTrue(resumed_state.bootstrap_completed)

    def test_human_review_decision_routes_are_typed_and_request_id_is_checked(self) -> None:
        graph = _build_test_graph(policy_engine_service=ReviewPolicyEngine())
        thread_id = "layer2-hitl-routing"
        initial = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": thread_id}},
        )

        task_state = initial["task_state"]
        pending_request = task_state.pending_human_review_request
        assert pending_request is not None

        evidence_state = deepcopy(task_state)
        _apply_human_review_decision(
            evidence_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.REQUEST_MORE_EVIDENCE,
            ),
        )
        self.assertEqual(evidence_state.current_state, RuntimeState.ACTING)

        replan_state = deepcopy(task_state)
        _apply_human_review_decision(
            replan_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.REPLAN,
            ),
        )
        self.assertEqual(replan_state.current_state, RuntimeState.REPLANNING)

        abort_state = deepcopy(task_state)
        _apply_human_review_decision(
            abort_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.ABORT,
            ),
        )
        self.assertEqual(abort_state.current_state, RuntimeState.ABORTED)

        read_only_state = deepcopy(task_state)
        _apply_human_review_decision(
            read_only_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.APPROVE_CONTINUE_READ_ONLY,
            ),
        )
        self.assertEqual(read_only_state.current_state, RuntimeState.READY_TOOL_EXECUTION)
        self.assertTrue(read_only_state.execution_policy.read_only_mode)

        with self.assertRaises(ValueError):
            graph.invoke(
                Command(
                    resume={
                        "request_id": "wrong-request-id",
                        "decision_type": "approve_continue",
                    }
                ),
                config={"configurable": {"thread_id": thread_id}},
            )

    def test_human_review_modify_and_continue_applies_external_edit_and_resumes(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
            metadata={"priority": "high"},
        )
        pending_request = build_human_review_request(state=task_state, thread_id="external-edit")
        task_state.pending_human_review_request = pending_request

        self.assertIn(
            HumanReviewDecisionType.MODIFY_AND_CONTINUE,
            pending_request.allowed_decisions,
        )

        _apply_human_review_decision(
            task_state,
            HumanReviewDecision(
                request_id=pending_request.request_id,
                decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                external_communication_edit=HumanReviewExternalEdit(
                    draft=ExternalCommunicationDraft(
                        channel="slack",
                        recipient="#ops",
                        subject="Updated subject",
                        body="Updated body",
                        metadata={"priority": "critical"},
                    ),
                    note="Clarified the target before submission.",
                ),
            ),
        )

        self.assertEqual(task_state.current_state, RuntimeState.ACTING)
        self.assertEqual(
            task_state.external_communication.phase,
            ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
        )
        self.assertEqual(task_state.external_communication.draft.channel, "slack")
        self.assertEqual(task_state.external_communication.draft.recipient, "#ops")
        self.assertEqual(task_state.external_communication.last_applied_edit.note, "Clarified the target before submission.")
        self.assertEqual(len(task_state.external_communication.edit_history), 1)
        self.assertEqual(len(task_state.human_review_history), 0)

    def test_human_review_modify_and_continue_rejects_invalid_request_id(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
        )
        pending_request = build_human_review_request(state=task_state, thread_id="external-edit")
        task_state.pending_human_review_request = pending_request

        with self.assertRaises(ValueError):
            _apply_human_review_decision(
                task_state,
                HumanReviewDecision(
                    request_id="wrong-request-id",
                    decision_type=HumanReviewDecisionType.MODIFY_AND_CONTINUE,
                    external_communication_edit=HumanReviewExternalEdit(
                        draft=ExternalCommunicationDraft(
                            channel="email",
                            recipient="ops@example.com",
                            subject="Updated subject",
                            body="Updated body",
                        ),
                    ),
                ),
            )

    def test_build_human_review_request_coerces_restored_external_draft_dict(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = {
            "channel": "email",
            "recipient": "ops@example.com",
            "subject": "Original subject",
            "body": "Original body",
            "metadata": {"priority": "high"},
        }

        pending_request = build_human_review_request(
            state=task_state,
            thread_id="external-edit-restored",
        )

        self.assertIsInstance(task_state.external_communication.draft, ExternalCommunicationDraft)
        self.assertEqual(task_state.external_communication.draft.channel, "email")
        self.assertIsInstance(pending_request.external_communication_draft, ExternalCommunicationDraft)
        self.assertEqual(pending_request.external_communication_draft.recipient, "ops@example.com")

    def test_human_review_node_accepts_modify_and_continue_and_resumes(self) -> None:
        task_state = TaskState.from_intake(_build_external_communication_intake_result())
        task_state.current_state = RuntimeState.WAITING_HUMAN_REVIEW
        task_state.human_review_resume_state = RuntimeState.ACTING
        task_state.external_communication.phase = ExternalCommunicationPhase.HUMAN_REVIEW_REQUIRED
        task_state.external_communication.draft = ExternalCommunicationDraft(
            channel="email",
            recipient="ops@example.com",
            subject="Original subject",
            body="Original body",
            metadata={"priority": "high"},
        )
        pending_request = build_human_review_request(
            state=task_state,
            thread_id="external-edit-node",
        )
        task_state.pending_human_review_request = pending_request

        resume_payload = {
            "request_id": pending_request.request_id,
            "thread_id": pending_request.thread_id,
            "decision_type": "modify_and_continue",
            "note": "Submit the updated draft.",
            "reviewer_id": "operator-1",
            "external_communication_edit": {
                "draft": {
                    "channel": "slack",
                    "recipient": "#ops",
                    "subject": "Updated subject",
                    "body": "Updated body",
                    "metadata": {"priority": "critical"},
                },
                "note": "Updated the target before submission.",
            },
        }

        with patch("layer2_runtime_governance.hitl.interrupt", return_value=resume_payload):
            result_state = human_review(task_state, thread_id="external-edit-node")

        self.assertEqual(result_state.current_state, RuntimeState.ACTING)
        self.assertIsNone(result_state.pending_human_review_request)
        self.assertEqual(result_state.last_human_review_decision.decision_type, HumanReviewDecisionType.MODIFY_AND_CONTINUE)
        self.assertEqual(
            result_state.external_communication.phase,
            ExternalCommunicationPhase.APPROVED_FOR_EXECUTION,
        )
        self.assertEqual(result_state.external_communication.draft.channel, "slack")
        self.assertEqual(result_state.external_communication.draft.recipient, "#ops")
        self.assertEqual(result_state.external_communication.last_applied_edit.note, "Updated the target before submission.")
        self.assertEqual(len(result_state.external_communication.edit_history), 1)

    def test_intermediate_output_does_not_complete_step_without_validation(self) -> None:
        graph = _build_test_graph(actor_service=ProgressThenCompleteActor())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=1, max_tool_calls=10)},
            config={"configurable": {"thread_id": "layer2-intermediate-output-continues"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.actor_turn_count, 2)
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertEqual(len(task_state.governance_history), 2)
        self.assertEqual(
            [decision.directive for decision in task_state.governance_history],
            [GovernanceDirective.CONTINUE, GovernanceDirective.CONTINUE],
        )
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(
            task_state.step_validations[0].next_action,
            StepValidationDecision.CONTINUE_ACTING,
        )
        self.assertEqual(
            task_state.step_validations[1].next_action,
            StepValidationDecision.COMPLETE_STEP,
        )
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)

    def test_needs_replan_routes_through_replanner_and_continues(self) -> None:
        graph = _build_test_graph(actor_service=ReplanRequestActor())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result()},
            config={"configurable": {"thread_id": "layer2-replanner-continues"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(task_state.actor_turn_count, 2)
        self.assertEqual(
            [step.node_name for step in task_state.step_history],
            [
                "bootstrap_from_intake",
                "planner",
                "actor",
                "governance",
                "replanner",
                "actor",
                "governance",
                "finalize_bootstrap",
            ],
        )
        self.assertEqual(len(task_state.plan_steps), 1)
        self.assertEqual(task_state.plan_steps[0].step_id, "step_1_replanned")
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(task_state.planner_name, "stub_replanner")
        self.assertEqual(task_state.planner_prompt_version, "stub_replanner_v1")
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertEqual(task_state.executed_steps[0].step_id, "step_1_replanned")
        self.assertEqual(len(task_state.governance_history), 2)
        self.assertEqual(task_state.governance_history[0].directive, GovernanceDirective.REPLAN)
        self.assertEqual(task_state.governance_history[1].directive, GovernanceDirective.CONTINUE)
        self.assertEqual(
            task_state.final_output_text,
            "Completed from the replanned recovery step.",
        )
        compact_trace = build_compact_trace(
            {
                "thread_id": "layer2-replanner-continues",
                "intake_result": _build_research_intake_result(),
                "final_result": result,
            }
        )
        self.assertEqual(compact_trace["planning_snapshot"]["planning_source"], "replanner")
        self.assertEqual(compact_trace["governance_snapshot"]["oversight_pass_count"], 2)
        self.assertIsNotNone(compact_trace["governance_snapshot"]["last_oversight_assessment"])

    def test_full_trace_is_written_to_disk(self) -> None:
        trace = run_layer2_flow(
            raw_task_text="Find official sources about the DMA and summarize them.",
            context_hint="Prefer official EU sources.",
            thread_id="full-trace-file-test",
            demo_intake_profile="research",
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
            policy_engine_service=StubPolicyEngine(),
        )

        temp_dir = REPO_ROOT / "_layer2_trace_test_output"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            output_path = temp_dir / "full-trace-1.json"
            written_path = write_layer2_trace_file(trace, output_path=output_path)

            self.assertEqual(written_path, output_path)
            self.assertTrue(written_path.exists())

            payload = json.loads(written_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["runtime"]["thread_id"], "full-trace-file-test")
            self.assertEqual(payload["runtime"]["current_state"], "completed")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_run_layer2_flow_persists_runtime_artifacts(self) -> None:
        temp_dir = REPO_ROOT / "_layer2_runtime_artifacts_test"
        artifact_store = RuntimeArtifactStore(root_path=temp_dir)
        checkpoint_path = temp_dir / "checkpoints.pkl"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            trace = run_layer2_flow(
                raw_task_text="Find official sources about the DMA and summarize them.",
                context_hint="Prefer official EU sources.",
                thread_id="persisted-runtime-test",
                demo_intake_profile="research",
                planner_service=StubPlanner(),
                actor_service=StubActor(),
                tool_gateway_service=StubToolGateway(),
                replanner_service=StubReplanner(),
                policy_engine_service=StubPolicyEngine(),
                artifact_store=artifact_store,
                checkpointer=build_default_checkpointer(storage_path=checkpoint_path),
            )

            paths = artifact_store.build_paths(trace["thread_id"])
            self.assertTrue(paths.full_trace_path.exists())
            self.assertTrue(paths.compact_trace_path.exists())
            self.assertTrue(paths.dashboard_snapshot_path.exists())
            self.assertTrue(paths.runtime_summary_path.exists())

            dashboard_payload = json.loads(paths.dashboard_snapshot_path.read_text(encoding="utf-8"))
            runtime_summary = json.loads(paths.runtime_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(dashboard_payload["runtime_state"], "completed")
            self.assertEqual(runtime_summary["thread_id"], "persisted-runtime-test")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_behavioral_store_is_populated_and_second_run_sees_history(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_store_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(
                    database_path=temp_dir / "behavioral.sqlite3"
                )
            )

            first_trace = run_layer2_flow(
                raw_task_text="Find official sources about the DMA and summarize them.",
                context_hint="Prefer official EU sources.",
                thread_id="behavioral-run-1",
                demo_intake_profile="research",
                planner_service=StubPlanner(),
                actor_service=StubActor(),
                tool_gateway_service=StubToolGateway(),
                replanner_service=StubReplanner(),
                policy_engine_service=StubPolicyEngine(),
                behavioral_store_service=behavioral_store,
                checkpointer=build_default_checkpointer(storage_path=temp_dir / "cp1.pkl"),
            )
            first_state = first_trace["final_result"]["task_state"]
            self.assertFalse(first_state.last_behavioral_assessment.history_available)
            self.assertEqual(first_state.last_behavioral_assessment.historical_run_count, 0)

            second_trace = run_layer2_flow(
                raw_task_text="Find official sources about the DMA and summarize them.",
                context_hint="Prefer official EU sources.",
                thread_id="behavioral-run-2",
                demo_intake_profile="research",
                planner_service=StubPlanner(),
                actor_service=StubActor(),
                tool_gateway_service=StubToolGateway(),
                replanner_service=StubReplanner(),
                policy_engine_service=StubPolicyEngine(),
                behavioral_store_service=behavioral_store,
                checkpointer=build_default_checkpointer(storage_path=temp_dir / "cp2.pkl"),
            )
            second_state = second_trace["final_result"]["task_state"]
            self.assertTrue(second_state.last_behavioral_assessment.history_available)
            self.assertGreaterEqual(second_state.last_behavioral_assessment.historical_run_count, 1)
            self.assertTrue((temp_dir / "behavioral.sqlite3").exists())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_behavioral_below_threshold_stays_descriptive_and_continue(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_below_threshold_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            dynamic_policy = _strong_dynamic_policy_assessment()

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
                dynamic_policy_assessment=dynamic_policy,
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.CONTINUE,
            )
            self.assertIsNone(assessment.historical_similarity_score)
            self.assertIsNone(assessment.behavior_deviation_score)
            self.assertIsNone(assessment.novelty_score)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_behavioral_same_family_high_similarity_stays_continue(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_similarity_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="similar",
                profile_overrides={},
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
                dynamic_policy_assessment=_strong_dynamic_policy_assessment(),
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.CONTINUE,
            )
            self.assertIsNotNone(assessment.historical_similarity_score)
            self.assertGreaterEqual(assessment.historical_similarity_score, 0.80)
            self.assertGreaterEqual(assessment.matched_run_count, 5)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_behavioral_high_novelty_with_fragile_evidence_requests_more_evidence(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_novelty_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            state.plan_steps.extend(
                [
                    PlanStep(
                        step_id="step_4",
                        objective="Collect additional niche evidence.",
                        rationale="Make the current profile longer than history.",
                        expected_outcome="Extra context.",
                    ),
                    PlanStep(
                        step_id="step_5",
                        objective="Produce completion attempt.",
                        rationale="Force a completion-shaped runtime state.",
                        expected_outcome="Completion attempt.",
                    ),
                ]
            )
            state.actor_turn_count = 5
            state.tool_call_count = 2
            state.current_state = RuntimeState.ABORTED
            state.step_history.extend(
                [
                    StepRecord(3, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(4, RuntimeState.REPLANNING, "replanner", "Replanned."),
                ]
            )
            state.governance_history = [
                GovernanceDecision(
                    directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                    policy_decision=PolicyDecision.ALLOW,
                    reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                    summary="Escalated once.",
                    constrains_capability_use=False,
                    evaluated_actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                    evaluated_tool_name=None,
                    policy_engine_name="test_policy",
                    policy_bundle_version="test_v1",
                    policy_directive=GovernanceDirective.CONTINUE,
                    oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                )
            ]
            state.observed_evidence[0] = ObservedEvidence(
                evidence_id=state.observed_evidence[0].evidence_id,
                plan_step_id=state.observed_evidence[0].plan_step_id,
                title=state.observed_evidence[0].title,
                locator=state.observed_evidence[0].locator,
                excerpt=state.observed_evidence[0].excerpt,
                source_tool=state.observed_evidence[0].source_tool,
                support_level=EvidenceSupportLevel.SOURCE_DISCOVERY,
            )
            state.actor_result = ActorResult(
                plan_step_id="step_3",
                result_type=ActorResultType.FINAL_OUTPUT,
                summary="Final output attempted from weak evidence.",
                capability_mode=CapabilityAccessMode.NONE,
                output_text="A detailed final output from limited support.",
                step_validation=StepValidation(
                    plan_step_id="step_3",
                    objective_satisfied=True,
                    evidence_sufficient=False,
                    confidence=0.44,
                    next_action=StepValidationDecision.REQUEST_MORE_EVIDENCE,
                    rationale_summary="The output is still weakly supported.",
                ),
            )
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="novelty",
                profile_overrides={
                    "sensitivity": SensitivityLevel.MEDIUM.value,
                    "policy_profile_name": "legacy_profile",
                    "step_count": 2,
                    "actor_turn_count": 1,
                    "tool_call_count": 0,
                    "max_evidence_support_level": EvidenceSupportLevel.SOURCE_DISCOVERY.value,
                    "tool_name": ToolName.MCP_LOCAL_TEST_ECHO.value,
                },
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
                dynamic_policy_assessment=_weak_dynamic_policy_assessment(),
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.REQUEST_MORE_EVIDENCE,
            )
            self.assertIsNotNone(assessment.historical_similarity_score)
            self.assertLess(assessment.historical_similarity_score, 0.35)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_behavioral_deviation_can_trigger_replan(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_replan_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            state.plan_steps.extend(
                [
                    PlanStep(
                        step_id="step_4",
                        objective="Recover from divergence.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 1.",
                    ),
                    PlanStep(
                        step_id="step_5",
                        objective="Recover further.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 2.",
                    ),
                    PlanStep(
                        step_id="step_6",
                        objective="Recover further.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 3.",
                    ),
                    PlanStep(
                        step_id="step_7",
                        objective="Recover further.",
                        rationale="Force a wider trajectory gap.",
                        expected_outcome="Recovery path 4.",
                    ),
                ]
            )
            state.actor_turn_count = 10
            state.tool_call_count = 6
            state.plan_steps = state.plan_steps[:3]
            state.step_history.extend(
                [
                    StepRecord(3, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(4, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(5, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(6, RuntimeState.REPLANNING, "replanner", "Replanned."),
                    StepRecord(7, RuntimeState.REPLANNING, "replanner", "Replanned."),
                ]
            )
            state.governance_history = [
                GovernanceDecision(
                    directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                    policy_decision=PolicyDecision.ALLOW,
                    reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                    summary="Escalated once.",
                    constrains_capability_use=False,
                    evaluated_actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                    evaluated_tool_name=None,
                    policy_engine_name="test_policy",
                    policy_bundle_version="test_v1",
                    policy_directive=GovernanceDirective.CONTINUE,
                    oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                ),
                GovernanceDecision(
                    directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                    policy_decision=PolicyDecision.ALLOW,
                    reason_codes=(GovernanceReasonCode.ALLOWED_BY_POLICY,),
                    summary="Escalated twice.",
                    constrains_capability_use=False,
                    evaluated_actor_result_type=ActorResultType.INTERMEDIATE_OUTPUT,
                    evaluated_tool_name=None,
                    policy_engine_name="test_policy",
                    policy_bundle_version="test_v1",
                    policy_directive=GovernanceDirective.CONTINUE,
                    oversight_recommended_directive=GovernanceDirective.WAIT_HUMAN_REVIEW,
                ),
            ]
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="replan",
                profile_overrides={
                    "actor_turn_count": 2,
                    "tool_call_count": 1,
                    "replan_count": 0,
                },
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
                dynamic_policy_assessment=_strong_dynamic_policy_assessment(),
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.REPLAN,
            )
            self.assertIsNotNone(assessment.behavior_deviation_score)
            self.assertGreaterEqual(assessment.behavior_deviation_score, 0.60)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_behavioral_high_review_rate_with_unhealthy_dynamic_signals_waits_review(self) -> None:
        temp_dir = REPO_ROOT / "_behavioral_review_rate_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_behavioral_candidate_state()
            _seed_behavioral_history(
                behavioral_store=behavioral_store,
                count=24,
                prefix="review-heavy",
                profile_overrides={"review_count": 1},
            )

            assessment = behavioral_store.build_behavioral_assessment(
                thread_id="current-run",
                task_state=state,
                dynamic_policy_assessment=_review_dynamic_policy_assessment(),
            )

            self.assertEqual(
                assessment.behavioral_recommended_directive,
                BehavioralRecommendedDirective.WAIT_HUMAN_REVIEW,
            )
            self.assertIn("historical_review_rate_high", assessment.behavioral_flags)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_judge_is_not_invoked_for_static_review(self) -> None:
        state = _build_actor_ready_state_for_judge()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Propose one external read-only action.",
            capability_mode=CapabilityAccessMode.READ_ONLY,
            tool_request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Need one governed lookup.",
                query="governed lookup",
            ),
        )
        state.current_tool_request = state.actor_result.tool_request
        result = governance(
            state,
            policy_engine_service=ReviewPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="judge-static-review",
        )

        self.assertIsNotNone(result.last_judge_assessment)
        self.assertFalse(result.last_judge_assessment.invoked)
        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

    def test_judge_is_invoked_for_fragile_completion(self) -> None:
        state = _build_actor_ready_state_for_judge()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="Attempt fragile completion.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="A precise output from limited evidence.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=True,
                evidence_sufficient=False,
                confidence=0.40,
                next_action=StepValidationDecision.REQUEST_MORE_EVIDENCE,
                rationale_summary="Still not sufficiently grounded.",
            ),
        )
        state.current_tool_request = None

        result = governance(
            state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_more_evidence_assessment()),
            thread_id="judge-fragile-completion",
        )

        self.assertTrue(result.last_judge_assessment.invoked)
        self.assertEqual(
            result.last_judge_assessment.recommendation,
            JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE,
        )

    def test_judge_is_invoked_for_external_draft(self) -> None:
        state = _build_actor_ready_state_for_judge()
        external_intake = _build_external_communication_intake_result_for_governance()
        state = TaskState.from_intake(external_intake)
        state.current_state = RuntimeState.ACTING
        state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Draft the external note.",
                rationale="The task is in the external branch.",
                expected_outcome="One local draft.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        state.current_plan_step_index = 0
        state.external_communication.phase = ExternalCommunicationPhase.DRAFT_PROPOSAL
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary="Draft prepared.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Draft email body.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=True,
                evidence_sufficient=True,
                confidence=0.72,
                next_action=StepValidationDecision.COMPLETE_STEP,
                rationale_summary="Draft is complete.",
            ),
        )

        result = governance(
            state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="judge-external-draft",
        )

        self.assertTrue(result.last_judge_assessment.invoked)
        self.assertEqual(result.last_judge_assessment.evaluated_target, "external_draft")

    def test_governance_combiner_prefers_more_conservative_judge(self) -> None:
        state = _build_actor_ready_state_for_judge()
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.FINAL_OUTPUT,
            summary="Fragile final output.",
            capability_mode=CapabilityAccessMode.NONE,
            output_text="Possibly unsupported completion.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                objective_satisfied=True,
                evidence_sufficient=False,
                confidence=0.42,
                next_action=StepValidationDecision.REQUEST_MORE_EVIDENCE,
                rationale_summary="Output is not strongly grounded.",
            ),
        )
        state.current_tool_request = None

        result = governance(
            state,
            policy_engine_service=StubPolicyEngine(),
            judge_service=StaticJudge(_judge_wait_review_assessment()),
            thread_id="judge-precedence",
        )

        self.assertEqual(
            result.last_governance_decision.directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )
        self.assertEqual(
            result.last_governance_decision.judge_recommended_directive,
            GovernanceDirective.WAIT_HUMAN_REVIEW,
        )

    def test_judge_snapshot_is_persisted_in_sqlite(self) -> None:
        temp_dir = REPO_ROOT / "_judge_persistence_test"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        try:
            behavioral_store = SQLiteBehavioralStore(
                settings=BehavioralStoreSettings(database_path=temp_dir / "behavioral.sqlite3")
            )
            state = _build_actor_ready_state_for_judge()
            governance(
                state,
                policy_engine_service=StubPolicyEngine(),
                judge_service=StaticJudge(_judge_wait_review_assessment()),
                thread_id="judge-persisted-run",
                behavioral_store_service=behavioral_store,
            )
            self.assertGreaterEqual(len(state.judge_history), 1)

            import sqlite3

            with sqlite3.connect(temp_dir / "behavioral.sqlite3") as connection:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM judge_assessments WHERE thread_id = ?",
                    ("judge-persisted-run",),
                ).fetchone()[0]
            self.assertGreaterEqual(int(row_count), 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Verify replanning drops stale live tool context from the replaced step trajectory.
    def test_replanner_clears_stale_live_tool_context(self) -> None:
        state = _build_replanning_task_state()
        state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Old failed request.",
                url="https://example.org/source",
            ),
            success=False,
            summary="Old step failed.",
            error_message="Duplicate request blocked.",
        )
        state.current_tool_request = ToolRequest(
            plan_step_id="step_1",
            tool_name=ToolName.FETCH_URL_TEXT,
            reason="Old pending request.",
            url="https://example.org/source",
        )

        replanned_state = replanner(state, replanner_service=StubReplanner())

        self.assertIsNone(replanned_state.last_tool_result)
        self.assertIsNone(replanned_state.current_tool_request)
        self.assertEqual(replanned_state.current_plan_step.step_id, "step_1_replanned")

    def test_openai_replanner_converts_structured_payload_without_network(self) -> None:
        from layer2_runtime_governance.replanning import _ReplanningPayload, _ReplanStepPayload

        task_state = _build_replanning_task_state()
        payload = _ReplanningPayload(
            rationale_summary="The original step was too broad, so the remaining path was narrowed.",
            replacement_steps=[
                _ReplanStepPayload(
                    objective="Recover with a narrower semantic step.",
                    rationale="The actor reported the previous step was not executable.",
                    expected_outcome="A narrower step the actor can complete.",
                ),
                _ReplanStepPayload(
                    objective="Synthesize the recovered result.",
                    rationale="The task still requires a final grounded answer.",
                    expected_outcome="A concise final output.",
                ),
            ],
        )
        replanner = OpenAIReplanner(
            settings=OpenAIReplannerSettings(api_key="test-key", model="test-model")
        )
        fake_client = FakeReplannerClient(payload)
        replanner._client = fake_client

        decision = replanner.replan(task_state=task_state)

        self.assertEqual(decision.replanner_name, "openai_layer2_replanner")
        self.assertEqual(decision.prompt_version, "replanner_v1")
        self.assertEqual(len(decision.replacement_steps), 2)
        self.assertEqual(decision.replacement_steps[0].step_id, "replan_step_1")
        self.assertEqual(decision.replacement_steps[0].status, PlanStepStatus.PENDING)
        self.assertEqual(fake_client.responses.last_kwargs["model"], "test-model")
        self.assertEqual(fake_client.responses.last_kwargs["text_format"], _ReplanningPayload)

    def test_replanner_prompts_include_recovery_context(self) -> None:
        task_state = _build_replanning_task_state()

        system_prompt = build_replanner_system_prompt()
        user_prompt = build_replanner_user_prompt(task_state=task_state)

        self.assertIn("Do not modify or repeat completed steps", system_prompt)
        self.assertIn("actor_replan_request", user_prompt)
        self.assertIn("The initial step is too broad", user_prompt)
        self.assertIn("current_blocked_step", user_prompt)

    def test_default_actor_can_be_configured_to_openrouter(self) -> None:
        with patch.dict(
            os.environ,
            {"LAYER2_ACTOR_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-key"},
            clear=False,
        ):
            actor = build_default_actor()

        self.assertIsInstance(actor, OpenRouterActor)

    # Verify planner settings stay constructible when no timeout override exists.
    def test_openai_planner_settings_default_timeout_without_env(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "layer2_runtime_governance.planning._load_env_file_values",
            return_value={},
        ):
            settings = OpenAIPlannerSettings.from_env()

        self.assertEqual(settings.timeout_seconds, 30.0)

    def test_openrouter_actor_converts_json_action_to_actor_result_without_network(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertEqual(result.output_text, "OpenRouter actor completed the step.")
        self.assertIsNotNone(result.step_validation)
        self.assertEqual(result.step_validation.confidence, 0.82)

    def test_openrouter_actor_web_search_without_query_uses_step_fallback(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSearchWithoutQueryClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.PROPOSE_ACTION)
        self.assertIsNotNone(result.tool_request)
        self.assertEqual(result.tool_request.tool_name, ToolName.WEB_SEARCH)
        self.assertIn("Collect the minimum official evidence", result.tool_request.query)

    def test_openrouter_actor_completion_without_rationale_uses_summary(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterCompletionWithoutRationaleClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertEqual(result.summary, "Grounded concise final answer.")
        self.assertIsNotNone(result.step_validation)
        self.assertEqual(
            result.step_validation.rationale_summary,
            "Grounded concise final answer.",
        )
        self.assertIsNotNone(result.scratchpad_entry)
        self.assertEqual(
            result.scratchpad_entry.summary,
            "Grounded concise final answer.",
        )

    def test_openrouter_plain_text_response_is_normalized_to_progress_payload(self) -> None:
        payload = openrouter_client_module._extract_tool_call_payload(  # type: ignore[attr-defined]
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Plain text answer from a provider that ignored tool_choice.",
                        },
                    }
                ]
            }
        )

        self.assertEqual(payload["tool_name"], "emit_intermediate_output")
        self.assertEqual(payload["arguments"]["confidence"], 0.3)
        self.assertIn("Plain text answer", payload["arguments"]["output_text"])

    def test_openrouter_rejects_any_configured_model_except_required_actor_model(self) -> None:
        with self.assertRaisesRegex(
            openrouter_client_module.OpenRouterRuntimeError,
            "must be 'openai/gpt-oss-120b:free'",
        ):
            OpenRouterActor(
                settings=OpenRouterActorSettings(
                    model="some-other-model",
                    api_key="test-key",
                    base_url="https://openrouter.ai/api/v1",
                    timeout_seconds=1.0,
                )
            )

    def test_openrouter_rejects_response_model_different_from_required_actor_model(self) -> None:
        with self.assertRaisesRegex(
            openrouter_client_module.OpenRouterRuntimeError,
            "unexpected model",
        ):
            openrouter_client_module._validate_response_model(  # type: ignore[attr-defined]
                response_payload={"model": "some-other-model"},
                requested_model="openai/gpt-oss-120b:free",
            )

    def test_openrouter_accepts_only_required_actor_model(self) -> None:
        openrouter_client_module._validate_response_model(  # type: ignore[attr-defined]
            response_payload={"model": "openai/gpt-oss-120b:free"},
            requested_model="openai/gpt-oss-120b:free",
        )

    def test_openrouter_json_without_envelope_is_blocked_when_not_recoverable(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterJsonWithoutEnvelopeClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.BLOCKED)
        self.assertIn("malformed JSON", result.summary)
        self.assertIsNotNone(result.blocking_reason)
        self.assertIn("without a tool_name", result.blocking_reason)

    # Recovered completion-shaped JSON should not carry protocol noise after positive validation.
    def test_openrouter_recovered_high_level_completion_has_no_protocol_uncertainty(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterRecoveredHighLevelStepOneCompletionClient()  # type: ignore[attr-defined]
        state = _build_conservative_replan_summary_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.INTERMEDIATE_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(
            result.step_validation.next_action,
            StepValidationDecision.COMPLETE_STEP,
        )
        self.assertIsNone(result.step_validation.residual_uncertainty)

    # Verify the planner prompt explicitly discourages oversplitting small research tasks.
    def test_planner_prompt_discourages_small_task_over_segmentation(self) -> None:
        prompt = build_planner_system_prompt()

        self.assertIn("prefer 2-3 semantic steps", prompt)
        self.assertIn('Do not split "down-select sources"', prompt)

    # Verify the actor prompt reinforces current-step boundaries and completion with existing evidence.
    def test_actor_prompts_bias_toward_current_step_completion(self) -> None:
        state = _build_policy_input_task_state()
        state.current_plan_step.step_id = "replan_step_1"
        state.current_plan_step.objective = (
            "Use the already-fetched Commission page content to extract high-level supported themes."
        )
        state.observed_evidence.append(
            ObservedEvidence(
                evidence_id="replan_step_1:tool:1:source:1",
                plan_step_id="replan_step_1",
                title="Commission DMA Legislation",
                locator="https://digital-markets-act.ec.europa.eu/legislation_en",
                excerpt="The DMA contains the main rules for the designation of gatekeepers and the implementation of obligations and prohibitions.",
                source_tool=ToolName.FETCH_URL_TEXT,
            )
        )
        state.scratchpad.append(
            ScratchpadEntry(
                turn_index=1,
                plan_step_id="replan_step_1",
                entry_type=ScratchpadEntryType.OBSERVATION,
                summary="Observed tool result from 'fetch_url_text': URL text fetched successfully.",
            )
        )
        actor_task = build_actor_task(task_state=state)

        system_prompt = build_actor_system_prompt()
        user_prompt = build_actor_user_prompt(actor_task=actor_task)

        self.assertIn("Do not gather evidence for future steps", system_prompt)
        self.assertIn("replacement step objective overrides the older blocked path", system_prompt)
        self.assertIn("A discovered source URL is not factual support", system_prompt)
        self.assertIn('"prefer_completion_with_existing_evidence": true', user_prompt)
        self.assertIn('"replan_replacement_step_active": true', user_prompt)
        self.assertIn('"source_discovery_is_not_direct_fact_support": true', user_prompt)
        self.assertIn('"available_evidence_support_level": "high_level_summary"', user_prompt)

    # High-level summary mode should tell weaker actors to lower precision instead of chasing detail.
    def test_actor_prompts_high_level_summary_to_avoid_granular_examples(self) -> None:
        state = _build_conservative_replan_summary_task_state()
        state.observed_evidence.append(
            ObservedEvidence(
                evidence_id="replan_step_1:tool:6:source:1",
                plan_step_id="replan_step_1",
                title="Digital Markets Act (DMA) Legislation",
                locator="https://digital-markets-act.ec.europa.eu/legislation_en",
                excerpt=(
                    "The Procedural Implementing Regulation (EU) 2023/814 details "
                    "procedural aspects related to the implementation and enforcement "
                    "of the DMA."
                ),
                source_tool=ToolName.FETCH_URL_TEXT,
            )
        )
        actor_task = build_actor_task(task_state=state)

        system_prompt = build_actor_system_prompt()
        user_prompt = build_actor_user_prompt(actor_task=actor_task)

        self.assertIn("keep obligation language broad", system_prompt)
        self.assertIn(
            "describe them as procedural/implementation sources",
            system_prompt,
        )
        self.assertIn(
            '"current_or_prior_steps_have_procedural_implementation_source": true',
            user_prompt,
        )
        self.assertIn('"high_level_summary_mode": true', user_prompt)
        self.assertIn(
            '"avoid_granular_obligation_examples_unless_directly_observed": true',
            user_prompt,
        )
        self.assertIn("self-preferencing", user_prompt)
        self.assertIn("obligations and prohibitions for gatekeepers", user_prompt)

    def test_openrouter_completion_with_unsupported_precision_stays_in_progress(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterUnsupportedPrecisionCompletionClient()  # type: ignore[attr-defined]
        state = _build_high_precision_research_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertFalse(result.step_validation.objective_satisfied)
        self.assertFalse(result.step_validation.evidence_sufficient)
        self.assertEqual(
            result.step_validation.next_action,
            StepValidationDecision.REQUEST_MORE_EVIDENCE,
        )
        self.assertIn("more specific than the observed evidence", result.summary.lower())
        self.assertIsNotNone(result.step_validation.residual_uncertainty)
        self.assertIn("more specific legal or factual claims", result.step_validation.residual_uncertainty)

    # Internal trace markers should be removed from user-facing completion text.
    def test_openrouter_completion_sanitizes_internal_trace_refs(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterCompletionWithInternalRefsClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_year_in_citation()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertNotIn("【", result.output_text or "")
        self.assertNotIn("step_1:tool:", result.output_text or "")
        self.assertNotIn("【", result.step_validation.rationale_summary)

    def test_openrouter_source_shortlist_completion_stays_at_source_discovery(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSourceShortlistCompletionClient()  # type: ignore[attr-defined]
        state = _build_source_shortlist_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.INTERMEDIATE_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)

    # Confirm that a regulation-year mention still completes when the draft is not over-specific.
    def test_openrouter_summary_with_regulation_year_stays_supported(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSummaryWithRegulationYearClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_year_in_citation()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)

    # Procedural implementation sources should not be described as if they directly enforce the rules.
    def test_openrouter_procedural_implementation_summary_softens_enforcement_language(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterProceduralImplementationSummaryClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_year_in_citation()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertNotIn("enforces the rules", result.output_text.lower())
        self.assertNotIn(
            "establishes enforcement by the european commission",
            result.output_text.lower(),
        )
        self.assertIn(
            "describes implementation and enforcement through the European Commission",
            result.output_text,
        )

    # Confirm the same precision rule works for a different EU-law timeline fact.
    def test_openrouter_summary_with_generic_timeline_stays_supported(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterSummaryWithGenericTimelineClient()  # type: ignore[attr-defined]
        state = _build_summary_task_state_with_generic_timeline()
        observed_state = observe_tool_result(state)
        actor_task = build_actor_task(task_state=observed_state)

        self.assertEqual(
            observed_state.observed_evidence[0].support_level,
            EvidenceSupportLevel.DETAILED_EXTRACTED,
        )

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)

    # A conservative replanned summary should not be blocked by negated detailed-extraction wording.
    def test_openrouter_conservative_summary_after_extractability_limit_completes(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterConservativeSummaryAfterExtractabilityLimitClient()  # type: ignore[attr-defined]
        state = _build_conservative_replan_summary_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertIsNotNone(result.step_validation)
        self.assertTrue(result.step_validation.objective_satisfied)
        self.assertTrue(result.step_validation.evidence_sufficient)
        self.assertEqual(result.step_validation.next_action, StepValidationDecision.COMPLETE_STEP)

    # False "tools unavailable" blocked outcomes should recover into a read-only search request.
    def test_openrouter_false_no_tool_blocked_recovers_to_web_search(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterFalseNoToolBlockedClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.PROPOSE_ACTION)
        self.assertEqual(result.capability_mode, CapabilityAccessMode.READ_ONLY)
        self.assertIsNotNone(result.tool_request)
        self.assertEqual(result.tool_request.tool_name, ToolName.WEB_SEARCH)

    def test_openrouter_empty_response_becomes_blocked_actor_result(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        actor._client = _FakeOpenRouterEmptyResponseClient()  # type: ignore[attr-defined]
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        result = actor.act(actor_task=actor_task)

        self.assertEqual(result.result_type, ActorResultType.BLOCKED)
        self.assertIn("no usable runtime action", result.summary)
        self.assertIsNotNone(result.blocking_reason)

    def test_openrouter_actor_timeout_is_normalized(self) -> None:
        actor = OpenRouterActor(
            settings=OpenRouterActorSettings(
                model="openai/gpt-oss-120b:free",
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_seconds=1.0,
            )
        )
        state = _build_policy_input_task_state()
        actor_task = build_actor_task(task_state=state)

        with patch.object(openrouter_client_module, "urlopen", side_effect=TimeoutError()):
            with self.assertRaisesRegex(ActorExecutionError, "timed out"):
                actor.act(actor_task=actor_task)

    def test_planner_trims_semantic_plan_to_max_steps(self) -> None:
        graph = _build_test_graph()
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=1, max_tool_calls=10)},
            config={"configurable": {"thread_id": "layer2-max-steps-trimmed"}},
        )

        task_state = result["task_state"]
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertEqual(len(task_state.plan_steps), 1)
        self.assertEqual(task_state.plan_steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(len(task_state.executed_steps), 1)
        self.assertEqual(
            task_state.final_output_text,
            "Collected grounded evidence from official sources.",
        )
        self.assertIn("trimmed to the configured max_steps budget", task_state.step_history[1].summary)

    # Small source-plus-summary tasks should not become a three-step extraction pipeline.
    def test_planner_compacts_over_decomposed_small_source_summary_task(self) -> None:
        graph = _build_test_graph(planner_service=OverDecomposedSourceSummaryPlanner())
        result = graph.invoke(
            {"intake_result": _build_research_intake_result(max_steps=5, max_tool_calls=10)},
            config={"configurable": {"thread_id": "layer2-small-summary-plan-compacted"}},
        )

        task_state = result["task_state"]
        self.assertLessEqual(len(task_state.plan_steps), 2)
        self.assertIn("normalized to the task scope", task_state.step_history[1].summary)
        self.assertIn("high-level evidence", task_state.plan_steps[0].objective)
        self.assertIn("precision level supported", task_state.plan_steps[1].objective)

    # Verify source-level observation does not clone one global excerpt across many sources.
    def test_observe_tool_result_uses_single_fallback_evidence_for_multi_source_results(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect evidence.",
                rationale="The step needs grounded sources.",
                expected_outcome="Observed evidence.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Collect sources.",
                query="official eu dma sources",
            ),
            success=True,
            summary="Web search executed successfully.",
            content="One global transcript covering several returned sources in aggregate.",
            sources=(
                ToolSource(
                    title="Source A",
                    locator="https://example.org/a",
                    snippet=None,
                ),
                ToolSource(
                    title="Source B",
                    locator="https://example.org/b",
                    snippet=None,
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(len(observed_state.observed_evidence), 1)
        self.assertEqual(
            observed_state.observed_evidence[0].evidence_id,
            "step_1:tool:1:fallback",
        )

    # Empty extraction failures should become an explicit runtime signal for replanning.
    def test_observe_tool_result_marks_extractability_limit_on_empty_fetch(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect evidence from an official source.",
                rationale="The step needs a concrete official source.",
                expected_outcome="Observed evidence.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.FETCH_URL_TEXT,
                reason="Fetch official text.",
                url="https://eur-lex.europa.eu/legal-content/en/TXT/?uri=CELEX:32022R1925",
            ),
            success=False,
            summary="URL fetch succeeded but extracted readable text was empty.",
            content=None,
            error_message="URL fetch succeeded but extracted readable text was empty.",
            sources=(),
        )

        observed_state = observe_tool_result(task_state)

        self.assertIn(RuntimeFlag.EXTRACTABILITY_LIMITED, observed_state.runtime_flags)
        self.assertEqual(observed_state.observed_evidence, [])

    # Search snippets with actual official prose should count as high-level evidence, not pure discovery.
    def test_observe_tool_result_classifies_substantive_web_search_as_high_level_summary(self) -> None:
        task_state = TaskState.from_intake(_build_research_intake_result())
        task_state.plan_steps = [
            PlanStep(
                step_id="step_1",
                objective="Collect official DMA sources.",
                rationale="The step needs a grounded official overview.",
                expected_outcome="Observed evidence.",
                status=PlanStepStatus.IN_PROGRESS,
            )
        ]
        task_state.current_plan_step_index = 0
        task_state.tool_call_count = 1
        task_state.last_tool_result = ToolExecutionResult(
            request=ToolRequest(
                plan_step_id="step_1",
                tool_name=ToolName.WEB_SEARCH,
                reason="Find the Commission DMA overview.",
                query="Digital Markets Act European Commission official page",
            ),
            success=True,
            summary="Web search executed successfully.",
            content=(
                "Here are the European Commission official pages for the Digital Markets Act (DMA): "
                "DMA official website and DMA legislation hub, with overview statements about the "
                "gatekeepers, core platform services, obligations, and purpose of the DMA."
            ),
            sources=(
                ToolSource(
                    title="web_search",
                    locator="Digital Markets Act European Commission official page",
                    snippet=None,
                ),
            ),
        )

        observed_state = observe_tool_result(task_state)

        self.assertEqual(
            observed_state.observed_evidence[0].support_level,
            EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
        )


def _build_research_intake_result(
    *,
    max_steps: int = 5,
    max_tool_calls: int | None = 10,
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
                budget=BudgetConfig(max_steps=4, max_tool_calls=4),
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


def _build_policy_input_task_state(*, read_only_mode: bool = False) -> TaskState:
    state = TaskState.from_intake(_build_research_intake_result(read_only_mode=read_only_mode))
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
    with sqlite3.connect(behavioral_store.settings.database_path) as connection:
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
):
    return _ThreadIdInjectingGraph(
        build_runtime_graph(
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
