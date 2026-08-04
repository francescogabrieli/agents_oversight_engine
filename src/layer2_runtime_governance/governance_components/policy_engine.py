import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from governance_policy_service import (
    OPAClientSettings,
    OPADecisionClient,
    PolicyServiceError,
)

# PolicyDecision is the deterministic outcome returned by the policy engine.
from .contracts import (
    PolicyEngineExecutionError,
    PolicyEnginePort,
    PolicyEvaluationInput,
    PolicyEvaluationResult,
)
from layer4_state_evidence_observability.infrastructure.environment import candidate_env_paths, load_env_file_values
from .decision_parser import _parse_non_empty_text, _parse_policy_decision, _parse_reason_codes
from .policy_input import build_opa_input_document

# Keep policy transport configuration and I/O behind the OPA adapter.
@dataclass(frozen=True)
class OPAHttpPolicyEngineSettings:
    # Settings for the HTTP OPA adapter.
    base_url: str = "http://127.0.0.1:8181"
    decision_path: str = "/v1/data/layer2/governance/decision"
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "OPAHttpPolicyEngineSettings":
        # Merge process env and local .env files into one policy engine settings object.
        env_file_values = _load_env_file_values()
        return cls(
            base_url=os.getenv(
                "LAYER2_GOVERNANCE_OPA_BASE_URL",
                env_file_values.get("LAYER2_GOVERNANCE_OPA_BASE_URL", "http://127.0.0.1:8181"),
            ),
            decision_path=os.getenv(
                "LAYER2_GOVERNANCE_OPA_DECISION_PATH",
                env_file_values.get(
                    "LAYER2_GOVERNANCE_OPA_DECISION_PATH",
                    "/v1/data/layer2/governance/decision",
                ),
            ),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_GOVERNANCE_OPA_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_GOVERNANCE_OPA_TIMEOUT_SECONDS", "5"),
                )
            ),
        )

# Keep policy transport configuration and I/O behind the OPA adapter.
class OPAHttpPolicyEngine(PolicyEnginePort):
    policy_engine_name = "opa_http_policy_engine"
    policy_bundle_version = "layer2_governance_v4"
    policy_config_version = "layer2_governance_thresholds_v2"

    def __init__(self, settings: OPAHttpPolicyEngineSettings | None = None) -> None:
        # Resolve settings once so the runtime stays deterministic.
        self._settings = settings or OPAHttpPolicyEngineSettings.from_env()

    # Preserve the v1 API for callers during this release; the runtime never invokes it.
    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        warnings.warn(
            "OPAHttpPolicyEngine.evaluate is deprecated; use evaluate_decision.",
            DeprecationWarning,
            stacklevel=2,
        )
        result = self._post_json(
            path=self._settings.decision_path,
            payload={"input": build_opa_input_document(policy_input=policy_input)},
        )
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA response must be an object.")
        return PolicyEvaluationResult(
            policy_decision=_parse_policy_decision(result.get("policy_decision")),
            reason_codes=_parse_reason_codes(result.get("reason_codes")),
            summary=_parse_non_empty_text(result.get("summary"), field_name="summary"),
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )

    # Ask OPA which optional signal pillars are required before invoking the judge.
    def evaluate_required_pillars(self, *, base_facts: dict[str, Any]) -> dict[str, bool]:
        result = self._post_json(
            path="/v1/data/layer2/governance/required_pillars",
            payload={"input": {
                "policy_bundle_version": self.policy_bundle_version,
                "policy_config_version": self.policy_config_version,
                "base_facts": base_facts,
            }},
        )
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA required_pillars response must be an object.")
        required_fields = (
            "require_evidence",
            "require_risk",
            "require_trajectory",
            "require_behavioral",
            "require_judge",
        )
        if any(type(result.get(field_name)) is not bool for field_name in required_fields):
            raise PolicyEngineExecutionError(
                "OPA required_pillars response is missing one or more boolean fields."
            )
        return {field_name: result[field_name] for field_name in required_fields}

    # Ask OPA for the final governance decision from versioned base facts and pillar signals.
    def evaluate_decision(self, *, decision_input: dict[str, Any]) -> dict[str, Any]:
        result = self._post_json(
            path="/v1/data/layer2/governance/decision",
            payload={"input": decision_input},
        )
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA decision response must be an object.")
        return result

    # Execute one JSON POST request against OPA and return the `result` envelope.
    def _post_json(self, *, path: str, payload: dict[str, Any]) -> Any:
        # Keep the legacy adapter surface while delegating transport to the service client.
        client = OPADecisionClient(
            settings=OPAClientSettings(
                base_url=self._settings.base_url,
                timeout_seconds=self._settings.timeout_seconds,
            ),
            # Resolve this module symbol at call time so existing test injection remains valid.
            opener=urlopen,
        )
        try:
            input_document = payload["input"]
            if not isinstance(input_document, dict):
                raise PolicyEngineExecutionError("OPA input document must be an object.")
            return client.evaluate(path=path, input_document=input_document)
        except PolicyServiceError as exc:
            raise PolicyEngineExecutionError(str(exc)) from exc

    # Resolve the OPA decision endpoint from the configured base URL and path.
    def _resolve_url(self) -> str:
        return self._settings.base_url.rstrip("/") + "/" + self._settings.decision_path.lstrip("/")

# Keep policy transport configuration and I/O behind the OPA adapter.
def build_default_policy_engine() -> PolicyEnginePort:
    return OPAHttpPolicyEngine()

# Keep policy transport configuration and I/O behind the OPA adapter.
def _load_env_file_values() -> dict[str, str]:
    return dict(load_env_file_values())

# Keep policy transport configuration and I/O behind the OPA adapter.
def _candidate_env_paths() -> list[Path]:
    return list(candidate_env_paths())

__all__ = [
    "OPAHttpPolicyEngineSettings",
    "OPAHttpPolicyEngine",
    "build_default_policy_engine",
    "_load_env_file_values",
    "_candidate_env_paths"
]
