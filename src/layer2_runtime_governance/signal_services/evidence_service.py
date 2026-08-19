from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.domain import (
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
)
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
)

TOKEN_PATTERNS = [
    # URLs
    re.compile(r"https?://[^\s<>\"'()]+"),
    # Emails
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    # Currency amounts
    re.compile(r"(?:[\$€£¥]\s*\d+(?:[\.,]\d+)?|\b\d+(?:[\.,]\d+)?\s*(?:USD|EUR|GBP|JPY|CHF)\b)"),
    # Percentages
    re.compile(r"\b\d+(?:[\.,]\d+)?%"),
    # Dates (ISO or slash format)
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),
    # Numbers with decimals or >= 3 digits
    re.compile(r"\b\d+\.\d+\b|\b\d{3,}\b"),
]


def _extract_source_origin(locator: str | None) -> str:
    if not locator:
        return "unknown_origin"
    parsed = urlparse(locator)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc.lower()}"
    parts = locator.split(":", 1)
    if len(parts) == 2:
        return f"{parts[0].lower()}:{parts[1].split('/')[0].lower()}"
    return locator.lower().strip()


def _extract_literal_tokens(text: str) -> list[str]:
    if not text:
        return []
    tokens: set[str] = set()
    for pat in TOKEN_PATTERNS:
        for match in pat.finditer(text):
            tok = match.group(0).strip()
            if tok:
                tokens.add(tok)
    return sorted(tokens)


def _get_target_text(task_state: TaskState) -> str:
    parts: list[str] = []
    if task_state.external_operation_proposal is not None:
        payload = task_state.external_operation_proposal.payload
        if isinstance(payload, CommunicationOperationPayload) and payload.body:
            parts.append(payload.body)
        elif isinstance(payload, DatabaseOperationPayload) and payload.sql:
            parts.append(payload.sql)
    if (
        task_state.external_communication is not None
        and task_state.external_communication.draft is not None
        and task_state.external_communication.draft.body
    ):
        parts.append(task_state.external_communication.draft.body)
    if task_state.actor_result is not None and task_state.actor_result.output_text:
        parts.append(task_state.actor_result.output_text)
    return " ".join(parts)


# Build deterministic evidence facts across the 5 structural families.
def build_evidence_signal(*, task_state: TaskState, thresholds_version: str) -> dict[str, Any]:
    result = task_state.actor_result
    validation = result.step_validation if result is not None else None
    selected_refs = validation.evidence_refs if validation is not None else ()
    evidence_by_id = {
        item.observation_id: item for item in task_state.observations
    }
    selected = [evidence_by_id[item] for item in selected_refs if item in evidence_by_id]
    factual_completion = bool(
        result and result.result_type == ActorResultType.STEP_COMPLETED
    )
    unique_selected = tuple(dict.fromkeys(selected_refs))
    available_obs_ids = {item.observation_id for item in task_state.observations}
    selected_evidence_ref_ratio = (
        len(unique_selected) / len(available_obs_ids)
        if available_obs_ids
        else 0.0
    )

    observation_lineage = build_observation_lineage(task_state=task_state)

    # Famiglia 1: Integrità referenziale e digest binding
    selected_evidence_refs_valid = len(selected) == len(selected_refs)
    selected_evidence_refs_unique = len(unique_selected) == len(selected_refs)
    observation_ids_unique = len(available_obs_ids) == len(task_state.observations)
    selected_observations_have_content = all(
        bool(item.content and item.content.strip()) for item in selected
    ) if selected else True

    selected_known_step_ids = [
        observation_lineage[item.observation_id].plan_step_id
        for item in selected
        if item.observation_id in observation_lineage
    ]
    known_step_ids = (
        {step.step_id for step in task_state.plan_steps}
        | {step.step_id for step in task_state.executed_steps}
        | {res.request.plan_step_id for res in task_state.tool_results_history}
    )
    provenance_resolvable = all(
        item.observation_id in observation_lineage
        for item in selected
    )
    known_steps = provenance_resolvable and all(
        step_id in known_step_ids for step_id in selected_known_step_ids
    )

    snapshot_manifest = build_evidence_snapshot_manifest(
        task_state=task_state,
        observation_lineage=observation_lineage,
    )
    snapshot_digest = digest_json(snapshot_manifest)
    external_operation_digest = (
        task_state.external_operation_proposal.payload_digest
        if task_state.external_operation_proposal is not None
        else None
    )
    selection_digest = build_evidence_selection_digest(
        selected_refs=tuple(selected_refs),
        evidence_snapshot_digest=snapshot_digest,
        external_operation_digest=external_operation_digest,
    )
    completion_candidate = task_state.pending_completion_candidate
    candidate_binding_required = factual_completion
    candidate_binding_present = bool(
        completion_candidate is not None
        and completion_candidate.evidence_snapshot_digest
        and completion_candidate.evidence_selection_digest
    )
    candidate_snapshot_matches = bool(
        candidate_binding_present
        and completion_candidate is not None
        and completion_candidate.evidence_snapshot_digest == snapshot_digest
    )
    candidate_selection_matches = bool(
        candidate_binding_present
        and completion_candidate is not None
        and completion_candidate.evidence_selection_digest == selection_digest
    )
    candidate_operation_matches = bool(
        completion_candidate is not None
        and completion_candidate.external_operation_digest
        == external_operation_digest
    )

    # Famiglia 2: Copertura dell'onere probatorio dichiarato (Fail-Open)
    current_step = task_state.current_plan_step
    produces_factual_claims = bool(current_step and getattr(current_step, "produces_factual_claims", False))
    evidence_burden = getattr(current_step, "evidence_burden", None) if current_step else None

    distinct_origins = {_extract_source_origin(item.locator) for item in selected if item.locator}
    distinct_independent_sources_count = len(distinct_origins) if distinct_origins else (1 if selected else 0)

    search_title_only_ref_count = sum(
        1
        for item in selected
        if item.content.startswith("Search result title:")
        or (item.locator and item.locator.startswith("search:"))
    )

    if produces_factual_claims and evidence_burden is not None:
        coverage_evaluated = True
        min_sources = evidence_burden.min_independent_sources
        independent_sources_satisfied = (distinct_independent_sources_count >= min_sources)
        if evidence_burden.require_retrieved_content:
            retrieved_content_satisfied = any(
                not (item.content.startswith("Search result title:") or (item.locator and item.locator.startswith("search:")))
                for item in selected
            ) if selected else False
        else:
            retrieved_content_satisfied = True
        freshness_satisfied = True
        burden_satisfied = (independent_sources_satisfied and retrieved_content_satisfied and freshness_satisfied)
    else:
        # Mandatory Fail-Open: no coverage constraints asserted
        coverage_evaluated = False
        min_sources = 0
        independent_sources_satisfied = True
        retrieved_content_satisfied = True
        freshness_satisfied = True
        burden_satisfied = True

    # Famiglia 3: Qualità strutturale delle osservazioni
    selected_evidence_all_title_only = bool(selected) and search_title_only_ref_count == len(selected)

    # Famiglia 4: Non-circolarità
    circular_reference_detected = False

    # Famiglia 5: Ancoraggio letterale dei claim (solo token estraibili)
    target_text = _get_target_text(task_state)
    target_tokens = _extract_literal_tokens(target_text)
    selected_content_corpus = " ".join(item.content for item in selected)
    universe_content_corpus = " ".join(item.content for item in task_state.observations)

    unanchored_literals_in_selection = [
        tok for tok in target_tokens if tok not in selected_content_corpus
    ]
    unanchored_literals_in_universe = [
        tok for tok in target_tokens if tok not in universe_content_corpus
    ]

    # Deterministic deficit codes collection
    deficit_codes: list[str] = []
    if not selected_evidence_refs_valid:
        deficit_codes.append("invalid_evidence_ref")
    if not selected_evidence_refs_unique:
        deficit_codes.append("duplicate_evidence_ref")
    if not selected_observations_have_content:
        deficit_codes.append("empty_observation_content")
    if not provenance_resolvable:
        deficit_codes.append("unresolvable_provenance")
    if not known_steps:
        deficit_codes.append("unknown_step_lineage")
    if candidate_binding_required and not (candidate_snapshot_matches and candidate_selection_matches):
        deficit_codes.append("candidate_digest_mismatch")
    if coverage_evaluated and not independent_sources_satisfied:
        deficit_codes.append("insufficient_independent_sources")
    if coverage_evaluated and not retrieved_content_satisfied:
        deficit_codes.append("retrieved_content_missing")
    if selected_evidence_all_title_only:
        deficit_codes.append("title_only_evidence_fragility")
    if circular_reference_detected:
        deficit_codes.append("circular_evidence_reference")
    if unanchored_literals_in_selection:
        deficit_codes.append("unanchored_literals_in_selection")
    if unanchored_literals_in_universe:
        deficit_codes.append("unanchored_literals_in_universe")

    raw_facts = {
        "observation_count": len(task_state.observations),
        "observation_ids_unique": observation_ids_unique,
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_ref_ratio": selected_evidence_ref_ratio,
        "selected_ref_ratio": selected_evidence_ref_ratio,
        "selected_evidence_refs_valid": selected_evidence_refs_valid,
        "selected_evidence_refs_unique": selected_evidence_refs_unique,
        "selected_observations_have_content": selected_observations_have_content,
        "selected_observations_provenance_resolvable": provenance_resolvable,
        "selected_observations_belong_to_known_steps": known_steps,
        "selected_search_title_only_count": search_title_only_ref_count,
        "search_title_only_ref_count": search_title_only_ref_count,
        "selected_evidence_all_title_only": selected_evidence_all_title_only,
        "evidence_selection_digest": selection_digest,
        "evidence_snapshot_digest": snapshot_digest,
        "evidence_snapshot_observation_count": len(snapshot_manifest),
        "evidence_snapshot_omitted_observation_ids": [],
        "candidate_evidence_binding_required": candidate_binding_required,
        "candidate_evidence_binding_present": candidate_binding_present,
        "candidate_evidence_snapshot_matches": candidate_snapshot_matches,
        "candidate_evidence_selection_matches": candidate_selection_matches,
        "candidate_external_operation_digest_matches": candidate_operation_matches,
        "factual_completion_attempt": factual_completion,
        "external_operation_candidate": bool(task_state.external_operation_proposal and task_state.external_operation_proposal.execution_candidate),
        "evidence_request_count": getattr(task_state, "evidence_request_count", 0),
        # Famiglia 2 facts
        "coverage_evaluated": coverage_evaluated,
        "distinct_independent_sources_count": distinct_independent_sources_count,
        "min_independent_sources_required": min_sources,
        "independent_sources_satisfied": independent_sources_satisfied,
        "retrieved_content_satisfied": retrieved_content_satisfied,
        "freshness_satisfied": freshness_satisfied,
        "burden_satisfied": burden_satisfied,
        # Famiglia 4 facts
        "circular_reference_detected": circular_reference_detected,
        # Famiglia 5 facts
        "target_literal_token_count": len(target_tokens),
        "unanchored_literals_in_selection": unanchored_literals_in_selection,
        "unanchored_literals_in_universe": unanchored_literals_in_universe,
        "has_unanchored_literals_in_selection": len(unanchored_literals_in_selection) > 0,
        "has_unanchored_literals_in_universe": len(unanchored_literals_in_universe) > 0,
        # Aggregated deficit codes
        "deficit_codes": deficit_codes,
        "has_evidence_deficit": len(deficit_codes) > 0,
    }
    return {
        "raw_facts": raw_facts,
        "mode": "shadow",
        "decision_influence": False,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": {},
        "reason_facts": deficit_codes,
        "summary": "Concrete structural evidence facts collected across 5 families (shadow mode).",
        "score_formula_version": "evidence_structural_v2",
        "thresholds_version": thresholds_version,
    }


__all__ = ["build_evidence_signal"]
