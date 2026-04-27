from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from layer1_intake import DomainType, SensitivityLevel

from .actor_contract import ActorResult, ActorResultType, CapabilityAccessMode
from .state import RuntimeFlag, RuntimeState, TaskState
from .tool_contract import ToolName


# PolicyDecision is the deterministic outcome returned by the policy engine.
class PolicyDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_READ_ONLY = "allow_read_only"
    ASK_CLARIFICATION = "ask_clarification"
    REQUIRE_HUMAN_REVIEW = "require_human_review"
    DENY = "deny"


# GovernanceDirective is the runtime action that follows the policy decision.
class GovernanceDirective(str, Enum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    REPLAN = "replan"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    WAIT_HUMAN_REVIEW = "wait_human_review"
    STOP = "stop"


# Reason codes remain compact and deterministic so tests and traces stay stable.
class GovernanceReasonCode(str, Enum):
    ALLOWED_BY_POLICY = "allowed_by_policy"
    READ_ONLY_ALLOWED = "read_only_allowed"
    TOOL_ALLOWED = "tool_allowed"
    TOOL_DISALLOWED = "tool_disallowed"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    WRITE_CAPABILITY_BLOCKED = "write_capability_blocked"
    READ_ONLY_MODE_ENFORCED = "read_only_mode_enforced"
    LOCAL_OUTPUT_ALLOWED = "local_output_allowed"
    REPLAN_ALLOWED = "replan_allowed"
    ASK_CLARIFICATION_REQUIRED = "ask_clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    DENIED_BY_POLICY = "denied_by_policy"
    NO_TOOL_REQUEST = "no_tool_request"
    EXTERNAL_CAPABILITY_REQUESTED = "external_capability_requested"


# PolicyEvaluationInput is the compact snapshot sent to OPA.
@dataclass(frozen=True)
class PolicyEvaluationInput:
    task_domain_type: DomainType
    task_domain_label: str
    task_sensitivity: SensitivityLevel
    policy_profile_name: str
    confidence_threshold: float
    read_only_mode: bool
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    budget_max_steps: int
    budget_max_tool_calls: int | None
    runtime_state: RuntimeState
    runtime_flags: tuple[RuntimeFlag, ...]
    current_plan_step_id: str | None
    current_plan_step_objective: str | None
    actor_result_type: ActorResultType
    capability_mode: CapabilityAccessMode
    external_impact: bool
    blocking_reason: str | None
    tool_name: ToolName | None
    tool_has_query: bool
    tool_has_url: bool
    tool_call_count: int
    observed_evidence_count: int
    actor_turn_count: int


# PolicyEvaluationResult mirrors the typed output returned by OPA.
@dataclass(frozen=True)
class PolicyEvaluationResult:
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    policy_engine_name: str
    policy_bundle_version: str


# GovernanceDecision is the canonical governance record stored in TaskState.
@dataclass(frozen=True)
class GovernanceDecision:
    directive: GovernanceDirective
    policy_decision: PolicyDecision
    reason_codes: tuple[GovernanceReasonCode, ...]
    summary: str
    constrains_capability_use: bool
    evaluated_actor_result_type: ActorResultType
    evaluated_tool_name: ToolName | None
    policy_engine_name: str
    policy_bundle_version: str


# PolicyEnginePort keeps the runtime decoupled from the policy engine backend.
class PolicyEnginePort(Protocol):
    policy_engine_name: str
    policy_bundle_version: str

    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult: ...


# Settings for the HTTP OPA adapter.
@dataclass(frozen=True)
class OPAHttpPolicyEngineSettings:
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


# Raised when the policy engine cannot return a valid deterministic decision.
class PolicyEngineExecutionError(RuntimeError):
    pass


# Build the policy input snapshot from TaskState and the actor outcome.
def build_policy_evaluation_input(*, task_state: TaskState) -> PolicyEvaluationInput:
    actor_result = task_state.actor_result
    if actor_result is None:
        raise ValueError("Cannot build policy evaluation input without an actor result.")

    evaluated_step = _resolve_evaluated_plan_step(task_state=task_state, actor_result=actor_result)
    current_tool_request = task_state.current_tool_request or actor_result.tool_request

    return PolicyEvaluationInput(
        task_domain_type=task_state.task_spec.domain_type,
        task_domain_label=task_state.task_spec.domain_label,
        task_sensitivity=task_state.task_spec.sensitivity,
        policy_profile_name=task_state.execution_policy.policy_profile_name,
        confidence_threshold=task_state.execution_policy.confidence_threshold,
        read_only_mode=task_state.execution_policy.read_only_mode,
        allowed_tools=tuple(task_state.execution_policy.allowed_tools),
        disallowed_tools=tuple(task_state.execution_policy.disallowed_tools),
        budget_max_steps=task_state.execution_policy.budget.max_steps,
        budget_max_tool_calls=task_state.execution_policy.budget.max_tool_calls,
        runtime_state=task_state.current_state,
        runtime_flags=tuple(task_state.runtime_flags),
        current_plan_step_id=evaluated_step.step_id if evaluated_step is not None else actor_result.plan_step_id,
        current_plan_step_objective=evaluated_step.objective if evaluated_step is not None else None,
        actor_result_type=actor_result.result_type,
        capability_mode=actor_result.capability_mode,
        external_impact=actor_result.external_impact,
        blocking_reason=actor_result.blocking_reason,
        tool_name=current_tool_request.tool_name if current_tool_request is not None else None,
        tool_has_query=bool(current_tool_request and current_tool_request.query),
        tool_has_url=bool(current_tool_request and current_tool_request.url),
        tool_call_count=task_state.tool_call_count,
        observed_evidence_count=len(task_state.observed_evidence),
        actor_turn_count=task_state.actor_turn_count,
    )


# Turn the typed governance input into the JSON document posted to OPA.
def build_opa_input_document(*, policy_input: PolicyEvaluationInput) -> dict[str, Any]:
    return {
        "task": {
            "domain_type": policy_input.task_domain_type.value,
            "domain_label": policy_input.task_domain_label,
            "sensitivity": policy_input.task_sensitivity.value,
        },
        "policy": {
            "profile_name": policy_input.policy_profile_name,
            "confidence_threshold": policy_input.confidence_threshold,
            "read_only_mode": policy_input.read_only_mode,
            "allowed_tools": list(policy_input.allowed_tools),
            "disallowed_tools": list(policy_input.disallowed_tools),
            "budget": {
                "max_steps": policy_input.budget_max_steps,
                "max_tool_calls": policy_input.budget_max_tool_calls,
            },
        },
        "runtime": {
            "state": policy_input.runtime_state.value,
            "flags": [flag.value for flag in policy_input.runtime_flags],
            "tool_call_count": policy_input.tool_call_count,
            "observed_evidence_count": policy_input.observed_evidence_count,
            "actor_turn_count": policy_input.actor_turn_count,
        },
        "step": {
            "id": policy_input.current_plan_step_id,
            "objective": policy_input.current_plan_step_objective,
        },
        "actor": {
            "result_type": policy_input.actor_result_type.value,
            "capability_mode": policy_input.capability_mode.value,
            "external_impact": policy_input.external_impact,
            "blocking_reason": policy_input.blocking_reason,
        },
        "tool": {
            "name": policy_input.tool_name.value if policy_input.tool_name is not None else None,
            "has_query": policy_input.tool_has_query,
            "has_url": policy_input.tool_has_url,
        },
    }


# Map the policy outcome and actor intent into a runtime directive.
def derive_governance_directive(
    *,
    policy_input: PolicyEvaluationInput,
    policy_result: PolicyEvaluationResult,
) -> GovernanceDirective:
    if policy_result.policy_decision == PolicyDecision.DENY:
        return GovernanceDirective.STOP

    if policy_result.policy_decision == PolicyDecision.REQUIRE_HUMAN_REVIEW:
        return GovernanceDirective.WAIT_HUMAN_REVIEW

    if policy_result.policy_decision == PolicyDecision.ASK_CLARIFICATION:
        return GovernanceDirective.ASK_USER_CLARIFICATION

    if policy_input.actor_result_type == ActorResultType.NEEDS_REPLAN:
        return GovernanceDirective.REPLAN

    if policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION:
        if policy_result.policy_decision == PolicyDecision.ALLOW_READ_ONLY:
            return GovernanceDirective.CONTINUE_READ_ONLY
        return GovernanceDirective.CONTINUE

    return GovernanceDirective.CONTINUE


# Decide whether the policy constrained capability use in a meaningful way.
def constrains_capability_use(
    *,
    policy_input: PolicyEvaluationInput,
    policy_result: PolicyEvaluationResult,
) -> bool:
    if policy_result.policy_decision in {
        PolicyDecision.ALLOW_READ_ONLY,
        PolicyDecision.REQUIRE_HUMAN_REVIEW,
        PolicyDecision.DENY,
    }:
        return True

    if (
        policy_input.actor_result_type == ActorResultType.PROPOSE_ACTION
        and policy_input.capability_mode == CapabilityAccessMode.WRITE
    ):
        return True

    return False


# Build a full governance decision record from the policy evaluation.
def build_governance_decision(
    *,
    policy_input: PolicyEvaluationInput,
    policy_result: PolicyEvaluationResult,
) -> GovernanceDecision:
    return GovernanceDecision(
        directive=derive_governance_directive(policy_input=policy_input, policy_result=policy_result),
        policy_decision=policy_result.policy_decision,
        reason_codes=policy_result.reason_codes,
        summary=policy_result.summary,
        constrains_capability_use=constrains_capability_use(
            policy_input=policy_input,
            policy_result=policy_result,
        ),
        evaluated_actor_result_type=policy_input.actor_result_type,
        evaluated_tool_name=policy_input.tool_name,
        policy_engine_name=policy_result.policy_engine_name,
        policy_bundle_version=policy_result.policy_bundle_version,
    )


# Execute the policy evaluation against OPA over HTTP.
class OPAHttpPolicyEngine(PolicyEnginePort):
    policy_engine_name = "opa_http_policy_engine"
    policy_bundle_version = "layer2_governance_v1"

    def __init__(self, settings: OPAHttpPolicyEngineSettings | None = None) -> None:
        # Resolve settings once so the runtime stays deterministic.
        self._settings = settings or OPAHttpPolicyEngineSettings.from_env()

    def evaluate(self, *, policy_input: PolicyEvaluationInput) -> PolicyEvaluationResult:
        payload = json.dumps(
            {"input": build_opa_input_document(policy_input=policy_input)},
            ensure_ascii=False,
        ).encode("utf-8")

        request = Request(
            self._resolve_url(),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self._settings.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:  # pragma: no cover - network/client errors
            raise PolicyEngineExecutionError(
                f"OPA policy request failed with HTTP {exc.code}: {exc.reason}"
            ) from exc
        except URLError as exc:  # pragma: no cover - network/client errors
            raise PolicyEngineExecutionError(f"OPA policy request failed: {exc.reason}") from exc
        except Exception as exc:  # pragma: no cover - network/client errors
            raise PolicyEngineExecutionError(f"OPA policy request failed: {exc}") from exc

        try:
            parsed_body = json.loads(raw_body)
        except json.JSONDecodeError as exc:  # pragma: no cover - malformed response
            raise PolicyEngineExecutionError(f"OPA returned invalid JSON: {exc}") from exc

        result = parsed_body.get("result")
        if not isinstance(result, dict):
            raise PolicyEngineExecutionError("OPA response did not contain a structured result.")

        return PolicyEvaluationResult(
            policy_decision=_parse_policy_decision(result.get("policy_decision")),
            reason_codes=_parse_reason_codes(result.get("reason_codes")),
            summary=_parse_non_empty_text(result.get("summary"), field_name="summary"),
            policy_engine_name=self.policy_engine_name,
            policy_bundle_version=self.policy_bundle_version,
        )

    # Resolve the OPA decision endpoint from the configured base URL and path.
    def _resolve_url(self) -> str:
        return self._settings.base_url.rstrip("/") + "/" + self._settings.decision_path.lstrip("/")


# Build the production policy engine used by the runtime.
def build_default_policy_engine() -> PolicyEnginePort:
    return OPAHttpPolicyEngine()


# Find the plan step that the actor result was evaluated against.
def _resolve_evaluated_plan_step(
    *,
    task_state: TaskState,
    actor_result: ActorResult,
):
    for step in task_state.plan_steps:
        if step.step_id == actor_result.plan_step_id:
            return step

    return None


# Parse a policy decision from the OPA response body.
def _parse_policy_decision(value: Any) -> PolicyDecision:
    if not isinstance(value, str):
        raise PolicyEngineExecutionError("OPA policy decision must be a string.")

    try:
        return PolicyDecision(value)
    except ValueError as exc:
        raise PolicyEngineExecutionError(f"OPA returned an unknown policy decision: {value}") from exc


# Parse the reason codes while failing closed on unknown values.
def _parse_reason_codes(value: Any) -> tuple[GovernanceReasonCode, ...]:
    if not isinstance(value, list) or not value:
        raise PolicyEngineExecutionError("OPA reason_codes must be a non-empty list.")

    parsed_codes: list[GovernanceReasonCode] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicyEngineExecutionError("OPA reason_codes entries must be strings.")
        try:
            parsed_codes.append(GovernanceReasonCode(item))
        except ValueError as exc:
            raise PolicyEngineExecutionError(f"OPA returned an unknown reason code: {item}") from exc

    return tuple(parsed_codes)


# Parse a required non-empty text field from the OPA response.
def _parse_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyEngineExecutionError(f"OPA response field '{field_name}' must be a non-empty string.")
    return " ".join(value.strip().split())


# Load simple KEY=VALUE pairs from the supported .env file locations.
def _load_env_file_values() -> dict[str, str]:
    env_values: dict[str, str] = {}

    for candidate in _candidate_env_paths():
        if not candidate.exists():
            continue

        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                env_values[key] = value

    return env_values


# Search both repository root and src-level env files for local development.
def _candidate_env_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    return [
        repo_root / ".env",
        repo_root / "src" / ".env",
    ]


__all__ = [
    "GovernanceDecision",
    "GovernanceDirective",
    "GovernanceReasonCode",
    "OPAHttpPolicyEngine",
    "OPAHttpPolicyEngineSettings",
    "PolicyDecision",
    "PolicyEngineExecutionError",
    "PolicyEnginePort",
    "PolicyEvaluationInput",
    "PolicyEvaluationResult",
    "build_default_policy_engine",
    "build_governance_decision",
    "build_opa_input_document",
    "build_policy_evaluation_input",
    "constrains_capability_use",
    "derive_governance_directive",
]
