from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from layer1_intake import (
    DomainType,
    IntakeService,
    JsonExecutionPolicyRepository,
    ParseStatus,
    SensitivityLevel,
    TaskSpec,
    get_default_policy_config_path,
)
from layer1_intake.service import (
    InputNormalizer,
    IntakeResultValidator,
    PolicyResolver,
    TaskSpecBuilder,
)
from layer2_runtime_governance import (
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    BehavioralAssessment,
    BehavioralRecommendedDirective,
    CapabilityAccessMode,
    GovernanceDirective,
    GovernanceReasonCode,
    EvidenceSourceKind,
    EvidenceSupportLevel,
    PlanStep,
    PlanStepKind,
    PlannerDecision,
    PolicyDecision,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
    ReplanDecision,
    ReplannerPort,
    RuntimeFlag,
    RuntimeState,
    ScratchpadEntry,
    ScratchpadEntryType,
    StepValidation,
    StepValidationDecision,
    ToolExecutionResult,
    ToolGatewayPort,
    ToolName,
    ToolRequest,
    ToolSource,
)
from layer2_runtime_governance.infrastructure.checkpointing import (
    build_runtime_serializer,
)
from main_layer2 import run_layer2_flow


class StubParser:
    parser_name = "stub_parser"
    prompt_version = "integration_v1"

    def __init__(self, task_spec: TaskSpec) -> None:
        self._task_spec = task_spec

    def parse(self, *, raw_task_text: str, context_hint: str | None = None) -> TaskSpec:
        return self._task_spec


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 4, 14, 9, 0, 0, tzinfo=timezone.utc)


class FixedFingerprintService:
    def fingerprint(self, raw_text: str) -> str:
        return f"fp::{raw_text}"


class StubPlanner:
    planner_name = "stub_planner"
    prompt_version = "stub_v1"

    def plan(self, *, task_state):
        return PlannerDecision(
            plan_steps=[
                PlanStep(
                    step_id="step_1",
                    objective="Collect reliable evidence aligned with the intake goal.",
                    rationale="The first step should ground the task before synthesis.",
                    expected_outcome="A compact reliable evidence base.",
                    step_kind=PlanStepKind.SOURCE_DISCOVERY,
                    evidence_requirement=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                    claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                ),
                PlanStep(
                    step_id="step_2",
                    objective="Synthesize a grounded answer.",
                    rationale="A synthesis step should follow evidence gathering.",
                    expected_outcome="A concise grounded answer aligned with the goal.",
                    step_kind=PlanStepKind.FINAL_SYNTHESIS,
                    evidence_requirement=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                    claim_precision_ceiling=EvidenceSupportLevel.HIGH_LEVEL_SUMMARY,
                ),
            ],
            rationale="The stub planner selected an evidence-first semantic mini-plan.",
            planner_name=self.planner_name,
            prompt_version=self.prompt_version,
        )


class StubActor(ActorPort):
    actor_name = "stub_actor"
    prompt_version = "stub_actor_v1"

    # Drive one deterministic actor/tool/observation loop across two semantic steps.
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


class StubPolicyEngine(PolicyEnginePort):
    policy_engine_name = "stub_policy_engine"
    policy_bundle_version = "layer2_governance_v2"
    policy_config_version = "layer2_governance_thresholds_v1"

    # Select deterministic OPA-first pillars without contacting a policy service.
    def evaluate_required_pillars(self, *, base_facts):
        return {
            "require_evidence": True,
            "require_risk": True,
            "require_trajectory": True,
            "require_behavioral": True,
            "require_judge": False,
        }

    # Return the minimal schema-valid decision needed by the integration trajectory.
    def evaluate_decision(self, *, decision_input):
        base_facts = decision_input["base_facts"]
        actor = base_facts["actor"]
        tool = base_facts["tool"]
        evidence = decision_input["pillars"]["evidence"]["raw_facts"]
        trajectory = decision_input["pillars"]["trajectory"]["raw_facts"]
        directive = "continue"
        policy_decision = "allow"
        reason_code = "allowed_by_policy"
        summary = "The integration policy permits this runtime turn."

        if actor["capability_mode"] == "write" or actor["external_impact"]:
            directive = "stop"
            policy_decision = "deny"
            reason_code = "write_capability_blocked"
            summary = "Write-like capability is blocked by the integration policy."
        elif actor["result_type"] == ActorResultType.ASK_CLARIFICATION.value:
            directive = "ask_user_clarification"
            policy_decision = "ask_clarification"
            reason_code = "ask_clarification_required"
            summary = "Clarification is required by the integration policy."
        elif trajectory.get("provider_runtime_action_missing"):
            directive = "replan"
            reason_code = "repeated_recovery"
            summary = "Missing provider action requires replanning."
        elif evidence.get("support_rank_available", 0) < evidence.get("support_rank_required", 0):
            directive = "request_more_evidence"
            reason_code = "evidence_support_below_step_need"
            summary = "The step requires stronger evidence support."
        elif tool["name"] is not None and base_facts["policy"]["read_only_mode"]:
            directive = "continue_read_only"
            policy_decision = "allow_read_only"
            reason_code = "read_only_allowed"
            summary = "Read-only capability use is allowed."

        return {
            "schema_version": "governance_decision_output.v1",
            "directive": directive,
            "policy_decision": policy_decision,
            "reason_codes": [reason_code],
            "pillar_results": decision_input["pillars"],
            "selected_reason": summary,
            "rejected_alternatives": {},
            "safety_invariants_checked": ["integration_fixture_policy"],
            "requires_human_review": directive == "wait_human_review",
            "policy_bundle_version": self.policy_bundle_version,
            "policy_config_version": self.policy_config_version,
        }

    # Preserve the legacy policy port for compatibility-focused callers.
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


class StubReplanner(ReplannerPort):
    replanner_name = "stub_replanner"
    prompt_version = "stub_replanner_v1"

    def replan(self, *, task_state) -> ReplanDecision:
        return ReplanDecision(
            replacement_steps=[
                PlanStep(
                    step_id="step_1_replanned",
                    objective="Complete a deterministic recovery step.",
                    rationale="Integration tests should not call the live replanner.",
                    expected_outcome="A completed recovery output.",
                )
            ],
            rationale="The integration stub provided one recovery step.",
            replanner_name=self.replanner_name,
            prompt_version=self.prompt_version,
        )


class EmptyBehavioralStore:
    # Keep integration tests independent from history accumulated by other test modules.
    def build_behavioral_assessment(self, *, thread_id, task_state, dynamic_policy_assessment=None):
        return BehavioralAssessment(
            history_available=False,
            historical_run_count=0,
            feature_snapshot_version="integration_empty_v1",
            summary="No behavioral history is used by this integration fixture.",
            notes=(),
            behavioral_recommended_directive=BehavioralRecommendedDirective.CONTINUE,
        )

    # The integration fixture intentionally keeps no behavioral read model.
    def persist_behavioral_snapshot(self, *, thread_id, intake_result, task_state) -> None:
        return None

    # The integration fixture intentionally keeps no judge history.
    def persist_judge_snapshot(self, *, thread_id, task_state) -> None:
        return None


class Layer1Layer2IntegrationTests(unittest.TestCase):
    def test_research_task_bootstraps_end_to_end(self) -> None:
        trace = run_layer2_flow(
            raw_task_text="  Find official sources about the DMA and summarize them.  ",
            context_hint="  Prefer official EU sources.  ",
            thread_id="layer1-layer2-bootstrap-research",
            intake_service=_build_intake_service(
                TaskSpec(
                    goal="Find official sources about the DMA and summarize them.",
                    context="Prefer official EU sources.",
                    constraints=["Prefer official EU sources."],
                    domain_type=DomainType.RESEARCH,
                    domain_label="dma_research",
                    sensitivity=SensitivityLevel.LOW,
                    success_criteria=["Produce a grounded short summary."],
                )
            ),
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
            policy_engine_service=StubPolicyEngine(),
            behavioral_store_service=EmptyBehavioralStore(),
            checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        )

        intake_result = trace["intake_result"]
        task_state = trace["final_result"]["task_state"]

        self.assertEqual(intake_result.metadata.parse_status, ParseStatus.COMPLETE)
        self.assertEqual(
            intake_result.metadata.raw_input_fingerprint,
            "fp::Find official sources about the DMA and summarize them.",
        )
        self.assertEqual(task_state.current_state, RuntimeState.COMPLETED)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertEqual(task_state.task_spec.goal, intake_result.structured_task.task_spec.goal)
        self.assertEqual(task_state.execution_policy, intake_result.structured_task.execution_policy)
        self.assertEqual(task_state.intake_metadata, intake_result.metadata)
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
        self.assertIsNone(task_state.current_plan_step_index)
        self.assertEqual(task_state.planner_name, "stub_planner")
        self.assertIsNotNone(task_state.actor_result)
        self.assertEqual(task_state.actor_result.result_type, ActorResultType.FINAL_OUTPUT)
        self.assertEqual(task_state.actor_result.capability_mode, CapabilityAccessMode.NONE)
        self.assertEqual(len(task_state.executed_steps), 2)
        self.assertEqual(task_state.tool_call_count, 1)
        self.assertEqual(len(task_state.observed_evidence), 3)
        self.assertEqual(
            sum(
                item.source_kind == EvidenceSourceKind.TOOL_OBSERVATION
                for item in task_state.observed_evidence
            ),
            1,
        )
        self.assertEqual(len(task_state.scratchpad), 4)
        self.assertEqual(len(task_state.step_validations), 2)
        self.assertEqual(len(task_state.governance_history), 3)
        self.assertEqual(task_state.governance_history[0].policy_decision, PolicyDecision.ALLOW)
        self.assertEqual(
            task_state.governance_history[0].directive,
            GovernanceDirective.REQUEST_MORE_EVIDENCE,
        )

    def test_clarification_task_bootstraps_end_to_end(self) -> None:
        trace = run_layer2_flow(
            raw_task_text="Review this contract and tell me whether I should sign it.",
            context_hint="No additional context.",
            thread_id="layer1-layer2-bootstrap-clarification",
            intake_service=_build_intake_service(
                TaskSpec(
                    goal="Review this contract and tell me whether I should sign it.",
                    context="Contract review request.",
                    constraints=[],
                    domain_type=DomainType.ANALYSIS,
                    domain_label="contract_review",
                    sensitivity=SensitivityLevel.HIGH,
                    success_criteria=[],
                )
            ),
            planner_service=StubPlanner(),
            actor_service=StubActor(),
            tool_gateway_service=StubToolGateway(),
            replanner_service=StubReplanner(),
            policy_engine_service=StubPolicyEngine(),
            behavioral_store_service=EmptyBehavioralStore(),
            checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        )

        intake_result = trace["intake_result"]
        task_state = trace["final_result"]["task_state"]

        self.assertEqual(intake_result.metadata.parse_status, ParseStatus.NEEDS_CLARIFICATION)
        self.assertEqual(task_state.current_state, RuntimeState.WAITING_HUMAN_REVIEW)
        self.assertTrue(task_state.bootstrap_completed)
        self.assertIn(RuntimeFlag.CLARIFICATION_REQUIRED, task_state.runtime_flags)
        self.assertIn(RuntimeFlag.HIGH_SENSITIVITY, task_state.runtime_flags)
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
        self.assertIsNone(task_state.actor_result)
        self.assertEqual(task_state.governance_history, [])


def _build_intake_service(task_spec: TaskSpec) -> IntakeService:
    return IntakeService(
        normalizer=InputNormalizer(),
        task_spec_builder=TaskSpecBuilder(parser=StubParser(task_spec)),
        policy_resolver=PolicyResolver(
            repository=JsonExecutionPolicyRepository(
                config_path=get_default_policy_config_path()
            )
        ),
        validator=IntakeResultValidator(),
        clock=FixedClock(),
        fingerprint_service=FixedFingerprintService(),
    )


if __name__ == "__main__":
    unittest.main()
