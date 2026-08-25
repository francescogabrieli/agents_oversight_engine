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
The bundle intentionally does not expose the Tool Bus `retrieval_kind`; the Judge
receives the bounded observation content and provenance identifiers, while retrieval
classification remains an Evidence/governance fact outside this semantic contract.

The runtime deterministically materializes those observations as a typed
`judge_evidence_bundle.v1`. The same JSON object is embedded in the Judge request and
persisted under the run artifacts for audit and replay. The model never searches the run
or chooses its own evidence perimeter. Each model-visible observation exposes one
citable identifier, `evidence_id`; content digests remain runtime-only binding metadata.
All structured reference fields are validated against the bundle's
`allowed_evidence_ids`. One invalid structured response receives a single local protocol
correction; retry exhaustion remains unavailable and fails closed through OPA.

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

The active provider prompt is `external_operation_judge_v11`. It is already the
refined semantic contract for the current Evidence boundary and is intentionally not
changed while Evidence attribution is being stabilized. The versioned
`tests/fixtures/judge_prompt_contract_golden_set.json` freezes its structural
requirements and exclusions (including the projection perimeter), without pretending
to be a model-verdict golden set. Provider-output goldens remain a separate follow-up
after the Evidence baseline is stable, so a future regression remains attributable.

Evidence-base completeness is explicit: serialization cannot silently change the
epistemic perimeter. An incomplete base leads to `uncertain`, not invented unsupported
claims. Base completeness means that every canonical observation at the boundary is
represented; it is not made false merely because one represented observation reports
bounded extraction or truncated content. Per-observation incompleteness remains explicit
and can make the affected claim `context_incomplete`/`uncertain`. See
[the research basis](../../../docs/governance_pillars_research_basis.md).
