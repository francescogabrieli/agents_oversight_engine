from __future__ import annotations

from typing import Any

from layer2_runtime_governance.judge import JudgeAssessment, JudgeRecommendedDirective


# Convert the optional semantic judge assessment into OPA facts.
def build_judge_signal(
    *,
    judge_assessment: JudgeAssessment | None,
    required: bool,
    thresholds_version: str,
) -> dict[str, Any]:
    verdict = _judge_verdict(judge_assessment)
    raw_facts = {
        "invoked": bool(judge_assessment and judge_assessment.invoked),
        "required": required,
        "verdict": verdict,
        "confidence": float(judge_assessment.confidence if judge_assessment else 0.0),
        "concerns": list(judge_assessment.flags if judge_assessment else ()),
        "summary": judge_assessment.summary if judge_assessment else "Judge was not invoked.",
    }
    return {
        "raw_facts": raw_facts,
        "diagnostic_score": raw_facts["confidence"],
        "score_components": {
            "confidence": raw_facts["confidence"],
            "invoked": raw_facts["invoked"],
            "required": required,
        },
        "reason_facts": raw_facts["concerns"],
        "summary": raw_facts["summary"],
        "score_formula_version": "judge_v1",
        "thresholds_version": thresholds_version,
    }


# Map legacy judge recommendations onto the OPA-owned verdict vocabulary.
def _judge_verdict(judge_assessment: JudgeAssessment | None) -> str:
    if judge_assessment is None:
        return "approve"
    if judge_assessment.recommendation == JudgeRecommendedDirective.REQUEST_MORE_EVIDENCE:
        return "reject_output"
    if judge_assessment.recommendation == JudgeRecommendedDirective.REPLAN:
        return "reject_output"
    if judge_assessment.recommendation == JudgeRecommendedDirective.WAIT_HUMAN_REVIEW:
        return "flag"
    if judge_assessment.recommendation == JudgeRecommendedDirective.CONTINUE:
        return "approve"
    return "uncertain"
