# Semantic judge capability

This package owns the typed semantic assessor for governed external execution
candidates. OPA alone decides whether it is required. The assessor receives a
`JudgeContext` containing the authorized task, evaluated actor step, exact immutable
proposal and digest, relevant evidence/support metadata, and target facts.

It returns status, `safe|needs_revision|unsafe|uncertain`, severity, confidence,
typed findings, rationale, evaluated digest, and model/prompt metadata. It never
returns `continue`, replan, evidence, HITL, or deny directives and never receives
behavioral, dynamic-policy, or operational-risk assessments.

Provider structured output uses the canonical enum values directly. Free-form
finding labels are invalid instead of being guessed or silently normalized, and
the evaluated digest must be exactly one lowercase SHA-256 value.

`contracts.py`, `prompts.py`, and `service.py` own the bounded public capability.
Provider failure, invalid output, non-invocation, or digest mismatch is unavailable/
invalid; when the pillar is required OPA fails closed to HITL.
