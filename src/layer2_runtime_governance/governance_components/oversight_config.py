
import json
from pathlib import Path

from ..oversight import (
    EvidenceQualityWeights,
    OversightPolicyConfig,
    RiskScores,
    TrajectoryPenalties,
)


# Load deterministic oversight scoring settings from repository configuration.
def load_oversight_policy_config(*, config_path: Path | None = None) -> OversightPolicyConfig:
    resolved_path = config_path or get_default_oversight_policy_config_path()
    if not resolved_path.exists():
        return build_default_oversight_policy_config()
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    thresholds = payload.get("thresholds", {})
    weights = payload.get("evidence_quality_weights", {})
    risk_scores = payload.get("risk_scores", {})
    trajectory_penalties = payload.get("trajectory_penalties", {})
    return OversightPolicyConfig(
        min_actor_confidence=float(thresholds.get("min_actor_confidence", 0.65)),
        min_evidence_quality=float(thresholds.get("min_evidence_quality", 0.70)),
        max_operational_risk=float(thresholds.get("max_operational_risk", 0.60)),
        min_trajectory_health=float(thresholds.get("min_trajectory_health", 0.60)),
        evidence_quality_weights=EvidenceQualityWeights(
            evidence_sufficient=float(weights.get("evidence_sufficient", 0.30)),
            objective_satisfied=float(weights.get("objective_satisfied", 0.20)),
            actor_confidence=float(weights.get("actor_confidence", 0.20)),
            observed_evidence_present=float(weights.get("observed_evidence_present", 0.15)),
            source_locator_present=float(weights.get("source_locator_present", 0.10)),
            last_tool_success=float(weights.get("last_tool_success", 0.05)),
        ),
        risk_scores=RiskScores(
            tool_risk={
                str(key): float(value)
                for key, value in risk_scores.get(
                    "tool_risk",
                    {"none": 0.0, "low": 0.15, "medium": 0.45, "high": 0.75, "critical": 0.95},
                ).items()
            },
            sensitivity_multiplier={
                str(key): float(value)
                for key, value in risk_scores.get(
                    "sensitivity_multiplier",
                    {"low": 1.0, "medium": 1.15, "high": 1.35, "critical": 1.6},
                ).items()
            },
            external_impact_additive=float(risk_scores.get("external_impact_additive", 0.25)),
            write_capability_additive=float(risk_scores.get("write_capability_additive", 0.30)),
            requires_human_review_additive=float(
                risk_scores.get("requires_human_review_additive", 0.40)
            ),
        ),
        trajectory_penalties=TrajectoryPenalties(
            max_actor_turns_per_step=int(trajectory_penalties.get("max_actor_turns_per_step", 5)),
            max_replans=int(trajectory_penalties.get("max_replans", 2)),
            actor_turn_overage_penalty=float(
                trajectory_penalties.get("actor_turn_overage_penalty", 0.10)
            ),
            completion_attempt_turn_overage_scale=float(
                trajectory_penalties.get("completion_attempt_turn_overage_scale", 0.50)
            ),
            failed_tool_result_penalty=float(
                trajectory_penalties.get("failed_tool_result_penalty", 0.20)
            ),
            needs_replan_penalty=float(trajectory_penalties.get("needs_replan_penalty", 0.25)),
            low_confidence_uncertainty_penalty=float(
                trajectory_penalties.get("low_confidence_uncertainty_penalty", 0.20)
            ),
        ),
    )


# Return the repository-local oversight configuration path.
def get_default_oversight_policy_config_path() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "configs" / "layer2_oversight_policy.json"


# Build a conservative fallback when the JSON policy file is unavailable.
def build_default_oversight_policy_config() -> OversightPolicyConfig:
    return OversightPolicyConfig(
        min_actor_confidence=0.65,
        min_evidence_quality=0.70,
        max_operational_risk=0.60,
        min_trajectory_health=0.60,
        evidence_quality_weights=EvidenceQualityWeights(
            evidence_sufficient=0.30,
            objective_satisfied=0.20,
            actor_confidence=0.20,
            observed_evidence_present=0.15,
            source_locator_present=0.10,
            last_tool_success=0.05,
        ),
        risk_scores=RiskScores(
            tool_risk={"none": 0.0, "low": 0.15, "medium": 0.45, "high": 0.75, "critical": 0.95},
            sensitivity_multiplier={"low": 1.0, "medium": 1.15, "high": 1.35, "critical": 1.6},
            external_impact_additive=0.25,
            write_capability_additive=0.30,
            requires_human_review_additive=0.40,
        ),
        trajectory_penalties=TrajectoryPenalties(
            max_actor_turns_per_step=5,
            max_replans=2,
            actor_turn_overage_penalty=0.10,
            completion_attempt_turn_overage_scale=0.50,
            failed_tool_result_penalty=0.20,
            needs_replan_penalty=0.25,
            low_confidence_uncertainty_penalty=0.20,
        ),
    )


__all__ = [
    "build_default_oversight_policy_config",
    "get_default_oversight_policy_config_path",
    "load_oversight_policy_config",
]
