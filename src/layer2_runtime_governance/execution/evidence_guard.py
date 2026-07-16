from __future__ import annotations

import re

from ..actor_contract import ActorTask
from ..domain import ScratchpadEntryType, StepValidationDecision
from ..tool_contract import EvidenceSourceKind, EvidenceSupportLevel, ObservedEvidence, ToolName
from .response_parser import coerce_provider_confidence as _coerce_provider_confidence

# Add deterministic runtime hints so the actor stays inside the semantic boundary of the current step.
def build_actor_execution_hints(*, actor_task: ActorTask) -> dict[str, object]:
    current_step_id = actor_task.current_plan_step.step_id
    current_step_evidence = [
        evidence for evidence in actor_task.observed_evidence if evidence.plan_step_id == current_step_id
    ]
    direct_support_evidence = [
        evidence for evidence in actor_task.observed_evidence if _is_direct_fact_support_evidence(evidence)
    ]
    available_support_level = max_available_evidence_support_level(actor_task=actor_task)
    required_support_level = required_evidence_support_level(
        actor_task=actor_task,
        output_text=None,
    )
    successful_current_step_tools = [
        entry
        for entry in actor_task.scratchpad
        if entry.plan_step_id == current_step_id
        and entry.entry_type == ScratchpadEntryType.OBSERVATION
        and "successfully" in entry.summary.lower()
    ]
    prior_failed_same_step_tools = [
        entry.summary
        for entry in actor_task.scratchpad
        if entry.plan_step_id == current_step_id
        and entry.entry_type == ScratchpadEntryType.OBSERVATION
        and (
            "empty" in entry.summary.lower()
            or "failed" in entry.summary.lower()
            or "exceeded the configured byte limit" in entry.summary.lower()
            or "duplicate tool request blocked" in entry.summary.lower()
        )
    ]
    has_primary_legal_text_excerpt = any(
        _looks_like_primary_legal_text_source(title=evidence.title, locator=evidence.locator)
        for evidence in direct_support_evidence
    )
    has_procedural_implementation_source = any(
        (
            "legislation_en" in evidence.locator.lower()
            or "procedural implementing regulation" in evidence.excerpt.lower()
            or "procedural implementing regulation" in evidence.title.lower()
        )
        for evidence in direct_support_evidence
    )
    high_level_summary_mode = (
        required_support_level == EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
        and available_support_level == EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
    )

    return {
        "current_step_only": True,
        "current_step_evidence_count": len(current_step_evidence),
        "direct_fact_support_evidence_count": len(direct_support_evidence),
        "current_step_successful_observation_count": len(successful_current_step_tools),
        "current_step_failed_observation_summaries": prior_failed_same_step_tools[-3:],
        "prefer_completion_with_existing_evidence": bool(current_step_evidence),
        "replan_replacement_step_active": current_step_id.startswith("replan_"),
        "do_not_collect_future_step_evidence": True,
        "source_discovery_is_not_direct_fact_support": True,
        "web_search_available": True,
        "fetch_url_text_available": True,
        "available_evidence_support_level": available_support_level.value,
        "required_evidence_support_level": required_support_level.value,
        "max_supported_claim_precision": actor_task.max_supported_claim_precision.value,
        "step_kind": (
            actor_task.step_kind.value if actor_task.step_kind is not None else None
        ),
        "high_level_summary_mode": high_level_summary_mode,
        "avoid_granular_obligation_examples_unless_directly_observed": high_level_summary_mode,
        "safe_high_level_obligation_phrase": "obligations and prohibitions for gatekeepers",
        "granular_obligation_examples_that_need_direct_support": [
            "self-preferencing",
            "interoperability",
            "data portability",
            "ranking",
            "non-discriminatory access",
            "transparent terms",
        ],
        "current_or_prior_steps_have_primary_legal_text_excerpt": has_primary_legal_text_excerpt,
        "current_or_prior_steps_have_procedural_implementation_source": (
            has_procedural_implementation_source
        ),
        "avoid_exact_dates_numbers_without_primary_excerpt": (
            required_support_level == EvidenceSupportLevel.PRIMARY_LEGAL
            and not has_primary_legal_text_excerpt
        ),
    }


# Treat fetched excerpts as direct factual support; source-discovery results stay weaker evidence.
def _is_direct_fact_support_evidence(evidence: ObservedEvidence) -> bool:
    if evidence.source_kind == EvidenceSourceKind.INTAKE_PROVIDED:
        return bool(evidence.excerpt.strip())
    return (
        evidence.source_tool == ToolName.FETCH_URL_TEXT
        and evidence.support_level != EvidenceSupportLevel.SOURCE_DISCOVERY
        and bool(evidence.excerpt.strip())
    )


# Order evidence levels from weakest to strongest for deterministic comparisons.
def _evidence_support_level_rank(level: EvidenceSupportLevel) -> int:
    ordering = {
        EvidenceSupportLevel.LOCAL_ONLY: 0,
        EvidenceSupportLevel.SOURCE_DISCOVERY: 1,
        EvidenceSupportLevel.HIGH_LEVEL_SUMMARY: 2,
        EvidenceSupportLevel.DETAILED_EXTRACTED: 3,
        EvidenceSupportLevel.PRIMARY_LEGAL: 4,
    }
    return ordering[level]


# Return the strongest evidence support level available to the actor.
def max_available_evidence_support_level(*, actor_task: ActorTask) -> EvidenceSupportLevel:
    available_levels = [item.support_level for item in actor_task.observed_evidence]
    if not available_levels:
        return EvidenceSupportLevel.LOCAL_ONLY
    return max(available_levels, key=_evidence_support_level_rank)


# Compute the minimum evidence level needed by the current step and optional output draft.
def required_evidence_support_level(
    *,
    actor_task: ActorTask,
    output_text: str | None,
) -> EvidenceSupportLevel:
    return actor_task.required_evidence_support_level


# Estimate how specific the current output draft is without changing the typed step requirement.
def output_claim_support_level(
    *,
    output_text: str | None,
) -> EvidenceSupportLevel:
    if not output_text or not output_text.strip():
        return EvidenceSupportLevel.LOCAL_ONLY
    return required_evidence_support_level_from_output_text(text=output_text)


# Infer the minimum support level from the semantic scope of the current step.
def required_evidence_support_level_from_step(*, actor_task: ActorTask) -> EvidenceSupportLevel:
    if actor_task.current_plan_step.evidence_requirement is not None:
        return actor_task.current_plan_step.evidence_requirement

    if actor_task.current_plan_step.claim_precision_ceiling is not None:
        return actor_task.current_plan_step.claim_precision_ceiling

    if actor_task.current_plan_step.step_kind is not None:
        return required_evidence_support_level_from_step_kind(
            step_kind=actor_task.current_plan_step.step_kind,
        )

    scope_text = " ".join(
        part.strip()
        for part in (
            actor_task.current_plan_step.objective,
            actor_task.current_plan_step.expected_outcome,
            actor_task.current_plan_step.rationale,
        )
        if part and part.strip()
    ).lower()

    if _step_is_source_discovery_only(scope_text):
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if _step_allows_conservative_high_level_summary(scope_text):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if any(
        marker in scope_text
        for marker in (
            "deadline",
            "deadlines",
            "threshold",
            "thresholds",
            "turnover",
            "number of users",
            "article ",
            "full legal text",
        )
    ):
        return EvidenceSupportLevel.PRIMARY_LEGAL

    if any(
        marker in scope_text
        for marker in (
            "official source",
            "official sources",
            "official pages",
            "official website",
            "official portal",
            "legislation page",
            "portal",
            "short summary",
            "summary",
            "overview",
            "high-level",
            "concise",
            "gatekeeper",
            "gatekeepers",
            "core platform services",
            "do's and don'ts",
            "what the dma is",
            "objectives",
            "legal instrument",
            "citation of the legal instrument",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    if any(
        marker in scope_text
        for marker in (
            "specific obligation",
            "specific obligations",
            "obligations-by-number",
            "obligation-by-obligation",
            "specific prohibition",
            "specific prohibitions",
            "specific requirements",
            "clause text",
            "citation-ready mapping",
            "claim mapping",
            "criteria",
            "enforcement mechanism",
            "what it requires",
            "who it applies to",
            "regulatory mechanisms",
            "main obligations",
            "entry into force",
            "applicability details",
            "timeline",
        )
    ):
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in scope_text
        for marker in ("purpose", "scope", "brief summary")
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    return EvidenceSupportLevel.LOCAL_ONLY


# Map typed plan-step kinds to conservative evidence requirements when explicit metadata is absent.
def required_evidence_support_level_from_step_kind(*, step_kind) -> EvidenceSupportLevel:
    if step_kind.value == "local_transformation":
        return EvidenceSupportLevel.LOCAL_ONLY
    if step_kind.value == "source_discovery":
        return EvidenceSupportLevel.SOURCE_DISCOVERY
    if step_kind.value == "high_level_summary":
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
    if step_kind.value == "detailed_extraction":
        return EvidenceSupportLevel.DETAILED_EXTRACTED
    if step_kind.value == "compliance_or_legal":
        return EvidenceSupportLevel.PRIMARY_LEGAL
    if step_kind.value == "final_synthesis":
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY
    return EvidenceSupportLevel.LOCAL_ONLY


# Infer the minimum support level from the draft output itself.
def required_evidence_support_level_from_output_text(*, text: str) -> EvidenceSupportLevel:
    lowered_text = _normalize_output_text_for_precision_checks(text).lower()

    if _output_text_is_source_shortlist_only(text.lower()):
        return EvidenceSupportLevel.SOURCE_DISCOVERY

    if _output_text_is_conservative_high_level_summary(lowered_text):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    precision_markers = _extract_precision_claim_markers(text=text)
    if precision_markers:
        if any(
            marker in lowered_text
            for marker in (
                "threshold",
                "thresholds",
                "turnover",
                "number of users",
                "specific size",
                "article ",
                "deadline",
                "deadlines",
            )
        ):
            return EvidenceSupportLevel.PRIMARY_LEGAL

        if any(
            marker in lowered_text
            for marker in (
                "entered into force",
                "comes into force",
                "coming into force",
                "effective date",
                "effective from",
                "applies from",
                "applies on",
                "entry into force",
                "adopted on",
                "published on",
                "timeline",
                "implementation date",
                "applicability details",
            )
        ):
            return EvidenceSupportLevel.DETAILED_EXTRACTED

        if any(
            marker in precision_markers
            for marker in (
                "percentages",
                "large numeric thresholds",
                "monetary thresholds",
            )
        ):
            return EvidenceSupportLevel.PRIMARY_LEGAL

        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_text
        for marker in (
            "threshold",
            "thresholds",
            "turnover",
            "number of users",
            "specific size",
            "article ",
        )
    ):
        return EvidenceSupportLevel.PRIMARY_LEGAL

    if any(
        marker in lowered_text
        for marker in (
            "specific obligation",
            "specific obligations",
            "prohibit",
            "prohibiting",
            "must",
            "shall",
            "cannot",
            "criteria",
            "interoperability",
            "data portability",
            "ranking",
            "self-preferencing",
            "non-discriminatory access",
        )
    ):
        return EvidenceSupportLevel.DETAILED_EXTRACTED

    if any(
        marker in lowered_text
        for marker in (
            "summary",
            "overview",
            "aims to",
            "applies to",
            "official sources",
        )
    ):
        return EvidenceSupportLevel.HIGH_LEVEL_SUMMARY

    return EvidenceSupportLevel.LOCAL_ONLY


# Treat explicitly conservative summary steps as high-level even if they mention broad obligations.
def _step_allows_conservative_high_level_summary(scope_text: str) -> bool:
    summary_markers = (
        "short summary",
        "brief summary",
        "few sentences",
        "high level",
        "high-level",
        "purpose/scope-level",
        "conservative",
        "broad categories",
        "obligation themes",
        "does not require",
        "do not require",
    )
    return any(marker in scope_text for marker in summary_markers)


# Keep compact policy summaries from being treated like clause-level extraction tasks.
def _output_text_is_conservative_high_level_summary(text: str) -> bool:
    if _extract_precision_claim_markers(text=text):
        return False

    high_level_markers = (
        "brief summary",
        "short summary",
        "overview",
        "purpose",
        "aims to",
        "intended to",
        "in general",
        "at a high level",
        "gatekeeper",
        "gatekeepers",
        "core platform services",
        "official sources",
    )
    if not any(marker in text for marker in high_level_markers):
        return False

    granular_markers = (
        "article ",
        "threshold",
        "thresholds",
        "turnover",
        "number of users",
        "specific size",
        "must ",
        "shall ",
        "cannot ",
        "non-discriminatory access",
        "self-preferencing",
        "data portability",
        "interoperability",
        "ranking",
    )
    return not any(marker in text for marker in granular_markers)


# Detect drafts that only lock in source identities/URLs without adding substantive claims.
def _output_text_is_source_shortlist_only(text: str) -> bool:
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    if any(
        marker in text
        for marker in (
            "aims to",
            "applies to",
            "purpose",
            "scope",
            "obligation",
            "obligations",
            "requires",
            "requirement",
            "criteria",
            "timeline",
            "entry into force",
        )
    ):
        return False

    source_identity_markers = (
        "official sources",
        "official eu sources",
        "shortlist of official",
        "source shortlist",
        "dma portal",
        "summary page",
        "legislation page",
        "eur-lex",
        "european commission",
    )
    if not any(marker in text for marker in source_identity_markers):
        return False

    return any("http://" in line or "https://" in line for line in lines)


# Distinguish pure source-list tasks from factual summarization tasks.
def _step_is_source_discovery_only(scope_text: str) -> bool:
    discovery_markers = ("identify", "find", "source", "sources", "links", "references", "citations")
    summary_markers = (
        "summary",
        "summar",
        "purpose",
        "scope",
        "obligation",
        "requirement",
        "criteria",
        "timeline",
        "what it requires",
    )
    return any(marker in scope_text for marker in discovery_markers) and not any(
        marker in scope_text for marker in summary_markers
    )


# Identify evidence that looks like a primary legal text rather than a secondary overview page.
def _looks_like_primary_legal_text_source(*, title: str, locator: str) -> bool:
    lowered_title = title.lower()
    lowered_locator = locator.lower()
    return "eur-lex.europa.eu" in lowered_locator or "eli/reg/" in lowered_locator or "regulation" in lowered_title


# Normalize output text before precision checks so citations like [1] do not look like unsupported numbers.
def _normalize_output_text_for_precision_checks(text: str) -> str:
    normalized = re.sub(r"\[\d+\]", " ", text)
    normalized = re.sub(r"https?://\S+", " ", normalized)
    return " ".join(normalized.split())


# Extract precision-shaped claim markers that should be grounded in direct excerpts.
def _extract_precision_claim_markers(*, text: str) -> list[str]:
    normalized = _normalize_output_text_for_precision_checks(text)
    markers: list[str] = []

    precision_context_markers = (
        "entered into force",
        "comes into force",
        "coming into force",
        "effective date",
        "effective from",
        "applies from",
        "applies on",
        "entry into force",
        "adopted on",
        "published on",
        "deadline",
        "deadlines",
        "timeline",
        "applicability details",
        "implementation date",
    )
    has_precision_context = any(marker in normalized for marker in precision_context_markers)

    year_matches = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", normalized)))
    if year_matches and has_precision_context:
        markers.append(f"calendar years ({', '.join(year_matches[:3])})")

    if has_precision_context and re.search(
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        markers.append("named calendar dates")

    if re.search(r"\b\d+(?:\.\d+)?\s*%", normalized):
        markers.append("percentages")

    if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:million|billion)\b", normalized, flags=re.IGNORECASE):
        markers.append("large numeric thresholds")

    if re.search(r"(?:€|\$|eur\s*)\d", normalized, flags=re.IGNORECASE):
        markers.append("monetary thresholds")

    return markers


# Block optimistic completions when precise claims are not backed by direct primary excerpts.
def guard_completion_against_unsupported_precision(
    *,
    actor_task: ActorTask,
    arguments: dict[str, object],
) -> dict[str, object]:
    output_text = arguments.get("output_text")
    if not isinstance(output_text, str) or not output_text.strip():
        return arguments

    if _output_stays_within_authoritative_intake_support(
        actor_task=actor_task,
        output_text=output_text,
    ):
        return arguments

    precision_markers = _extract_precision_claim_markers(text=output_text)
    available_level = max_available_evidence_support_level(actor_task=actor_task)
    required_level = required_evidence_support_level(
        actor_task=actor_task,
        output_text=output_text,
    )
    output_claim_level = output_claim_support_level(output_text=output_text)
    effective_required_level = required_level
    if _evidence_support_level_rank(output_claim_level) > _evidence_support_level_rank(
        effective_required_level
    ):
        effective_required_level = output_claim_level
    if _evidence_support_level_rank(available_level) >= _evidence_support_level_rank(
        effective_required_level
    ):
        return arguments

    guarded_arguments = dict(arguments)
    guarded_arguments["objective_satisfied"] = False
    guarded_arguments["evidence_sufficient"] = False
    guarded_arguments["_guarded_next_action"] = StepValidationDecision.REQUEST_MORE_EVIDENCE.value
    guarded_arguments["confidence"] = min(
        _coerce_provider_confidence(guarded_arguments.get("confidence"), default=0.0),
        0.35,
    )
    guarded_arguments["summary"] = (
        "The current draft is more specific than the observed evidence currently supports."
    )
    support_gap_detail = (
        f"typed requirement is '{required_level.value}', but the current output claims "
        f"'{output_claim_level.value}' precision while available evidence remains '{available_level.value}'."
    )
    if precision_markers:
        guarded_arguments["completion_rationale"] = (
            "The runtime kept the current step open because the draft introduced "
            f"{', '.join(precision_markers)} and the {support_gap_detail}"
        )
    else:
        guarded_arguments["completion_rationale"] = (
            "The runtime kept the current step open because the draft claims are more specific "
            f"than the observed evidence can support; {support_gap_detail}"
        )
    guarded_arguments["residual_uncertainty"] = (
        "Revise the draft to match the current evidence ceiling or fetch stronger excerpts before "
        "finalizing more specific legal or factual claims."
    )
    return guarded_arguments


# Allow exact task-provided facts to support local drafting without requiring external excerpts.
def _output_stays_within_authoritative_intake_support(
    *,
    actor_task: ActorTask,
    output_text: str,
) -> bool:
    intake_evidence = [
        evidence
        for evidence in actor_task.observed_evidence
        if evidence.source_kind == EvidenceSourceKind.INTAKE_PROVIDED
        and evidence.excerpt.strip()
    ]
    if not intake_evidence:
        return False

    local_step_text = " ".join(
        part.strip()
        for part in (
            actor_task.current_plan_step.objective,
            actor_task.current_plan_step.expected_outcome,
            actor_task.current_plan_step.rationale,
        )
        if part and part.strip()
    ).lower()
    normalized_output = _normalize_output_text_for_precision_checks(output_text).lower()
    support_requirement_text = f"{local_step_text} {normalized_output}"

    # External-source requirements must come from the current step or output, not global task preferences.
    external_support_markers = (
        "official source",
        "official sources",
        "official website",
        "official eu",
        "source shortlist",
        "web search",
        "fetch",
        "url",
        "citation",
        "citations",
        "reference",
        "references",
        "legal",
        "regulation",
        "compliance",
        "verify against",
        "check source",
        "article ",
        "law",
        "statute",
    )
    if any(marker in support_requirement_text for marker in external_support_markers):
        return False

    output_claims = _extract_local_precision_claims(text=normalized_output)
    if not output_claims:
        return True

    intake_text = " ".join(evidence.excerpt.lower() for evidence in intake_evidence)
    return all(
        _intake_text_supports_local_precision_claim(
            intake_text=intake_text,
            claim=claim,
        )
        for claim in output_claims
    )


# Extract local date/time/numeric details that can be checked against task-provided facts.
def _extract_local_precision_claims(*, text: str) -> list[str]:
    claims: list[str] = []
    patterns = (
        r"\b(?:19|20)\d{2}\b",
        r"\b\d{1,2}:\d{2}\s*(?:cet|cest|utc|gmt)?\b",
        r"\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+(?:19|20)\d{2}\b",
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s*(?:19|20)\d{2}\b",
        r"\bwithin\s+\d+\s+minutes?\b",
        r"\b\d+\s+minutes?\b",
    )
    for pattern in patterns:
        claims.extend(match.group(0).lower() for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    return sorted(set(claims))


# Match equivalent local date spellings without treating reformatted intake facts as new claims.
def _intake_text_supports_local_precision_claim(
    *,
    intake_text: str,
    claim: str,
) -> bool:
    if claim in intake_text:
        return True

    day_first = re.fullmatch(
        r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+((?:19|20)\d{2})",
        claim,
        flags=re.IGNORECASE,
    )
    if day_first is not None:
        day, month, year = day_first.groups()
        return bool(
            re.search(
                rf"\b{month}\s+0?{day},?\s+{year}\b",
                intake_text,
                flags=re.IGNORECASE,
            )
        )

    month_first = re.fullmatch(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+((?:19|20)\d{2})",
        claim,
        flags=re.IGNORECASE,
    )
    if month_first is not None:
        month, day, year = month_first.groups()
        return bool(
            re.search(
                rf"\b0?{day}\s+{month}\s+{year}\b",
                intake_text,
                flags=re.IGNORECASE,
            )
        )

    return False



__all__ = [
    "build_actor_execution_hints",
    "guard_completion_against_unsupported_precision",
    "max_available_evidence_support_level",
    "output_claim_support_level",
    "required_evidence_support_level",
]
