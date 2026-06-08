from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from typing import Any

from .actor_contract import (
    ActorPort,
    ActorResult,
    ActorResultType,
    ActorTask,
    CapabilityAccessMode,
)
from .openrouter_client import (
    OpenRouterChatClient,
    OpenRouterRuntimeError,
    OpenRouterRuntimeSettings,
)
from .state import ScratchpadEntry, ScratchpadEntryType, StepValidation, StepValidationDecision
from .tool_contract import (
    EvidenceSourceKind,
    EvidenceSupportLevel,
    ObservedEvidence,
    ToolName,
    ToolRequest,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    OpenAI = None


@dataclass(frozen=True)
class OpenAIActorSettings:
    model: str = "gpt-5.4-nano"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "OpenAIActorSettings":
        # Merge process env and local .env files into one actor settings object.
        env_file_values = _load_env_file_values()

        return cls(
            model=os.getenv(
                "LAYER2_ACTOR_OPENAI_MODEL",
                env_file_values.get("LAYER2_ACTOR_OPENAI_MODEL", "gpt-5.4-nano"),
            ),
            api_key=os.getenv("OPENAI_API_KEY", env_file_values.get("OPENAI_API_KEY")),
            timeout_seconds=float(
                os.getenv(
                    "LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS",
                    env_file_values.get("LAYER2_ACTOR_OPENAI_TIMEOUT_SECONDS", "30"),
                )
            ),
        )


@dataclass(frozen=True)
class OpenRouterActorSettings:
    # OpenRouterActorSettings selects the temporary cloud actor test model.
    model: str = "openai/gpt-oss-120b:free"
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 120.0
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "OpenRouterActorSettings":
        # Reuse the shared OpenRouter settings while allowing actor-specific model override.
        settings = OpenRouterRuntimeSettings.from_env(
            model_env_key="LAYER2_ACTOR_OPENROUTER_MODEL"
        )
        return cls(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
            temperature=settings.temperature,
        )


class ActorExecutionError(RuntimeError):
    """Raised when the actor cannot produce a valid execution outcome."""


# Build the constrained system prompt for Layer 2 step execution.
def build_actor_system_prompt() -> str:
    return """
You are the execution agent for layer 2 of a governed plan-and-execute runtime.
The planner has already produced the semantic plan.
Your job is to execute only the current plan step, using prior executed steps, observed evidence, and the last tool result as context.

Rules:
- Operate only on the current plan step.
- Use the full plan as context, not as a second planning task.
- Do not rewrite the plan and do not invent new plan steps.
- Work in a local ReAct-like way for the current step.
- Use the runtime-bound tools when external information is required.
- Read-only runtime tools exposed in this turn are available for evidence gathering.
- Do not emit blocked just because external lookup is needed; request a read-only runtime tool instead.
- If you do not need an external tool, finish the turn by emitting exactly one structured outcome tool.
- Use emit_intermediate_output only for progress that should continue the current step.
- Use emit_step_completed or emit_final_output only after validating that the current step objective is satisfied.
- Treat the current plan step as the only active objective. Do not gather evidence for future steps.
- If the current step can be completed from existing evidence, complete it even if later steps may still have open evidence gaps.
- After a replan, the replacement step objective overrides the older blocked path; do not keep pursuing the pre-replan objective unless the new step explicitly says to do so.
- If one official source already supports the current step and another source fetch fails, prefer completing the current step with residual uncertainty instead of retrying many near-equivalent retrieval attempts.
- If runtime_flags includes `extractability_limited`, do not keep chasing near-equivalent extraction attempts; prefer a compact summary or source shortlist that uses already observed official metadata and clearly notes the limitation.
- A residual uncertainty about missing precision is not, by itself, a reason to keep the current step open when the step only asks for high-level supported themes.
- A discovered source URL is not factual support by itself. Treat only observed excerpts and successful fetched content as direct support for factual claims.
- When citing official implementation pages or procedural regulations, describe them as procedural/implementation sources; do not say they directly enforce the rules unless the source explicitly states that.
- If current_or_prior_steps_have_procedural_implementation_source is true, keep the wording at the level of "describes implementation/procedural enforcement" rather than "enforces the rules" unless a fetched excerpt explicitly says otherwise.
- Do not introduce exact dates, years, thresholds, deadlines, or other precise numeric/legal claims unless those details are directly present in the observed evidence or fetched content.
- When the current step asks for a short, brief, concise, or high-level summary, keep obligation language broad unless a named example is directly present in observed evidence. Prefer "obligations and prohibitions for gatekeepers" over unsupported examples such as self-preferencing, interoperability, data portability, ranking, non-discriminatory access, or transparent terms.
- If the runtime says available_evidence_support_level is high_level_summary, do not try to satisfy the step by becoming more granular; reduce claim precision instead.
- Respect the explicit required_evidence_support_level and max_supported_claim_precision fields from the runtime contract.
- If the available evidence is below the requirement, prefer lowering claim precision or requesting more evidence rather than inventing finer-grained claims.
- Keep the turn compact, explicit, and directly actionable by the runtime.
- Respect read_only_mode when it is true.
- Do not emit chain-of-thought.
- Do not emit concrete shell commands or raw implementation details.
- Prefer grounded local outputs when the available evidence is already sufficient.
""".strip()


# Build the user prompt from the canonical actor handoff.
def build_actor_user_prompt(*, actor_task: ActorTask) -> str:
    payload = {
        "objective": actor_task.task_spec.goal,
        "task_spec": {
            "goal": actor_task.task_spec.goal,
            "context": actor_task.task_spec.context,
            "constraints": actor_task.task_spec.constraints,
            "domain_type": actor_task.task_spec.domain_type.value,
            "domain_label": actor_task.task_spec.domain_label,
            "sensitivity": actor_task.task_spec.sensitivity.value,
            "success_criteria": actor_task.task_spec.success_criteria,
        },
        "execution_policy": {
            "policy_profile_name": actor_task.execution_policy.policy_profile_name,
            "confidence_threshold": actor_task.execution_policy.confidence_threshold,
            "read_only_mode": actor_task.execution_policy.read_only_mode,
            "budget": {
                "max_steps": actor_task.execution_policy.budget.max_steps,
                "max_tool_calls": actor_task.execution_policy.budget.max_tool_calls,
            },
            "tool_call_count": actor_task.tool_call_count,
            "remaining_tool_calls": actor_task.remaining_tool_calls,
        },
        "intake_metadata": {
            "parse_status": actor_task.intake_metadata.parse_status.value,
            "warnings": actor_task.intake_metadata.warnings,
            "missing_information": actor_task.intake_metadata.missing_information,
        },
        "runtime": {
            "current_state": actor_task.current_state.value,
            "runtime_flags": [flag.value for flag in actor_task.runtime_flags],
            "actor_turn_count": actor_task.actor_turn_count,
        },
        "planner": {
            "planner_name": actor_task.planner_name,
            "planner_prompt_version": actor_task.planner_prompt_version,
            "planner_rationale": actor_task.planner_rationale,
        },
        "plan": {
            "current_step_number": actor_task.current_plan_step_index + 1,
            "current_plan_step_index": actor_task.current_plan_step_index,
            "current_plan_step": {
                "step_id": actor_task.current_plan_step.step_id,
                "objective": actor_task.current_plan_step.objective,
                "rationale": actor_task.current_plan_step.rationale,
                "expected_outcome": actor_task.current_plan_step.expected_outcome,
                "step_kind": (
                    actor_task.current_plan_step.step_kind.value
                    if actor_task.current_plan_step.step_kind is not None
                    else None
                ),
                "evidence_requirement": (
                    actor_task.current_plan_step.evidence_requirement.value
                    if actor_task.current_plan_step.evidence_requirement is not None
                    else None
                ),
                "claim_precision_ceiling": (
                    actor_task.current_plan_step.claim_precision_ceiling.value
                    if actor_task.current_plan_step.claim_precision_ceiling is not None
                    else None
                ),
                "status": actor_task.current_plan_step.status.value,
            },
            "plan_steps": [
                {
                    "step_id": step.step_id,
                    "objective": step.objective,
                    "rationale": step.rationale,
                    "expected_outcome": step.expected_outcome,
                    "status": step.status.value,
                }
                for step in actor_task.plan_steps
            ],
        },
        "past_steps": [
            {
                "step_id": step.step_id,
                "objective": step.objective,
                "result_type": step.result_type.value,
                "summary": step.summary,
                "output_text": step.output_text,
            }
            for step in actor_task.past_steps
        ],
        "observed_evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "title": evidence.title,
                "locator": evidence.locator,
                "excerpt": evidence.excerpt,
                "source_kind": evidence.source_kind.value,
                "source_tool": evidence.source_tool.value if evidence.source_tool is not None else None,
                "support_level": evidence.support_level.value,
            }
            for evidence in actor_task.observed_evidence
        ],
        # Scratchpad and validations are summarized state, not chain-of-thought.
        "scratchpad": [
            {
                "turn_index": entry.turn_index,
                "plan_step_id": entry.plan_step_id,
                "entry_type": entry.entry_type.value,
                "summary": entry.summary,
                "evidence_refs": entry.evidence_refs,
            }
            for entry in actor_task.scratchpad
        ],
        "step_validations": [
            {
                "plan_step_id": validation.plan_step_id,
                "objective_satisfied": validation.objective_satisfied,
                "evidence_sufficient": validation.evidence_sufficient,
                "confidence": validation.confidence,
                "next_action": validation.next_action.value,
                "rationale_summary": validation.rationale_summary,
                "residual_uncertainty": validation.residual_uncertainty,
            }
            for validation in actor_task.step_validations
        ],
        "last_tool_result": (
            {
                "tool_name": actor_task.last_tool_result.request.tool_name.value,
                "success": actor_task.last_tool_result.success,
                "summary": actor_task.last_tool_result.summary,
                "content": actor_task.last_tool_result.content,
                "error_message": actor_task.last_tool_result.error_message,
                "sources": [
                    {
                        "title": source.title,
                        "locator": source.locator,
                        "snippet": source.snippet,
                    }
                    for source in actor_task.last_tool_result.sources
                ],
            }
            if actor_task.last_tool_result is not None
            else None
        ),
        "execution_hints": _build_actor_execution_hints(actor_task=actor_task),
    }

    return (
        "Execute the current plan step in the governed plan-and-execute runtime.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


# Add deterministic runtime hints so the actor stays inside the semantic boundary of the current step.
def _build_actor_execution_hints(*, actor_task: ActorTask) -> dict[str, object]:
    current_step_id = actor_task.current_plan_step.step_id
    current_step_evidence = [
        evidence for evidence in actor_task.observed_evidence if evidence.plan_step_id == current_step_id
    ]
    direct_support_evidence = [
        evidence for evidence in actor_task.observed_evidence if _is_direct_fact_support_evidence(evidence)
    ]
    available_support_level = _max_available_evidence_support_level(actor_task=actor_task)
    required_support_level = _required_evidence_support_level(
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
def _max_available_evidence_support_level(*, actor_task: ActorTask) -> EvidenceSupportLevel:
    available_levels = [item.support_level for item in actor_task.observed_evidence]
    if not available_levels:
        return EvidenceSupportLevel.LOCAL_ONLY
    return max(available_levels, key=_evidence_support_level_rank)


# Compute the minimum evidence level needed by the current step and optional output draft.
def _required_evidence_support_level(
    *,
    actor_task: ActorTask,
    output_text: str | None,
) -> EvidenceSupportLevel:
    return actor_task.required_evidence_support_level


# Estimate how specific the current output draft is without changing the typed step requirement.
def _output_claim_support_level(
    *,
    output_text: str | None,
) -> EvidenceSupportLevel:
    if not output_text or not output_text.strip():
        return EvidenceSupportLevel.LOCAL_ONLY
    return _required_evidence_support_level_from_output_text(text=output_text)


# Infer the minimum support level from the semantic scope of the current step.
def _required_evidence_support_level_from_step(*, actor_task: ActorTask) -> EvidenceSupportLevel:
    if actor_task.current_plan_step.evidence_requirement is not None:
        return actor_task.current_plan_step.evidence_requirement

    if actor_task.current_plan_step.claim_precision_ceiling is not None:
        return actor_task.current_plan_step.claim_precision_ceiling

    if actor_task.current_plan_step.step_kind is not None:
        return _required_evidence_support_level_from_step_kind(
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
def _required_evidence_support_level_from_step_kind(*, step_kind) -> EvidenceSupportLevel:
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
def _required_evidence_support_level_from_output_text(*, text: str) -> EvidenceSupportLevel:
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
def _guard_completion_against_unsupported_precision(
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
    available_level = _max_available_evidence_support_level(actor_task=actor_task)
    required_level = _required_evidence_support_level(
        actor_task=actor_task,
        output_text=output_text,
    )
    output_claim_level = _output_claim_support_level(output_text=output_text)
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


class OpenAIActor(ActorPort):
    actor_name = "openai_layer2_actor"
    prompt_version = "actor_v4_runtime_tool_binding"

    def __init__(self, settings: OpenAIActorSettings | None = None) -> None:
        # Validate actor prerequisites once at construction time.
        self._settings = settings or OpenAIActorSettings.from_env()

        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is not installed. Install it before using the Layer 2 actor."
            )
        if not self._settings.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Set it in the process environment or in src/.env."
            )

        self._client = OpenAI(
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    # Ask OpenAI for one tool-bound actor turn and convert it into the domain result.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        # Keep the actor to one tool call so the runtime can validate a single typed outcome per turn.
        try:
            response = self._client.responses.create(
                model=self._settings.model,
                input=[
                    {
                        "role": "system",
                        "content": build_actor_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": build_actor_user_prompt(actor_task=actor_task),
                    },
                ],
                tools=_build_actor_runtime_tools(),
                tool_choice="required",
                parallel_tool_calls=False,
                max_tool_calls=1,
            )
        except Exception as exc:  # pragma: no cover - network/client errors
            raise ActorExecutionError(f"Actor request failed: {exc}") from exc

        tool_call = _extract_single_function_call(response)
        return _convert_tool_call_to_actor_result(
            actor_task=actor_task,
            tool_call=tool_call,
        )


class OpenRouterActor(ActorPort):
    # OpenRouterActor runs only the actor role on the temporary cloud test backend.
    actor_name = "openrouter_gpt_oss_120b_free_actor"
    prompt_version = "actor_v5_openrouter_standard_tool_calling"

    def __init__(self, settings: OpenRouterActorSettings | None = None) -> None:
        # Validate OpenRouter actor settings without affecting planner/replanner providers.
        self._settings = settings or OpenRouterActorSettings.from_env()
        self._client = OpenRouterChatClient(
            OpenRouterRuntimeSettings(
                model=self._settings.model,
                api_key=self._settings.api_key,
                base_url=self._settings.base_url,
                timeout_seconds=self._settings.timeout_seconds,
                temperature=self._settings.temperature,
            )
        )

    # Ask OpenRouter for one standard tool call and convert it into ActorResult.
    def act(self, *, actor_task: ActorTask) -> ActorResult:
        try:
            payload = self._client.complete_tool_call(
                system_prompt=build_actor_system_prompt(),
                user_prompt=build_actor_user_prompt(actor_task=actor_task),
                tools=_build_actor_runtime_tools(),
            )
        except OpenRouterRuntimeError as exc:
            if "no tool call and no assistant content" in str(exc):
                return _build_provider_blocked_result(actor_task=actor_task, detail=str(exc))
            raise ActorExecutionError(f"OpenRouter actor request failed: {exc}") from exc

        tool_call = _convert_json_payload_to_tool_call(payload)
        return _convert_tool_call_to_actor_result(actor_task=actor_task, tool_call=tool_call)


# Build the bound runtime tools exposed to the actor model.
def _build_actor_runtime_tools() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "name": "request_web_search",
            "description": (
                "Request a read-only web search when the current plan step requires discovering external sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    "extractive_goal": {"type": "string"},
                },
                "required": ["summary", "reason", "query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_fetch_url_text",
            "description": (
                "Request a read-only fetch of a concrete URL when the current plan step needs extraction from a known source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "url": {"type": "string"},
                    "extractive_goal": {"type": "string"},
                },
                "required": ["summary", "reason", "url"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_echo",
            "description": "Request the configured read-only MCP echo tool for gateway connectivity checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["summary", "reason", "text"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_read_fixture",
            "description": "Request a read-only MCP fixture read from the configured test fixture directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["summary", "reason", "path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_text_stats",
            "description": "Request read-only text statistics through the configured MCP test server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "text": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["summary", "reason", "text"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "request_mcp_calculate_expression",
            "description": "Request safe arithmetic calculation through the configured MCP test server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "expression": {"type": "string"},
                },
                "required": ["summary", "reason", "expression"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_intermediate_output",
            "description": (
                "Emit a grounded progress note when the current plan step should continue without completing yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "output_text": {"type": "string"},
                    "progress_note": {"type": "string"},
                    "residual_uncertainty": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["summary", "output_text", "progress_note", "confidence"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_step_completed",
            "description": (
                "Complete the current plan step only after self-validating that the objective is satisfied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "output_text": {"type": "string"},
                    "completion_rationale": {"type": "string"},
                    "objective_satisfied": {"type": "boolean"},
                    "evidence_sufficient": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "residual_uncertainty": {"type": "string"},
                },
                "required": [
                    "summary",
                    "output_text",
                    "completion_rationale",
                    "objective_satisfied",
                    "evidence_sufficient",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_final_output",
            "description": "Emit the final answer only after self-validating that the final plan step is satisfied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "output_text": {"type": "string"},
                    "completion_rationale": {"type": "string"},
                    "objective_satisfied": {"type": "boolean"},
                    "evidence_sufficient": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "residual_uncertainty": {"type": "string"},
                },
                "required": [
                    "summary",
                    "output_text",
                    "completion_rationale",
                    "objective_satisfied",
                    "evidence_sufficient",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_ask_clarification",
            "description": "Ask for clarification when missing information blocks safe progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "blocking_reason": {"type": "string"},
                },
                "required": ["summary", "blocking_reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_blocked",
            "description": "Signal that the current step cannot proceed safely from the current state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "blocking_reason": {"type": "string"},
                },
                "required": ["summary", "blocking_reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emit_needs_replan",
            "description": "Signal that the current semantic step is insufficient or misaligned and requires replanning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "blocking_reason": {"type": "string"},
                },
                "required": ["summary", "blocking_reason"],
                "additionalProperties": False,
            },
        },
    ]


# Build the JSON action schema described to non-tool-calling actor models.
def _build_json_actor_output_shape() -> str:
    tool_names = [str(tool["name"]) for tool in _build_actor_runtime_tools()]
    return json.dumps(
        {
            "tool_name": "one of the allowed runtime action names",
            "arguments": {
                "summary": "short audit-safe summary",
                "reason": "required for request_* tool actions",
                "output_text": "required for emit_* output actions",
                "confidence": "number from 0.0 to 1.0 when validating output",
            },
            "allowed_tool_names": tool_names,
        },
        indent=2,
        ensure_ascii=False,
    )


# Convert empty provider responses into a typed blocked actor result instead of crashing the graph.
def _build_provider_blocked_result(*, actor_task: ActorTask, detail: str) -> ActorResult:
    blocking_reason = f"Actor provider returned no usable runtime action: {detail}"
    return ActorResult(
        plan_step_id=actor_task.current_plan_step.step_id,
        result_type=ActorResultType.BLOCKED,
        summary="Actor provider returned no usable runtime action.",
        capability_mode=CapabilityAccessMode.NONE,
        blocking_reason=blocking_reason,
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.UNCERTAINTY,
            summary=blocking_reason,
        ),
    )


# Convert JSON actor output into the same shape used by OpenAI function tool calls.
def _convert_json_payload_to_tool_call(payload: dict[str, Any]) -> SimpleNamespace:
    normalized_payload = _normalize_actor_payload(payload=payload)
    tool_name = normalized_payload.get("tool_name")
    arguments = normalized_payload.get("arguments")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ActorExecutionError("Actor JSON response must include string 'tool_name'.")
    if not isinstance(arguments, dict):
        raise ActorExecutionError("Actor JSON response must include object 'arguments'.")

    allowed_names = {str(tool["name"]) for tool in _build_actor_runtime_tools()}
    if tool_name not in allowed_names:
        raise ActorExecutionError(f"Actor emitted an unknown runtime action: {tool_name}")

    return SimpleNamespace(name=tool_name, arguments=json.dumps(arguments, ensure_ascii=False))


# Normalize provider JSON that ignores the expected actor payload envelope.
def _normalize_actor_payload(*, payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("tool_name"), str):
        return payload

    output_value = _first_string_value(
        payload,
        "output_text",
        "answer",
        "final_answer",
        "summary",
        "content",
    )
    if output_value and _looks_like_completion_payload(payload):
        return {
            "tool_name": "emit_step_completed",
            "arguments": {
                "summary": _first_string_value(
                    payload,
                    "summary",
                    "completion_rationale",
                    "output_text",
                    "answer",
                    "final_answer",
                    "content",
                )
                or "Actor provider returned JSON without the runtime action envelope.",
                "output_text": output_value,
                "completion_rationale": _first_string_value(
                    payload,
                    "completion_rationale",
                    "summary",
                    "output_text",
                    "answer",
                    "final_answer",
                    "content",
                )
                or "Provider JSON was recovered as a completion payload.",
                "objective_satisfied": bool(payload.get("objective_satisfied", True)),
                "evidence_sufficient": bool(payload.get("evidence_sufficient", True)),
                "confidence": _coerce_provider_confidence(payload.get("confidence"), default=0.55),
            },
        }

    if output_value:
        return {
            "tool_name": "emit_blocked",
            "arguments": {
                "summary": "Actor provider returned malformed JSON without a usable runtime action.",
                "blocking_reason": (
                    "The actor provider returned answer-like JSON without the required "
                    f"runtime action envelope. Preserved excerpt: {_truncate_provider_excerpt(output_value)}"
                ),
            },
        }

    raise ActorExecutionError("Actor JSON response must include string 'tool_name'.")


# Return the first non-empty string value for known provider fallback keys.
def _first_string_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# Recover only clearly completion-shaped JSON payloads without inventing tool intent.
def _looks_like_completion_payload(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("completion_rationale"), str) and payload["completion_rationale"].strip():
        return True
    return any(key in payload for key in ("objective_satisfied", "evidence_sufficient", "confidence"))


# Clamp provider confidence values before they enter the typed validation contract.
def _coerce_provider_confidence(value: object, *, default: float) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(float(value), 1.0))
    return default


# Keep provider protocol excerpts compact inside blocked outcomes.
def _truncate_provider_excerpt(value: str, *, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


# Extract exactly one function tool call from the model response.
def _extract_single_function_call(response):
    function_calls = [
        item
        for item in getattr(response, "output", []) or []
        if getattr(item, "type", None) == "function_call"
    ]

    if len(function_calls) != 1:
        raise ActorExecutionError(
            f"Actor must emit exactly one function tool call, received {len(function_calls)}."
        )

    return function_calls[0]


# Convert one function tool call into the stable ActorResult domain model.
def _convert_tool_call_to_actor_result(*, actor_task: ActorTask, tool_call) -> ActorResult:
    tool_name = getattr(tool_call, "name", None)
    raw_arguments = getattr(tool_call, "arguments", None)

    if tool_name is None or raw_arguments is None:
        raise ActorExecutionError("Actor returned a malformed function tool call.")

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ActorExecutionError(f"Actor returned invalid tool arguments: {exc}") from exc

    plan_step_id = actor_task.current_plan_step.step_id

    if tool_name == "request_web_search":
        _require_arguments(arguments, "summary", "reason")
        query = _resolve_search_query(actor_task=actor_task, arguments=arguments)
        # Tool-selection outcomes are converted into ToolRequest for the gateway node.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.READ_ONLY,
            external_impact=False,
            tool_request=ToolRequest(
                plan_step_id=plan_step_id,
                tool_name=ToolName.WEB_SEARCH,
                reason=arguments["reason"],
                query=query,
                max_results=int(arguments.get("max_results", 5)),
                extractive_goal=arguments.get("extractive_goal"),
            ),
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.TOOL_DECISION,
                summary=arguments["summary"],
            ),
        )

    if tool_name == "request_fetch_url_text":
        _require_arguments(arguments, "summary", "reason", "url")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.PROPOSE_ACTION,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.READ_ONLY,
            external_impact=False,
            tool_request=ToolRequest(
                plan_step_id=plan_step_id,
                tool_name=ToolName.FETCH_URL_TEXT,
                reason=arguments["reason"],
                url=arguments["url"],
                extractive_goal=arguments.get("extractive_goal"),
            ),
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.TOOL_DECISION,
                summary=arguments["summary"],
            ),
        )

    if tool_name == "request_mcp_echo":
        _require_arguments(arguments, "summary", "reason", "text")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_ECHO,
            text=arguments["text"],
        )

    if tool_name == "request_mcp_read_fixture":
        _require_arguments(arguments, "summary", "reason", "path")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_READ_FIXTURE,
            path=arguments["path"],
        )

    if tool_name == "request_mcp_text_stats":
        _require_arguments(arguments, "summary", "reason", "text")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_TEXT_STATS,
            text=arguments["text"],
            limit=int(arguments.get("limit", 5)),
        )

    if tool_name == "request_mcp_calculate_expression":
        _require_arguments(arguments, "summary", "reason", "expression")
        return _build_mcp_actor_result(
            actor_task=actor_task,
            plan_step_id=plan_step_id,
            arguments=arguments,
            tool_name=ToolName.MCP_LOCAL_TEST_CALCULATE_EXPRESSION,
            expression=arguments["expression"],
        )

    if tool_name == "emit_intermediate_output":
        _require_arguments(arguments, "summary", "output_text", "progress_note")
        # Intermediate output deliberately keeps the current step active.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.PROGRESS_NOTE,
                summary=arguments["progress_note"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=arguments,
                next_action=StepValidationDecision.CONTINUE_ACTING,
                rationale_key="progress_note",
                objective_satisfied=False,
                evidence_sufficient=False,
            ),
        )

    if tool_name == "emit_step_completed":
        _require_arguments(arguments, "output_text")
        completion_arguments = _ensure_completion_arguments(arguments=arguments)
        completion_arguments["summary"] = _sanitize_completion_text(
            str(completion_arguments.get("summary", ""))
        )
        completion_arguments["output_text"] = _sanitize_completion_text(
            str(completion_arguments.get("output_text", ""))
        )
        completion_arguments["completion_rationale"] = _sanitize_completion_text(
            str(completion_arguments.get("completion_rationale", ""))
        )
        completion_arguments = _soften_procedural_implementation_language(
            actor_task=actor_task,
            arguments=completion_arguments,
        )
        completion_arguments = _guard_completion_against_unsupported_precision(
            actor_task=actor_task,
            arguments=completion_arguments,
        )
        next_action = _resolve_completion_next_action(arguments=completion_arguments)
        # Step completion is represented as local output plus positive validation.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.INTERMEDIATE_OUTPUT,
            summary=completion_arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=completion_arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary=completion_arguments["completion_rationale"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=completion_arguments,
                next_action=next_action,
                rationale_key="completion_rationale",
            ),
        )

    if tool_name == "emit_final_output":
        _require_arguments(arguments, "output_text")
        completion_arguments = _ensure_completion_arguments(arguments=arguments)
        completion_arguments["summary"] = _sanitize_completion_text(
            str(completion_arguments.get("summary", ""))
        )
        completion_arguments["output_text"] = _sanitize_completion_text(
            str(completion_arguments.get("output_text", ""))
        )
        completion_arguments["completion_rationale"] = _sanitize_completion_text(
            str(completion_arguments.get("completion_rationale", ""))
        )
        completion_arguments = _soften_procedural_implementation_language(
            actor_task=actor_task,
            arguments=completion_arguments,
        )
        completion_arguments = _guard_completion_against_unsupported_precision(
            actor_task=actor_task,
            arguments=completion_arguments,
        )
        next_action = _resolve_completion_next_action(arguments=completion_arguments)
        # Final output is still subject to the same validation contract as normal steps.
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.FINAL_OUTPUT,
            summary=completion_arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            output_text=completion_arguments["output_text"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.COMPLETION_RATIONALE,
                summary=completion_arguments["completion_rationale"],
            ),
            step_validation=_build_step_validation(
                actor_task=actor_task,
                arguments=completion_arguments,
                next_action=next_action,
                rationale_key="completion_rationale",
            ),
        )

    if tool_name == "emit_ask_clarification":
        _require_arguments(arguments, "summary", "blocking_reason")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.ASK_CLARIFICATION,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason=arguments["blocking_reason"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary=arguments["blocking_reason"],
            ),
        )

    if tool_name == "emit_blocked":
        _require_arguments(arguments, "summary", "blocking_reason")
        recovered_result = _recover_false_blocked_lookup_as_tool_request(
            actor_task=actor_task,
            arguments=arguments,
            plan_step_id=plan_step_id,
        )
        if recovered_result is not None:
            return recovered_result
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.BLOCKED,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason=arguments["blocking_reason"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary=arguments["blocking_reason"],
            ),
        )

    if tool_name == "emit_needs_replan":
        _require_arguments(arguments, "summary", "blocking_reason")
        return ActorResult(
            plan_step_id=plan_step_id,
            result_type=ActorResultType.NEEDS_REPLAN,
            summary=arguments["summary"],
            capability_mode=CapabilityAccessMode.NONE,
            blocking_reason=arguments["blocking_reason"],
            scratchpad_entry=_build_scratchpad_entry(
                actor_task=actor_task,
                entry_type=ScratchpadEntryType.UNCERTAINTY,
                summary=arguments["blocking_reason"],
            ),
        )

    raise ActorExecutionError(f"Actor emitted an unknown runtime tool: {tool_name}")


# Recover model confusion between an empty allowlist and unavailable read-only tools.
def _recover_false_blocked_lookup_as_tool_request(
    *,
    actor_task: ActorTask,
    arguments: dict[str, object],
    plan_step_id: str,
) -> ActorResult | None:
    blocking_reason = str(arguments["blocking_reason"])
    if not _blocked_lookup_is_recoverable(actor_task=actor_task, blocking_reason=blocking_reason):
        return None

    query = _resolve_search_query(actor_task=actor_task, arguments={})
    summary = "Recover blocked source lookup as a read-only web search."
    return ActorResult(
        plan_step_id=plan_step_id,
        result_type=ActorResultType.PROPOSE_ACTION,
        summary=summary,
        capability_mode=CapabilityAccessMode.READ_ONLY,
        external_impact=False,
        tool_request=ToolRequest(
            plan_step_id=plan_step_id,
            tool_name=ToolName.WEB_SEARCH,
            reason=(
                "The actor incorrectly treated runtime-bound read-only tools as unavailable; "
                "the current step requires discovering official sources."
            ),
            query=query,
            max_results=5,
            extractive_goal="Identify official source pages and high-level source evidence for the current step.",
        ),
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.TOOL_DECISION,
            summary=summary,
        ),
    )


# Identify false blocked outcomes where a source lookup should have been requested.
def _blocked_lookup_is_recoverable(*, actor_task: ActorTask, blocking_reason: str) -> bool:
    reason_text = blocking_reason.lower()
    blocked_tool_markers = (
        "no external tool",
        "external tool usage is allowed",
        "external lookup",
        "no prior evidence",
        "without external",
        "cannot identify official",
    )
    if not any(marker in reason_text for marker in blocked_tool_markers):
        return False

    step_text = " ".join(
        part
        for part in (
            actor_task.current_plan_step.objective,
            actor_task.current_plan_step.expected_outcome,
            actor_task.current_plan_step.rationale,
        )
        if part
    ).lower()
    source_need_markers = ("source", "sources", "official", "lookup", "evidence", "references")
    return any(marker in step_text for marker in source_need_markers)


# Build one read-only ActorResult for configured MCP tool requests.
def _build_mcp_actor_result(
    *,
    actor_task: ActorTask,
    plan_step_id: str,
    arguments: dict[str, object],
    tool_name: ToolName,
    text: object | None = None,
    path: object | None = None,
    expression: object | None = None,
    limit: int | None = None,
) -> ActorResult:
    return ActorResult(
        plan_step_id=plan_step_id,
        result_type=ActorResultType.PROPOSE_ACTION,
        summary=str(arguments["summary"]),
        capability_mode=CapabilityAccessMode.READ_ONLY,
        external_impact=False,
        tool_request=ToolRequest(
            plan_step_id=plan_step_id,
            tool_name=tool_name,
            reason=str(arguments["reason"]),
            text=str(text) if text is not None else None,
            path=str(path) if path is not None else None,
            expression=str(expression) if expression is not None else None,
            limit=limit,
        ),
        scratchpad_entry=_build_scratchpad_entry(
            actor_task=actor_task,
            entry_type=ScratchpadEntryType.TOOL_DECISION,
            summary=str(arguments["summary"]),
        ),
    )


# Create one audit-safe scratchpad entry for the current actor turn.
def _build_scratchpad_entry(
    *,
    actor_task: ActorTask,
    entry_type: ScratchpadEntryType,
    summary: str,
) -> ScratchpadEntry:
    return ScratchpadEntry(
        turn_index=actor_task.actor_turn_count,
        plan_step_id=actor_task.current_plan_step.step_id,
        entry_type=entry_type,
        summary=summary,
        evidence_refs=[item.evidence_id for item in actor_task.observed_evidence],
    )


# Build the compact self-validation contract used before step completion.
def _build_step_validation(
    *,
    actor_task: ActorTask,
    arguments: dict[str, object],
    next_action: StepValidationDecision,
    rationale_key: str,
    objective_satisfied: bool | None = None,
    evidence_sufficient: bool | None = None,
) -> StepValidation:
    confidence = arguments.get("confidence", 0.0)
    if not isinstance(confidence, int | float):
        raise ActorExecutionError("Actor validation confidence must be numeric.")

    # Some runtime tools fix these booleans, while completion tools read them from the model.
    resolved_objective_satisfied = (
        bool(arguments.get("objective_satisfied"))
        if objective_satisfied is None
        else objective_satisfied
    )
    resolved_evidence_sufficient = (
        bool(arguments.get("evidence_sufficient"))
        if evidence_sufficient is None
        else evidence_sufficient
    )

    rationale = arguments.get(rationale_key)
    if not isinstance(rationale, str) or not rationale.strip():
        raise ActorExecutionError(f"Actor validation is missing '{rationale_key}'.")

    residual_uncertainty = arguments.get("residual_uncertainty")
    if residual_uncertainty is not None and not isinstance(residual_uncertainty, str):
        raise ActorExecutionError("Actor validation residual_uncertainty must be a string.")

    return StepValidation(
        plan_step_id=actor_task.current_plan_step.step_id,
        objective_satisfied=resolved_objective_satisfied,
        evidence_sufficient=resolved_evidence_sufficient,
        confidence=max(0.0, min(float(confidence), 1.0)),
        next_action=next_action,
        rationale_summary=rationale,
        residual_uncertainty=residual_uncertainty,
    )


# Let runtime guards downgrade optimistic completions into explicit evidence-seeking actions.
def _resolve_completion_next_action(*, arguments: dict[str, object]) -> StepValidationDecision:
    raw_next_action = arguments.get("_guarded_next_action")
    if isinstance(raw_next_action, str):
        try:
            return StepValidationDecision(raw_next_action)
        except ValueError:
            pass
    return StepValidationDecision.COMPLETE_STEP


# Validate that required tool arguments are present and non-empty.
def _require_arguments(arguments: dict[str, object], *required_keys: str) -> None:
    for key in required_keys:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ActorExecutionError(f"Actor tool call is missing required string argument '{key}'.")


# Preserve the internal completion contract when provider tool arguments are incomplete.
def _ensure_completion_arguments(*, arguments: dict[str, object]) -> dict[str, object]:
    normalized_arguments = dict(arguments)
    summary = normalized_arguments.get("summary")
    output_text = normalized_arguments.get("output_text")
    rationale = normalized_arguments.get("completion_rationale")

    # Completion summary is audit-facing, so derive it from rationale before user output.
    if not isinstance(summary, str) or not summary.strip():
        if isinstance(rationale, str) and rationale.strip():
            normalized_arguments["summary"] = rationale
        elif isinstance(output_text, str) and output_text.strip():
            normalized_arguments["summary"] = output_text

    summary = normalized_arguments.get("summary")
    if isinstance(rationale, str) and rationale.strip():
        return normalized_arguments

    # Prefer the normalized summary because it is already intended as an audit-safe explanation.
    if isinstance(summary, str) and summary.strip():
        normalized_arguments["completion_rationale"] = summary
        return normalized_arguments

    if isinstance(output_text, str) and output_text.strip():
        normalized_arguments["completion_rationale"] = output_text
        return normalized_arguments

    return normalized_arguments


# Strip internal trace markers so user-facing completions stay clean.
def _sanitize_completion_text(text: str) -> str:
    cleaned = re.sub(r"【[^】]+】", "", text)
    return " ".join(cleaned.split())


# Build a conservative search query if a JSON actor omits the explicit query field.
def _resolve_search_query(*, actor_task: ActorTask, arguments: dict[str, object]) -> str:
    query = arguments.get("query")
    if isinstance(query, str) and query.strip():
        return query

    # Prefer the current step objective because the actor is scoped to that semantic step.
    fallback_parts = [
        actor_task.current_plan_step.objective,
        actor_task.task_spec.context,
        "official sources",
    ]
    return " ".join(part.strip() for part in fallback_parts if part and part.strip())


# Soften overstated enforcement phrasing when the evidence is procedural rather than primary legal.
def _soften_procedural_implementation_language(
    *,
    actor_task: ActorTask,
    arguments: dict[str, object],
) -> dict[str, object]:
    if not _has_procedural_implementation_evidence(actor_task=actor_task):
        return arguments

    softened_arguments = dict(arguments)
    for key in ("summary", "output_text", "completion_rationale"):
        value = softened_arguments.get(key)
        if isinstance(value, str) and value.strip():
            softened_arguments[key] = _rewrite_procedural_enforcement_phrasing(value)
    return softened_arguments


# Detect implementation/procedural evidence without tying the behavior to a specific task.
def _has_procedural_implementation_evidence(*, actor_task: ActorTask) -> bool:
    procedural_markers = (
        "procedural",
        "implementation",
        "implementing regulation",
        "implementation and enforcement",
        "enforcement procedure",
    )
    return any(
        (
            any(marker in evidence.excerpt.lower() for marker in procedural_markers)
            or any(marker in evidence.title.lower() for marker in procedural_markers)
            or any(marker in evidence.locator.lower() for marker in procedural_markers)
        )
        for evidence in actor_task.observed_evidence
        if _is_direct_fact_support_evidence(evidence)
    )


# Keep procedural source summaries on the implementation side of the wording boundary.
def _rewrite_procedural_enforcement_phrasing(text: str) -> str:
    rewritten = re.sub(
        r"\bestablishes enforcement by the European Commission\s*\(([^)]+)\)",
        r"describes implementation and enforcement through the European Commission (\1)",
        text,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(
        r"\bsole enforcer of the DMA\b",
        "responsible for implementation and enforcement of the DMA",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(
        r"\benforces the rules\b",
        "describes implementation and procedural enforcement",
        rewritten,
        flags=re.IGNORECASE,
    )
    return rewritten


# Build the production actor used by the live Layer 2 runtime.
def build_default_actor() -> ActorPort:
    provider = _resolve_actor_provider()
    if provider == "openai":
        return OpenAIActor()
    if provider == "openrouter":
        return OpenRouterActor()
    raise RuntimeError(
        "Unsupported LAYER2_ACTOR_PROVIDER. Use 'openrouter' for temporary actor tests or 'openai'."
    )


# Resolve only the actor provider so planner and replanner can stay on OpenAI.
def _resolve_actor_provider() -> str:
    env_file_values = _load_env_file_values()
    return os.getenv(
        "LAYER2_ACTOR_PROVIDER",
        env_file_values.get("LAYER2_ACTOR_PROVIDER", "openrouter"),
    ).strip().lower()


def _load_env_file_values() -> dict[str, str]:
    # Load simple KEY=VALUE pairs from the supported .env file locations.
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


def _candidate_env_paths() -> list[Path]:
    # Search both repository root and src-level env files for local development.
    repo_root = Path(__file__).resolve().parents[2]
    return [
        repo_root / ".env",
        repo_root / "src" / ".env",
    ]


__all__ = [
    "ActorExecutionError",
    "OpenAIActor",
    "OpenAIActorSettings",
    "OpenRouterActor",
    "OpenRouterActorSettings",
    "build_actor_system_prompt",
    "build_actor_user_prompt",
    "build_default_actor",
]
