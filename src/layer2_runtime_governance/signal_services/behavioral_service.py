from __future__ import annotations

from typing import Any

from layer2_runtime_governance.behavioral import BehavioralAssessment


# Convert the behavioural read-model assessment into OPA facts.
def build_behavioral_signal(
    *,
    behavioral_assessment: BehavioralAssessment | None,
    thresholds_version: str,
) -> dict[str, Any]:
    assessment = behavioral_assessment
    raw_facts = {
        "mode": "shadow",
        "decision_influence": False,
        "status": assessment.status if assessment else "unavailable",
        "pillar_unavailable": assessment is None or assessment.status == "unavailable",
        "history_available": bool(assessment and assessment.history_available),
        "historical_run_count": int(assessment.historical_run_count if assessment else 0),
        "matched_run_count": int(assessment.matched_run_count if assessment else 0),
        "similarity_score": assessment.historical_similarity_score if assessment else None,
        "deviation_score": assessment.behavior_deviation_score if assessment else None,
        "novelty_score": assessment.novelty_score if assessment else None,
        "historical_failure_rate": _note_rate(assessment, "failure_rate"),
        "historical_review_rate": _note_rate(assessment, "review_rate"),
        "deviation_driver": _deviation_driver(assessment),
        "flags": list(assessment.behavioral_flags if assessment else ()),
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": max(
            float(raw_facts["deviation_score"] or 0.0),
            float(raw_facts["novelty_score"] or 0.0),
        ),
        "score_components": {
            "similarity_score": raw_facts["similarity_score"],
            "deviation_score": raw_facts["deviation_score"],
            "novelty_score": raw_facts["novelty_score"],
            "matched_run_count": raw_facts["matched_run_count"],
        },
        "reason_facts": raw_facts["flags"],
        "summary": assessment.summary if assessment else "Behavioral pillar was unavailable.",
        "mode": "shadow",
        "decision_influence": False,
        "score_formula_version": "behavioral_features_v2",
        "thresholds_version": thresholds_version,
    }


# Extract the persisted deviation driver from behavioural notes when present.
def _deviation_driver(assessment: BehavioralAssessment | None) -> str | None:
    if assessment is None:
        return None
    for note in assessment.notes:
        if note.startswith("deviation_driver="):
            return note.split("=", 1)[1]
    return None


# Extract non-normative historical rates persisted by the behavioural read model.
def _note_rate(assessment: BehavioralAssessment | None, prefix: str) -> float | None:
    if assessment is None:
        return None
    for note in assessment.notes:
        if note.startswith(f"{prefix}="):
            try:
                return float(note.split("=", 1)[1])
            except ValueError:
                return None
    return None
