# Semantic judge capability

This package owns the typed semantic assessor for governed external execution
candidates. OPA can admit one only from an exact `STEP_COMPLETED`; partial
`PROPOSE_ACTION` output is never a semantic execution candidate. OPA alone decides
whether the assessor is required. The assessor receives a
`JudgeContext` containing the authoritative task, complete semantic plan, completed-step
summaries, exact immutable proposal and digest, and every canonical `ToolObservation`
available at the evaluated boundary. Observations are bounded typed projections, not raw
tool transcripts. `selected_by_actor` records declared reliance but never limits the
evidence the Judge can examine. Process narration and execution counters are excluded.

It returns status, `safe|needs_revision|unsafe|uncertain`, severity, confidence,
typed findings, claim-level grounding (`supported_by_selected`,
`supported_but_not_selected`, `unsupported`, `contradicted`, `context_incomplete`),
rationale, evaluated operation/evidence-snapshot/evidence-selection digests, and
model/prompt metadata. It never
returns `continue`, replan, evidence, HITL, or deny directives and never receives
behavioral, dynamic-policy, or operational-risk assessments.

Provider structured output uses the canonical enum values directly. Free-form
finding labels are invalid instead of being guessed or silently normalized, and
the evaluated digest must be exactly one lowercase SHA-256 value.

`contracts.py`, `prompts.py`, and `service.py` own the bounded public capability.
Provider failure, invalid output, non-invocation, or mismatch in any of the three
bindings is unavailable/invalid; when the pillar is required OPA fails closed to
HITL.

Evidence-base completeness is explicit: serialization cannot silently change the
epistemic perimeter. An incomplete base leads to `uncertain`, not invented unsupported
claims. See [the research basis](../../../docs/governance_pillars_research_basis.md).
