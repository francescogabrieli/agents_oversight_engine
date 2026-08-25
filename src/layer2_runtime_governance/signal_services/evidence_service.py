from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlsplit

from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.domain import (
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    digest_json,
)
from layer2_runtime_governance.domain.step_recovery import resolve_evidence_episode_id
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationOperationPayload,
    DatabaseOperationPayload,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    ToolName,
    ToolObservation,
    canonical_web_locator,
)

_GENERAL_NUMBER_PATTERN = re.compile(
    r"(?<![\w-])(?:\d+\.\d+|\d{3,})(?![\w-])"
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
    _GENERAL_NUMBER_PATTERN,
]
_STRUCTURAL_NUMBER_PREFIX = re.compile(
    r"(?:articolo|capitolo|fonte|sezione|step)\s+$",
    re.IGNORECASE,
)
_LOCALIZED_CURRENCY = re.compile(
    r"^(?P<symbol>[\$€£¥])\s*(?P<whole>\d+)(?P<separator>[\.,])(?P<fraction>\d{1,2})$"
)

_ACTOR_DERIVED_TOOL_NAMES = frozenset(
    {
        ToolName.MCP_LOCAL_TEST_ECHO.value,
        ToolName.MCP_LOCAL_TEST_TEXT_STATS.value,
    }
)
_SELF_PRODUCED_LOCATOR_PREFIXES = (
    "actor-output://",
    "agent-output://",
    "draft://",
    "runtime-artifact://",
    "scratchpad://",
    "workspace://",
)


@dataclass(frozen=True)
class _FreshnessAssessment:
    # Retain every deterministic freshness outcome needed by OPA and the audit trace.
    evaluated: bool
    satisfied: bool
    window_days: int | None
    reference_time: str | None
    source_timestamps: dict[str, str | None]
    missing_observation_ids: tuple[str, ...]
    stale_observation_ids: tuple[str, ...]
    future_observation_ids: tuple[str, ...]


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


# Exclude document structure labels such as "Articolo 1.1" from factual literals.
def _is_structural_number(*, text: str, start: int) -> bool:
    prefix = text[max(0, start - 32):start]
    return _STRUCTURAL_NUMBER_PREFIX.search(prefix) is not None


def _extract_literal_tokens(text: str) -> list[str]:
    if not text:
        return []
    tokens: set[str] = set()
    for pat in TOKEN_PATTERNS:
        for match in pat.finditer(text):
            if pat is _GENERAL_NUMBER_PATTERN and _is_structural_number(
                text=text,
                start=match.start(),
            ):
                continue
            tok = match.group(0).strip()
            if tok:
                tokens.add(tok)
    return sorted(tokens)


# Treat decimal comma and decimal point as equivalent for localized currency rendering.
def _literal_comparison_key(token: str) -> str:
    currency = _LOCALIZED_CURRENCY.fullmatch(token)
    if currency is None:
        return token
    return (
        f"{currency.group('symbol')}"
        f"{currency.group('whole')}.{currency.group('fraction')}"
    )


# Include immutable observation provenance when checking literal claim anchoring.
def _literal_evidence_corpus(observations: list[ToolObservation]) -> str:
    parts: list[str] = []
    for item in observations:
        parts.append(item.content)
        if item.locator:
            parts.append(item.locator)
        source_timestamp = _source_timestamp_text(
            getattr(item, "source_timestamp", None)
        )
        if source_timestamp:
            parts.append(source_timestamp)
    return " ".join(parts)


# Admit prose punctuation only when its stripped URL is an exact observed locator.
def _unanchored_literal_tokens(
    *,
    target_tokens: list[str],
    observations: list[ToolObservation],
) -> list[str]:
    corpus = _literal_evidence_corpus(observations)
    locators = {
        canonical_web_locator(item.locator)
        for item in observations
        if item.locator
        and item.locator.casefold().startswith(("http://", "https://"))
    }
    corpus_urls = {
        canonical_web_locator(item)
        for item in _extract_literal_tokens(corpus)
        if item.casefold().startswith(("http://", "https://"))
    }
    parsed_observed_urls = tuple(
        urlsplit(locator) for locator in locators | corpus_urls
    )
    observed_origins = {
        f"{parsed.scheme}://{parsed.netloc}"
        for parsed in parsed_observed_urls
        if parsed.scheme and parsed.netloc
    }
    corpus_literal_keys = {
        _literal_comparison_key(item)
        for item in _extract_literal_tokens(corpus)
        if not item.casefold().startswith(("http://", "https://"))
    }
    unanchored: list[str] = []
    for token in target_tokens:
        if token.casefold().startswith(("http://", "https://")):
            # URLs use exact token identity; substring matches such as /doc
            # versus /document are not evidence anchoring.
            canonical_token = canonical_web_locator(token)
            if canonical_token in locators or canonical_token in corpus_urls:
                continue
            parsed_token = urlsplit(canonical_token)
            token_origin = f"{parsed_token.scheme}://{parsed_token.netloc}"
            # A cited origin is grounded by an observed page on that exact host;
            # non-root paths and meaningful queries still require exact identity.
            if (
                parsed_token.path in {"", "/"}
                and not parsed_token.query
                and token_origin in observed_origins
            ):
                continue
            token_path = parsed_token.path.rstrip("/")
            # A channel/index URL is grounded by an observed descendant only at
            # a complete path-segment boundary, never by an arbitrary prefix.
            if token_path and not parsed_token.query and any(
                observed.scheme == parsed_token.scheme
                and observed.netloc == parsed_token.netloc
                and (
                    observed.path.rstrip("/") == token_path
                    or observed.path.startswith(f"{token_path}/")
                )
                for observed in parsed_observed_urls
            ):
                continue
            # A final full stop or comma is commonly prose. Characters such as
            # !, ?, ; and : remain part of a valid URL and are never discarded.
            stripped = token.rstrip(".,")
            if stripped != token and canonical_web_locator(stripped) in locators:
                continue
        elif token in corpus or _literal_comparison_key(token) in corpus_literal_keys:
            continue
        unanchored.append(token)
    return unanchored


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


# Normalize only source-attributed, timezone-aware timestamps for a freshness decision.
def _normalize_source_timestamp(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


# Preserve an observed timestamp in audit facts even when it is unusable for freshness.
def _source_timestamp_text(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# Evaluate a declared freshness window against source metadata, never retrieval time.
def _evaluate_freshness(
    *,
    selected: list[ToolObservation],
    freshness_window_days: int | None,
    evaluated_at: datetime | None,
) -> _FreshnessAssessment:
    source_timestamps = {
        item.observation_id: _source_timestamp_text(
            getattr(item, "source_timestamp", None)
        )
        for item in selected
    }
    if freshness_window_days is None:
        return _FreshnessAssessment(
            evaluated=False,
            satisfied=True,
            window_days=None,
            reference_time=None,
            source_timestamps=source_timestamps,
            missing_observation_ids=(),
            stale_observation_ids=(),
            future_observation_ids=(),
        )

    reference_time = evaluated_at or datetime.now(timezone.utc)
    normalized_reference_time = _normalize_source_timestamp(reference_time)
    if normalized_reference_time is None:
        raise ValueError("Evidence freshness evaluation requires an aware UTC reference time.")

    cutoff = normalized_reference_time - timedelta(days=freshness_window_days)
    missing_observation_ids: list[str] = []
    stale_observation_ids: list[str] = []
    future_observation_ids: list[str] = []
    for item in selected:
        source_timestamp = _normalize_source_timestamp(
            getattr(item, "source_timestamp", None)
        )
        if source_timestamp is None:
            missing_observation_ids.append(item.observation_id)
        elif source_timestamp > normalized_reference_time:
            future_observation_ids.append(item.observation_id)
        elif source_timestamp < cutoff:
            stale_observation_ids.append(item.observation_id)

    return _FreshnessAssessment(
        evaluated=True,
        satisfied=bool(selected)
        and not missing_observation_ids
        and not stale_observation_ids
        and not future_observation_ids,
        window_days=freshness_window_days,
        reference_time=normalized_reference_time.isoformat(),
        source_timestamps=source_timestamps,
        missing_observation_ids=tuple(missing_observation_ids),
        stale_observation_ids=tuple(stale_observation_ids),
        future_observation_ids=tuple(future_observation_ids),
    )


# Reject typed actor-produced artifacts and echo-style transformations as evidence sources.
def _detect_circular_evidence(
    *,
    selected: list[ToolObservation],
    observation_lineage: dict[str, Any],
) -> dict[str, str]:
    circular_reasons: dict[str, str] = {}
    for item in selected:
        locator = (item.locator or "").lower()
        if any(locator.startswith(prefix) for prefix in _SELF_PRODUCED_LOCATOR_PREFIXES):
            circular_reasons[item.observation_id] = "self_produced_locator"
            continue
        lineage = observation_lineage.get(item.observation_id)
        if (
            lineage is not None
            and lineage.tool_name in _ACTOR_DERIVED_TOOL_NAMES
        ):
            circular_reasons[item.observation_id] = "actor_derived_tool_output"
    return circular_reasons


# Build deterministic evidence facts across the 5 structural families.
def build_evidence_signal(
    *,
    task_state: TaskState,
    thresholds_version: str,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    result = task_state.actor_result
    validation = result.step_validation if result is not None else None
    selected_refs = validation.evidence_refs if validation is not None else ()
    evidence_by_id = {
        item.observation_id: item for item in task_state.observations
    }
    selected = [evidence_by_id[item] for item in selected_refs if item in evidence_by_id]
    current_step = task_state.current_plan_step
    produces_factual_claims = bool(
        current_step and getattr(current_step, "produces_factual_claims", False)
    )
    completion_attempt = bool(
        result and result.result_type == ActorResultType.STEP_COMPLETED
    )
    factual_completion_attempt = completion_attempt and produces_factual_claims
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
    candidate_binding_required = completion_attempt
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

    # Famiglia 2: Copertura dell'onere probatorio dichiarato (Fail-Closed)
    evidence_burden = (
        getattr(current_step, "evidence_burden", None) if current_step else None
    )
    factual_step_missing_evidence_burden = (
        produces_factual_claims and evidence_burden is None
    )

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
        independent_sources_satisfied = (
            distinct_independent_sources_count >= min_sources
        )
        if evidence_burden.require_retrieved_content:
            retrieved_content_satisfied = any(
                not (
                    item.content.startswith("Search result title:")
                    or (item.locator and item.locator.startswith("search:"))
                )
                for item in selected
            ) if selected else False
        else:
            retrieved_content_satisfied = True
        freshness = _evaluate_freshness(
            selected=selected,
            freshness_window_days=evidence_burden.freshness_window_days,
            evaluated_at=evaluated_at,
        )
        freshness_satisfied = freshness.satisfied
        burden_satisfied = (
            independent_sources_satisfied
            and retrieved_content_satisfied
            and freshness_satisfied
        )
    elif produces_factual_claims:
        # Legacy or malformed state must not silently downgrade the factual obligation.
        coverage_evaluated = False
        min_sources = 0
        independent_sources_satisfied = False
        retrieved_content_satisfied = False
        freshness = _FreshnessAssessment(
            evaluated=False,
            satisfied=False,
            window_days=None,
            reference_time=None,
            source_timestamps={},
            missing_observation_ids=(),
            stale_observation_ids=(),
            future_observation_ids=(),
        )
        freshness_satisfied = freshness.satisfied
        burden_satisfied = False
    else:
        # Non-factual steps have no planner-declared coverage requirement.
        coverage_evaluated = False
        min_sources = 0
        independent_sources_satisfied = True
        retrieved_content_satisfied = True
        freshness = _FreshnessAssessment(
            evaluated=False,
            satisfied=True,
            window_days=None,
            reference_time=None,
            source_timestamps={},
            missing_observation_ids=(),
            stale_observation_ids=(),
            future_observation_ids=(),
        )
        freshness_satisfied = freshness.satisfied
        burden_satisfied = True

    # Famiglia 3: Qualità strutturale delle osservazioni
    selected_evidence_all_title_only = bool(selected) and search_title_only_ref_count == len(selected)

    # Famiglia 4: Non-circolarità
    circular_evidence_reasons = _detect_circular_evidence(
        selected=selected,
        observation_lineage=observation_lineage,
    )
    circular_reference_detected = bool(circular_evidence_reasons)

    # Famiglia 5: Ancoraggio letterale dei claim (solo token estraibili)
    target_text = _get_target_text(task_state)
    target_tokens = _extract_literal_tokens(target_text)
    unanchored_literals_in_selection = _unanchored_literal_tokens(
        target_tokens=target_tokens,
        observations=selected,
    )
    unanchored_literals_in_universe = _unanchored_literal_tokens(
        target_tokens=target_tokens,
        observations=task_state.observations,
    )

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
    if factual_step_missing_evidence_burden:
        deficit_codes.append("missing_evidence_burden")
    if coverage_evaluated and not independent_sources_satisfied:
        deficit_codes.append("insufficient_independent_sources")
    if coverage_evaluated and not retrieved_content_satisfied:
        deficit_codes.append("retrieved_content_missing")
    if freshness.evaluated and freshness.missing_observation_ids:
        deficit_codes.append("freshness_metadata_missing")
    if freshness.evaluated and freshness.stale_observation_ids:
        deficit_codes.append("stale_evidence")
    if freshness.evaluated and freshness.future_observation_ids:
        deficit_codes.append("future_source_timestamp")
    if factual_completion_attempt and selected_evidence_all_title_only:
        deficit_codes.append("title_only_evidence_fragility")
    if factual_completion_attempt and circular_reference_detected:
        deficit_codes.append("circular_evidence_reference")
    if factual_completion_attempt and unanchored_literals_in_selection:
        deficit_codes.append("unanchored_literals_in_selection")
    if factual_completion_attempt and unanchored_literals_in_universe:
        deficit_codes.append("unanchored_literals_in_universe")

    current_evidence_episode_id = resolve_evidence_episode_id(task_state)
    stored_evidence_episode_id = getattr(
        task_state,
        "evidence_request_episode_id",
        None,
    )
    evidence_request_episode_count = (
        getattr(
            task_state,
            "evidence_request_episode_count",
            getattr(task_state, "evidence_request_count", 0),
        )
        if stored_evidence_episode_id == current_evidence_episode_id
        else 0
    )
    raw_facts = {
        "observation_count": len(task_state.observations),
        "observation_ids_unique": observation_ids_unique,
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_ref_ratio": selected_evidence_ref_ratio,
        "selected_evidence_refs_valid": selected_evidence_refs_valid,
        "selected_evidence_refs_unique": selected_evidence_refs_unique,
        "selected_observations_have_content": selected_observations_have_content,
        "selected_observations_provenance_resolvable": provenance_resolvable,
        "selected_observations_belong_to_known_steps": known_steps,
        "selected_search_title_only_count": search_title_only_ref_count,
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
        "completion_attempt": completion_attempt,
        "factual_completion_attempt": factual_completion_attempt,
        "external_operation_candidate": bool(task_state.external_operation_proposal and task_state.external_operation_proposal.execution_candidate),
        "evidence_request_count": getattr(task_state, "evidence_request_count", 0),
        "evidence_request_episode_count": evidence_request_episode_count,
        "evidence_request_episode_id": current_evidence_episode_id,
        # Famiglia 2 facts
        "produces_factual_claims": produces_factual_claims,
        "factual_step_missing_evidence_burden": factual_step_missing_evidence_burden,
        "coverage_evaluated": coverage_evaluated,
        "distinct_independent_sources_count": distinct_independent_sources_count,
        "min_independent_sources_required": min_sources,
        "independent_sources_satisfied": independent_sources_satisfied,
        "retrieved_content_satisfied": retrieved_content_satisfied,
        "freshness_evaluated": freshness.evaluated,
        "freshness_window_days": freshness.window_days,
        "freshness_reference_time": freshness.reference_time,
        "selected_source_timestamps": freshness.source_timestamps,
        "freshness_missing_observation_ids": list(freshness.missing_observation_ids),
        "stale_evidence_observation_ids": list(freshness.stale_observation_ids),
        "future_source_timestamp_observation_ids": list(
            freshness.future_observation_ids
        ),
        "freshness_satisfied": freshness_satisfied,
        "burden_satisfied": burden_satisfied,
        # Famiglia 4 facts
        "circular_reference_detected": circular_reference_detected,
        "circular_evidence_observation_ids": sorted(circular_evidence_reasons),
        "circular_evidence_reasons": circular_evidence_reasons,
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
        "mode": "active",
        "decision_influence": True,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": {},
        "reason_facts": deficit_codes,
        "summary": "Concrete structural evidence facts collected across 5 families.",
        "score_formula_version": "evidence_structural_v2",
        "thresholds_version": thresholds_version,
    }


__all__ = ["build_evidence_signal"]
