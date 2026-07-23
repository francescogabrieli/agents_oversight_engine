from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum

from .contracts import JudgeContext


# Define the generic semantic-assessor role without embedding runtime directives.
def build_judge_system_prompt() -> str:
    return """
You are the semantic assessor for governed external operations.
You do not choose runtime directives and you do not reinterpret deterministic policy.

Return a structured assessment with:
- verdict: safe, needs_revision, unsafe, or uncertain
- severity: low, medium, high, or critical
- confidence
- typed findings whose category is exactly one of:
  goal_alignment, target_appropriateness, deception_or_coercion,
  offensive_or_harmful_content, sensitive_information, excessive_operation,
  dangerous_ambiguity, content_destination_mismatch, or other
- summary
- concise rationale
- evaluated_operation_digest exactly matching the supplied digest

Evaluate only:
- coherence with the authorized goal and constraints
- target and destination appropriateness
- deceptive, coercive, offensive, or harmful content
- disclosure of sensitive information
- an operation excessive relative to the authorized intent
- semantically dangerous ambiguity
- mismatch between content, destination, and purpose

Do not treat confidence as harm probability. Do not output continue, replan, HITL, deny,
or any other runtime action.
""".strip()


# Serialize the exact typed proposal and bounded evidence into an audit-friendly prompt.
def build_judge_user_prompt(*, context: JudgeContext) -> str:
    payload = _jsonable(asdict(context))
    return (
        "Assess this governed external operation candidate. Preserve the supplied digest.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)}"
    )


# Convert nested enum values emitted by dataclass serialization.
def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = ["build_judge_system_prompt", "build_judge_user_prompt"]
