from __future__ import annotations

import re

from layer1_intake import TaskSpec

from ..tool_contract import (
    CitationAlignment,
    DocumentType,
    EvidenceContentState,
    EvidenceSourceKind,
    Extractability,
    ObservedEvidence,
    SourceAuthority,
)


# Materialize authoritative task inputs as initial local runtime evidence.
def build_initial_observed_evidence(task_spec: TaskSpec) -> list[ObservedEvidence]:
    evidence_items: list[ObservedEvidence] = []
    seen: set[str] = set()
    candidate_index = 0
    for title, raw_text in (("Task goal", task_spec.goal), ("Task context", task_spec.context)):
        for statement in _split_statements(raw_text):
            candidate_index += 1
            _append_item(
                evidence_items=evidence_items,
                seen=seen,
                evidence_id=f"intake:{candidate_index}",
                title=title,
                excerpt=statement,
            )
    for label, items in (
        ("Task constraint", task_spec.constraints),
        ("Success criterion", task_spec.success_criteria),
    ):
        for item in items:
            candidate_index += 1
            _append_item(
                evidence_items=evidence_items,
                seen=seen,
                evidence_id=f"intake:{candidate_index}",
                title=label,
                excerpt=item,
            )
    return evidence_items


# Append one deduplicated authoritative intake statement to runtime evidence.
def _append_item(
    *,
    evidence_items: list[ObservedEvidence],
    seen: set[str],
    evidence_id: str,
    title: str,
    excerpt: str,
) -> None:
    normalized_excerpt = " ".join(excerpt.split())
    if not normalized_excerpt or not _is_authoritative_text(normalized_excerpt):
        return
    dedupe_key = normalized_excerpt.lower()
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    evidence_items.append(
        ObservedEvidence(
            evidence_id=evidence_id,
            plan_step_id="intake",
            title=title,
            locator="intake://task_spec",
            excerpt=normalized_excerpt[:1000],
            source_kind=EvidenceSourceKind.INTAKE_PROVIDED,
            source_tool=None,
            content_state=EvidenceContentState.EXTRACTED_FULL,
            source_authority=SourceAuthority.PRIMARY,
            document_type=DocumentType.UNKNOWN,
            extractability=Extractability.COMPLETE,
            citation_alignment=CitationAlignment.EXACT,
        )
    )


# Split semi-structured intake text into separately traceable statements.
def _split_statements(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    parts = re.split(r"\s*[;•]\s*|\.\s+(?=[A-Z0-9])", normalized)
    return [part.strip(" .") for part in parts if part and part.strip(" .")]


# Exclude generic preferences while retaining factual and operational inputs.
def _is_authoritative_text(text: str) -> bool:
    lowered = text.lower()
    if lowered in {"none", "n/a"}:
        return False
    meta_only_markers = (
        "prefer official sources",
        "prefer using official sources",
        "prefer official eu sources",
        "when referencing compliance/impact context",
        "if relevant",
    )
    if any(marker in lowered for marker in meta_only_markers):
        factual_markers = (
            "detected",
            "observed",
            "confirmed",
            "under investigation",
            "within ",
            "check ",
            "verify ",
            "include ",
            "ask ",
            "request ",
            "report ",
            "incident",
            "customer",
            "tenant",
            "pipeline",
            "email",
        )
        if not any(marker in lowered for marker in factual_markers):
            return False
    return len(lowered) >= 8


__all__ = ["build_initial_observed_evidence"]
