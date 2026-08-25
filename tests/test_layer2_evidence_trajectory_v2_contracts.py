from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from layer1_intake import (
    ExecutionIntent,
    ExecutionPolicy,
    IntakeMetadata,
    IntakeResult,
    ParseStatus,
    SensitivityLevel,
    StructuredTask,
    TaskSpec,
)
from layer2_runtime_governance.runtime.governance_service import (
    _build_evidence_feedback_findings,
)
from layer2_runtime_governance.signal_services.evidence_service import (
    _build_literal_corpus_index,
    _declares_insufficiency,
    _literal_is_anchored,
    _task_literal_corpus,
)
from layer2_runtime_governance.actor_contract import (
    ActorResult,
    ActorResultType,
)
from layer2_runtime_governance.domain import (
    EvidenceBurden,
    ExternalCommunicationDraft,
    PlanStep,
    PlanStepStatus,
    StepCompletionCandidate,
    StepValidation,
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    derive_task_evidence_floor,
    digest_json,
    normalize_task_state_serialization,
)
from layer2_runtime_governance.governance_components import build_policy_evaluation_input
from layer2_runtime_governance.signal_services.evidence_service import build_evidence_signal
from layer2_runtime_governance.signal_services.trajectory_service import build_trajectory_signal
from layer3_tool_bus_external_capabilities.external_operations import (
    normalize_communication_operation,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolErrorCode,
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
    RetrievalKind,
)


def _build_test_task_state(
    *,
    produces_factual_claims: bool = False,
    produces_external_effect_proposal: bool = False,
    evidence_burden: EvidenceBurden | None = None,
    task_spec: TaskSpec | None = None,
) -> TaskState:
    intake = IntakeResult(
        structured_task=StructuredTask(
            task_spec=task_spec
            or TaskSpec(
                goal="Test evidence and trajectory contracts v2",
                context="Unit test fixtures",
                constraints=["Deterministic execution"],
                sensitivity=SensitivityLevel.LOW,
                success_criteria=["Verify structural facts"],
                execution_intent=ExecutionIntent.PROPOSAL_ONLY,
            ),
            execution_policy=ExecutionPolicy(
                policy_profile_name="test_policy",
                confidence_threshold=0.7,
                read_only_mode=False,
            ),
        ),
        metadata=IntakeMetadata(
            request_id=f"req-{uuid4()}",
            parse_status=ParseStatus.COMPLETE,
            parser_name="test_parser",
            prompt_version="v1",
            schema_version="1.0.0",
        ),
    )
    state = TaskState.from_intake(intake)
    step = PlanStep(
        step_id="step_1",
        objective="Collect official data",
        rationale="Step needs evidence",
        expected_outcome="Data collected",
        status=PlanStepStatus.IN_PROGRESS,
        produces_factual_claims=produces_factual_claims,
        produces_governed_external_effect_proposal=produces_external_effect_proposal,
        evidence_burden=evidence_burden,
    )
    state.plan_steps.append(step)
    state.current_plan_step_index = 0
    return state


class EvidenceTrajectoryV2ContractsTests(unittest.TestCase):
    # Derive only explicit proof language from constraints and success criteria.
    def test_task_floor_parses_word_counts_freshness_and_explicit_fallback(self) -> None:
        task_spec = TaskSpec(
            goal="Ignore any source count mentioned in the goal.",
            context="The context may mention at least nine sources, but it is not binding.",
            constraints=[
                "Use at least three valid independent sources within 30 days.",
                "If fewer than three sources are available, explicitly declare insufficiency.",
            ],
            sensitivity=SensitivityLevel.LOW,
            success_criteria=["At least three sources support the result."],
            execution_intent=ExecutionIntent.PROPOSAL_ONLY,
        )

        floor = derive_task_evidence_floor(task_spec)

        self.assertIsNotNone(floor)
        self.assertEqual(floor.min_independent_sources, 3)
        self.assertTrue(floor.require_retrieved_content)
        self.assertEqual(floor.freshness_window_days, 30)
        self.assertTrue(floor.insufficiency_declaration_permitted)

        no_floor = TaskSpec(
            goal="The goal says at least ninety-nine sources, but this is not a proof contract.",
            context="No explicit evidence requirement.",
            constraints=[],
            sensitivity=SensitivityLevel.LOW,
            success_criteria=[],
            execution_intent=ExecutionIntent.PROPOSAL_ONLY,
        )
        self.assertIsNone(derive_task_evidence_floor(no_floor))

    # A weaker declared burden cannot hide a stronger task-level minimum.
    def test_task_floor_lifts_declared_burden_in_evidence_signal(self) -> None:
        task_spec = TaskSpec(
            goal="Produce a grounded answer.",
            context="",
            constraints=["Use at least three reliable sources."],
            sensitivity=SensitivityLevel.LOW,
            success_criteria=["State the result."],
            execution_intent=ExecutionIntent.PROPOSAL_ONLY,
        )
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(
                min_independent_sources=1,
                require_retrieved_content=False,
            ),
            task_spec=task_spec,
        )

        facts = build_evidence_signal(task_state=state, thresholds_version="v1")["raw_facts"]

        self.assertEqual(facts["min_independent_sources_required"], 3)
        self.assertFalse(facts["retrieved_content_satisfied"])
        self.assertFalse(facts["insufficiency_declaration_permitted"])

    # External-effect proposals activate structural evidence families even without factual claims.
    def test_external_effect_proposal_activates_evidence_families(self) -> None:
        state = _build_test_task_state(produces_external_effect_proposal=True)
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared a governed external proposal.",
            output_text="The proposal is ready for human review.",
        )

        facts = build_evidence_signal(task_state=state, thresholds_version="v1")["raw_facts"]

        self.assertFalse(facts["produces_factual_claims"])
        self.assertTrue(facts["produces_governed_external_effect_proposal"])
        self.assertTrue(facts["evidence_families_active"])
        self.assertTrue(facts["evidence_completion_attempt"])

    # Search snippets do not count as independently retrieved source coverage.
    def test_search_snippet_does_not_satisfy_independent_source_coverage(self) -> None:
        burden = EvidenceBurden(min_independent_sources=2, require_retrieved_content=True)
        state = _build_test_task_state(produces_factual_claims=True, evidence_burden=burden)
        state.observations.extend(
            [
                ToolObservation(
                    observation_id="obs-search",
                    content="Search result title",
                    locator="https://source-a.org/search-result",
                    retrieval_kind=RetrievalKind.SEARCH_SNIPPET,
                ),
                ToolObservation(
                    observation_id="obs-fetch",
                    content="Fetched source content",
                    locator="https://source-b.org/article",
                    retrieval_kind=RetrievalKind.FETCHED_DOCUMENT,
                ),
            ]
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Claim uses a search result and one fetched source.",
            output_text="The result is supported by the available material.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-search", "obs-fetch"),
                rationale_summary="The observation set is intentionally mixed.",
            ),
        )

        facts = build_evidence_signal(task_state=state, thresholds_version="v1")["raw_facts"]

        self.assertEqual(facts["distinct_independent_sources_count"], 1)
        self.assertTrue(facts["retrieved_content_satisfied"])
        self.assertFalse(facts["independent_sources_satisfied"])
        self.assertIn("insufficient_independent_sources", facts["deficit_codes"])

    def test_nonfactual_step_has_no_declared_coverage_requirement(self) -> None:
        state = _build_test_task_state(produces_factual_claims=False, evidence_burden=None)
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Completed without explicit factual burden",
            output_text=(
                "Reference date: 24 August 2026. The requested 30-day window starts "
                "on 25 July 2026."
            ),
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=(),
                rationale_summary="General knowledge used.",
            ),
        )
        snapshot_digest = digest_json(
            build_evidence_snapshot_manifest(task_state=state)
        )
        selection_digest = build_evidence_selection_digest(
            selected_refs=(),
            evidence_snapshot_digest=snapshot_digest,
            external_operation_digest=None,
        )
        # Mirror the candidate binding created by the production actor service.
        state.pending_completion_candidate = StepCompletionCandidate(
            candidate_id="step_1:candidate:1",
            step_id="step_1",
            summary=state.actor_result.summary,
            output_text=state.actor_result.output_text or "",
            evidence_snapshot_digest=snapshot_digest,
            evidence_selection_digest=selection_digest,
        )
        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertTrue(facts["completion_attempt"])
        self.assertFalse(facts["factual_completion_attempt"])
        self.assertFalse(facts["coverage_evaluated"])
        self.assertTrue(facts["independent_sources_satisfied"])
        self.assertTrue(facts["retrieved_content_satisfied"])
        self.assertTrue(facts["burden_satisfied"])
        self.assertTrue(facts["has_unanchored_literals_in_universe"])
        self.assertNotIn("insufficient_independent_sources", facts["deficit_codes"])
        self.assertNotIn("retrieved_content_missing", facts["deficit_codes"])
        self.assertNotIn("unanchored_literals_in_selection", facts["deficit_codes"])
        self.assertNotIn("unanchored_literals_in_universe", facts["deficit_codes"])
        self.assertFalse(facts["has_evidence_deficit"])
        self.assertEqual(signal["mode"], "active")
        self.assertTrue(signal["decision_influence"])

    # Preserve a diagnostic route for legacy state that bypassed plan construction.
    def test_factual_step_without_burden_is_explicitly_detected(self) -> None:
        state = _build_test_task_state()
        # Simulate a legacy checkpoint that predates the canonical PlanStep invariant.
        state.plan_steps[0].produces_factual_claims = True
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Malformed factual completion",
            output_text="The result is factual.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=(),
                rationale_summary="Legacy state exercised defensively.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertTrue(facts["factual_step_missing_evidence_burden"])
        self.assertFalse(facts["coverage_evaluated"])
        self.assertFalse(facts["burden_satisfied"])
        self.assertIn("missing_evidence_burden", facts["deficit_codes"])

    def test_evidence_burden_fail_closed_on_insufficient_sources(self) -> None:
        burden = EvidenceBurden(min_independent_sources=2, require_retrieved_content=True)
        state = _build_test_task_state(produces_factual_claims=True, evidence_burden=burden)

        obs1 = ToolObservation(observation_id="obs-1", content="Fact from domain A", locator="https://source-a.org/data")
        state.observations.append(obs1)

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Claim made with only one source",
            output_text="Revenue reached $100M in 2026.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-1",),
                rationale_summary="Grounded on source A.",
            ),
        )
        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertTrue(facts["coverage_evaluated"])
        self.assertEqual(facts["min_independent_sources_required"], 2)
        self.assertEqual(facts["distinct_independent_sources_count"], 1)
        self.assertFalse(facts["independent_sources_satisfied"])
        self.assertFalse(facts["burden_satisfied"])
        self.assertIn("insufficient_independent_sources", facts["deficit_codes"])

    # Accept a source-attributed timestamp only when it lies within the declared window.
    def test_freshness_window_requires_source_timestamp_within_window(self) -> None:
        reference_time = datetime(2026, 8, 24, tzinfo=timezone.utc)
        burden = EvidenceBurden(
            min_independent_sources=1,
            require_retrieved_content=True,
            freshness_window_days=7,
        )
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=burden,
        )
        observation = ToolObservation(
            observation_id="obs-fresh",
            content="Revenue reached $100M in 2026.",
            locator="https://source-a.org/data",
            source_timestamp=reference_time - timedelta(days=2),
        )
        state.observations.append(observation)
        state.tool_results_history.append(
            ToolExecutionResult(
                request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.FETCH_URL_TEXT,
                    reason="Fetch source metadata.",
                    url="https://source-a.org/data",
                ),
                success=True,
                summary="Source fetched.",
                observations=(observation,),
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Fresh factual completion",
            output_text="Revenue reached $100M in 2026.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-fresh",),
                rationale_summary="The source metadata is recent.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
            evaluated_at=reference_time,
        )["raw_facts"]

        self.assertTrue(facts["freshness_evaluated"])
        self.assertTrue(facts["freshness_satisfied"])
        self.assertEqual(
            facts["selected_source_timestamps"]["obs-fresh"],
            "2026-08-22T00:00:00+00:00",
        )

    # Fail closed for both absent and stale source metadata.
    def test_freshness_window_rejects_missing_and_stale_source_metadata(self) -> None:
        reference_time = datetime(2026, 8, 24, tzinfo=timezone.utc)
        burden = EvidenceBurden(freshness_window_days=7)

        stale_state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=burden,
        )
        stale_observation = ToolObservation(
            observation_id="obs-stale",
            content="Revenue reached $100M in 2026.",
            locator="https://source-a.org/data",
            source_timestamp=reference_time - timedelta(days=8),
        )
        stale_state.observations.append(stale_observation)
        stale_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Stale factual completion",
            output_text="Revenue reached $100M in 2026.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-stale",),
                rationale_summary="Exercise stale source handling.",
            ),
        )
        stale_facts = build_evidence_signal(
            task_state=stale_state,
            thresholds_version="v1",
            evaluated_at=reference_time,
        )["raw_facts"]

        self.assertFalse(stale_facts["freshness_satisfied"])
        self.assertIn("obs-stale", stale_facts["stale_evidence_observation_ids"])
        self.assertIn("stale_evidence", stale_facts["deficit_codes"])

        missing_state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=burden,
        )
        missing_observation = ToolObservation(
            observation_id="obs-missing-timestamp",
            content="Revenue reached $100M in 2026.",
            locator="https://source-a.org/data",
        )
        missing_state.observations.append(missing_observation)
        missing_state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Missing source timestamp",
            output_text="Revenue reached $100M in 2026.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-missing-timestamp",),
                rationale_summary="Exercise missing metadata handling.",
            ),
        )
        missing_facts = build_evidence_signal(
            task_state=missing_state,
            thresholds_version="v1",
            evaluated_at=reference_time,
        )["raw_facts"]

        self.assertFalse(missing_facts["freshness_satisfied"])
        self.assertIn(
            "obs-missing-timestamp",
            missing_facts["freshness_missing_observation_ids"],
        )
        self.assertIn("freshness_metadata_missing", missing_facts["deficit_codes"])

    # Reject an echo transformation that cannot independently support a factual claim.
    def test_actor_derived_tool_output_is_rejected_as_circular_evidence(self) -> None:
        burden = EvidenceBurden()
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=burden,
        )
        observation = ToolObservation(
            observation_id="obs-echo",
            content="Revenue reached $100M in 2026.",
            locator="mcp://local_test/echo",
        )
        state.observations.append(observation)
        state.tool_results_history.append(
            ToolExecutionResult(
                request=ToolRequest(
                    plan_step_id="step_1",
                    tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
                    reason="Echo actor-provided text.",
                    text="Revenue reached $100M in 2026.",
                ),
                success=True,
                summary="Echo completed.",
                observations=(observation,),
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Circular factual completion",
            output_text="Revenue reached $100M in 2026.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-echo",),
                rationale_summary="Exercise actor-derived source detection.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertTrue(facts["circular_reference_detected"])
        self.assertEqual(
            facts["circular_evidence_reasons"]["obs-echo"],
            "actor_derived_tool_output",
        )
        self.assertIn("circular_evidence_reference", facts["deficit_codes"])

    def test_literal_anchoring_identifies_unanchored_tokens(self) -> None:
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        obs1 = ToolObservation(observation_id="obs-1", content="Growth was 15% in 2025.", locator="https://source-a.org/data")
        state.observations.append(obs1)

        draft = ExternalCommunicationDraft(
            channel="email",
            recipient="partner@example.com",
            subject="Results",
            body="Growth was 15% and revenue reached $500M on 2026-05-10 at https://secret.org.",
        )
        state.external_operation_proposal = normalize_communication_operation(
            operation_id="op-1",
            plan_step_id="step_1",
            capability_id="communication.email.send",
            draft=draft,
            execution_candidate=True,
        )

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Report prepared",
            output_text="Prepared email.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.8,
                evidence_refs=("obs-1",),
                rationale_summary="Report references obs-1.",
            ),
        )

        signal = build_evidence_signal(task_state=state, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertTrue(facts["has_unanchored_literals_in_selection"])
        self.assertTrue(facts["has_unanchored_literals_in_universe"])
        self.assertIn("unanchored_literals_in_selection", facts["deficit_codes"])
        self.assertIn("unanchored_literals_in_universe", facts["deficit_codes"])
        # Check that extracted tokens include $500M or 2026-05-10 or https://secret.org
        unanchored_univ = facts["unanchored_literals_in_universe"]
        self.assertTrue(any("500" in tok or "secret.org" in tok or "2026" in tok for tok in unanchored_univ))

    # A selected observation's exact locator is part of its auditable evidence corpus.
    def test_literal_url_is_anchored_by_selected_observation_locator(self) -> None:
        source_url = "https://source-a.org/official-announcement"
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.observations.append(
            ToolObservation(
                observation_id="obs-url",
                content="The official announcement describes the release.",
                locator=source_url,
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared one linked factual result.",
            output_text=f"Official announcement: {source_url}.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-url",),
                rationale_summary="The selected locator is the cited source.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertNotIn(source_url, facts["unanchored_literals_in_selection"])
        self.assertNotIn(source_url, facts["unanchored_literals_in_universe"])

    # Tracking-only query parameters do not make a clean citation unsupported.
    def test_literal_url_ignores_tracking_parameters_but_keeps_meaningful_queries(self) -> None:
        clean_url = "https://source-a.org/official-announcement"
        tracked_url = f"{clean_url}?utm_source=openai"
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.observations.append(
            ToolObservation(
                observation_id="obs-url",
                content="The official announcement describes the release.",
                locator=tracked_url,
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared one linked factual result.",
            output_text=f"Official announcement: {clean_url}.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-url",),
                rationale_summary="The selected locator is the cited source.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertNotIn(clean_url, facts["unanchored_literals_in_selection"])
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared a materially different linked result.",
            output_text=f"Different view: {clean_url}?version=2",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-url",),
                rationale_summary="The query changes the cited resource.",
            ),
        )
        changed_facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]
        self.assertIn(
            f"{clean_url}?version=2",
            changed_facts["unanchored_literals_in_selection"],
        )

    # Layout numbers and UUID fragments are not claims; localized currency remains checkable.
    def test_literal_extraction_ignores_structure_and_identifier_fragments(self) -> None:
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.observations.append(
            ToolObservation(
                observation_id="obs-price",
                content="The observed rental price was $5.30 per GPU-hour.",
                locator="https://source-a.org/pricing",
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared a localized result.",
            output_text=(
                "Articolo 1.1: prezzo $5,30. "
                "Riferimento effed34f-8ea6-4035-9199-be576344cbef:document."
            ),
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-price",),
                rationale_summary="The price is supported by the selected observation.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertEqual(facts["target_literal_token_count"], 1)
        self.assertFalse(facts["has_unanchored_literals_in_selection"])
        self.assertFalse(facts["has_unanchored_literals_in_universe"])

    # URL-significant punctuation is preserved and cannot match a different locator.
    def test_literal_url_with_significant_terminal_punctuation_is_unanchored(self) -> None:
        observed_url = "https://source-a.org/official-announcement"
        claimed_url = f"{observed_url}!"
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.observations.append(
            ToolObservation(
                observation_id="obs-url",
                content="The official announcement describes the release.",
                locator=observed_url,
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared one linked factual result.",
            output_text=f"Official announcement: {claimed_url}",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-url",),
                rationale_summary="The claimed URL differs from the selected locator.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertIn(claimed_url, facts["unanchored_literals_in_selection"])
        self.assertIn(claimed_url, facts["unanchored_literals_in_universe"])

    # URL prefixes are not evidence for a different, longer observed locator.
    def test_literal_url_prefix_is_not_anchored_by_longer_locator(self) -> None:
        claimed_url = "https://source-a.org/doc"
        observed_url = "https://source-a.org/document"
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.observations.append(
            ToolObservation(
                observation_id="obs-url",
                content=f"The source is available at {observed_url}.",
                locator=observed_url,
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Prepared one linked factual result.",
            output_text=f"Official announcement: {claimed_url}",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-url",),
                rationale_summary="The claimed URL is only a prefix of the locator.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertIn(claimed_url, facts["unanchored_literals_in_selection"])
        self.assertIn(claimed_url, facts["unanchored_literals_in_universe"])

    # An observed article grounds its exact channel path at a segment boundary.
    def test_literal_url_channel_path_is_anchored_by_observed_descendant(self) -> None:
        channel_url = "https://source-a.org/news/"
        article_url = "https://source-a.org/news/2026/official-announcement"
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.observations.append(
            ToolObservation(
                observation_id="obs-url",
                content="The official article describes the release.",
                locator=article_url,
            )
        )
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Identified the official channel.",
            output_text=f"Official channel: {channel_url}",
            step_validation=StepValidation(
                plan_step_id="step_1",
                actor_self_confidence=0.9,
                evidence_refs=("obs-url",),
                rationale_summary="The selected article belongs to the cited channel.",
            ),
        )

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertNotIn(channel_url, facts["unanchored_literals_in_selection"])
        self.assertNotIn(channel_url, facts["unanchored_literals_in_universe"])

    def test_trajectory_convergence_rules_facts(self) -> None:
        state = _build_test_task_state()
        # Add 3 consecutive tool failures
        for i in range(3):
            tr = ToolExecutionResult(
                request=ToolRequest(plan_step_id="step_1", tool_name=ToolName.WEB_SEARCH, reason="Search", query=f"query {i}"),
                success=False,
                summary=f"Search failed for query {i}",
                error_message="Search API failed",
                error_code=ToolErrorCode.TIMEOUT,
                observations=(),
            )
            state.tool_results_history.append(tr)

        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.PROPOSE_ACTION,
            summary="Proposing another tool action",
        )

        policy_input = build_policy_evaluation_input(task_state=state)
        signal = build_trajectory_signal(task_state=state, policy_input=policy_input, thresholds_version="v1")
        facts = signal["raw_facts"]

        self.assertEqual(facts["consecutive_tool_failures"], 3)
        self.assertEqual(facts["replan_count"], 0)

    def test_task_state_serialization_roundtrip_preserves_new_fields(self) -> None:
        burden = EvidenceBurden(min_independent_sources=2, require_retrieved_content=True, freshness_window_days=7)
        state = _build_test_task_state(produces_factual_claims=True, evidence_burden=burden)
        state.evidence_request_count = 2
        state.evidence_request_episode_count = 1
        state.evidence_request_episode_id = "step_1"

        normalized = normalize_task_state_serialization(state)
        self.assertEqual(normalized.evidence_request_count, 2)
        self.assertEqual(normalized.evidence_request_episode_count, 1)
        self.assertEqual(normalized.evidence_request_episode_id, "step_1")
        self.assertTrue(normalized.plan_steps[0].produces_factual_claims)
        self.assertEqual(normalized.plan_steps[0].evidence_burden.min_independent_sources, 2)

    # A prior step's exhausted episode cannot consume the next step's allowance.
    def test_evidence_signal_starts_new_episode_when_semantic_step_changes(self) -> None:
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(),
        )
        state.evidence_request_count = 7
        state.evidence_request_episode_count = 3
        state.evidence_request_episode_id = "step_1"
        state.plan_steps = [
            PlanStep(
                step_id="step_2",
                objective="Verify a different factual result.",
                rationale="The semantic cursor advanced.",
                expected_outcome="A new evidence selection.",
                status=PlanStepStatus.IN_PROGRESS,
                produces_factual_claims=True,
                evidence_burden=EvidenceBurden(),
            )
        ]
        state.current_plan_step_index = 0

        facts = build_evidence_signal(
            task_state=state,
            thresholds_version="v1",
        )["raw_facts"]

        self.assertEqual(facts["evidence_request_count"], 7)
        self.assertEqual(facts["evidence_request_episode_count"], 0)
        self.assertEqual(facts["evidence_request_episode_id"], "step_2")


class TaskLiteralAndInsufficiencyRecognitionTests(unittest.TestCase):
    # A date the task states in prose must anchor whichever rendering the actor writes.
    def test_task_prose_date_anchors_every_numeric_rendering(self) -> None:
        spec = TaskSpec(
            goal="Produce a briefing.",
            context="Data corrente indicata: 24 agosto 2026.",
            constraints=["Usare solo fonti pubblicate entro gli ultimi 30 giorni."],
            success_criteria=["Ogni fonte riporta la data."],
            sensitivity=SensitivityLevel.LOW,
            execution_intent=ExecutionIntent.PROPOSAL_ONLY,
        )
        index = _build_literal_corpus_index(corpus=_task_literal_corpus(spec))

        for rendering in ("2026-08-24", "24/08/2026", "2026/08/24", "24-08-2026"):
            with self.subTest(rendering=rendering):
                self.assertTrue(_literal_is_anchored(rendering, index))

    # A publication date belongs to the observations, never to the task corpus.
    def test_source_publication_date_is_not_task_anchored(self) -> None:
        spec = TaskSpec(
            goal="Produce a briefing.",
            context="Data corrente indicata: 24 agosto 2026.",
            constraints=[],
            success_criteria=[],
            sensitivity=SensitivityLevel.LOW,
            execution_intent=ExecutionIntent.PROPOSAL_ONLY,
        )
        index = _build_literal_corpus_index(corpus=_task_literal_corpus(spec))

        self.assertFalse(_literal_is_anchored("2026-07-15", index))
        self.assertFalse(_literal_is_anchored("987654", index))

    # The declaration is assertive and order-insensitive, never a restated task rule.
    def test_insufficiency_declaration_is_order_insensitive(self) -> None:
        declaring = (
            "Sono state individuate solo 2 fonti valide.",
            "Le fonti valide sono insufficienti e questo viene dichiarato esplicitamente.",
            "Le fonti valide risultano inferiori a tre.",
            "Only two valid sources were found within the window.",
            "The available sources are insufficient, as stated explicitly.",
        )
        for text in declaring:
            with self.subTest(text=text):
                self.assertTrue(_declares_insufficiency(text))

    def test_conditional_task_rule_is_not_a_declaration(self) -> None:
        not_declaring = (
            "Se le fonti valide sono meno di tre, va dichiarato esplicitamente.",
            "If fewer than three sources are available, declare it.",
            "Il briefing confronta tre fonti ufficiali con date verificate.",
        )
        for text in not_declaring:
            with self.subTest(text=text):
                self.assertFalse(_declares_insufficiency(text))


class SelectionScopedCoverageTests(unittest.TestCase):
    def _state_with_observations(self, *, minimum: int) -> TaskState:
        state = _build_test_task_state(
            produces_factual_claims=True,
            evidence_burden=EvidenceBurden(min_independent_sources=minimum),
        )
        state.observations.extend(
            ToolObservation(
                observation_id=f"obs_{index}",
                content=f"Retrieved body {index}",
                locator=f"https://source-{index}.example/article",
                retrieval_kind=RetrievalKind.FETCHED_DOCUMENT,
            )
            for index in range(1, 4)
        )
        return state

    def _complete_with(self, state: TaskState, refs: tuple[str, ...]) -> dict:
        state.actor_result = ActorResult(
            plan_step_id="step_1",
            result_type=ActorResultType.STEP_COMPLETED,
            summary="Completed the factual step.",
            output_text="A grounded statement.",
            step_validation=StepValidation(
                plan_step_id="step_1",
                rationale_summary="Selected the supporting evidence.",
                evidence_refs=refs,
                actor_self_confidence=0.9,
            ),
        )
        return build_evidence_signal(task_state=state, thresholds_version="v1")["raw_facts"]

    # The declared burden belongs to the claim: retrieving three sources but citing one
    # must not satisfy coverage.
    def test_coverage_counts_the_actor_selection_not_the_universe(self) -> None:
        state = self._state_with_observations(minimum=3)

        facts = self._complete_with(state, ("obs_1",))

        self.assertEqual(facts["distinct_independent_sources_count"], 1)
        self.assertEqual(facts["retrieved_independent_sources_count"], 3)
        self.assertFalse(facts["independent_sources_satisfied"])
        self.assertIn("insufficient_independent_sources", facts["deficit_codes"])

    def test_coverage_is_satisfied_when_the_selection_reaches_the_minimum(self) -> None:
        state = self._state_with_observations(minimum=3)

        facts = self._complete_with(state, ("obs_1", "obs_2", "obs_3"))

        self.assertEqual(facts["distinct_independent_sources_count"], 3)
        self.assertEqual(facts["retrieved_independent_sources_count"], 3)
        self.assertTrue(facts["independent_sources_satisfied"])
        self.assertNotIn("insufficient_independent_sources", facts["deficit_codes"])

    # A run that already holds enough origins must be told to widen its citations,
    # never to fetch more, otherwise the remediation loops on the wrong action.
    def test_remediation_asks_to_cite_already_retrieved_sources(self) -> None:
        findings = _build_evidence_feedback_findings(
            codes=("insufficient_independent_sources",),
            facts={
                "distinct_independent_sources_count": 1,
                "retrieved_independent_sources_count": 3,
                "min_independent_sources_required": 3,
            },
            observations=(),
        )

        summary = findings[0].summary
        self.assertIn("already retrieved 3 distinct source origins", summary)
        self.assertIn("Cite at least 2 more", summary)
        self.assertNotIn("new web_search", summary)

    def test_remediation_asks_to_retrieve_when_origins_are_missing(self) -> None:
        findings = _build_evidence_feedback_findings(
            codes=("insufficient_independent_sources",),
            facts={
                "distinct_independent_sources_count": 1,
                "retrieved_independent_sources_count": 1,
                "min_independent_sources_required": 3,
            },
            observations=(),
        )

        summary = findings[0].summary
        self.assertNotIn("already retrieved", summary)
        self.assertIn("new web_search", summary)


if __name__ == "__main__":
    unittest.main()
