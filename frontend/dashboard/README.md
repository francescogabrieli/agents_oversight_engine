# HITL dashboard

The dashboard presents request-bound human decisions; the runtime and OPA remain
the authority for allowed actions, candidate digests, approval and continuation.

## Review workflow

- Read the selected reason, the agent's message and the current step.
- Inspect `ProposalPanel` for email destination/subject/body or the structured
  operation payload (including SQL, parameters and profile when provided).
- Select a decision explicitly; none is preselected. Editing returns a new proposal
  to governance, not an immediate execution authorization.
- Equivalent polling responses preserve edits. A changed request or candidate
  resets the form and requires a new selection. Errors suspend submission while
  retaining the last received context and local edits.
- `DecisionDeliveryStatus` distinguishes registered, claimed, consumed and failed
  delivery. A consumed decision is not proof of successful task completion.
- Recently submitted decisions remain non-interactive for the existing two-minute
  acknowledgement window. The runtime terminal remains the execution monitor.
- Sources expose the available bounded excerpt, not an invented complete document.
  `EvidenceComparisonPanel` displays the recorded actor output beside a selectable
  source, retaining selection across equivalent polls. New snapshots preserve source
  paragraphs up to 12,000 characters and explicitly report truncation, missing
  observations and retrieval provenance. Historical absence remains unknown; when
  an old snapshot contains a governed draft, its fallback is labelled as a proposal.
  Flagged values show the actor excerpt beside candidate source passages, with exact-text
  highlighting only. Matches are explicitly unverified and instructions explain checking
  subject, threshold, unit and period in the linked source. No numerical or semantic
  equivalence is inferred by the frontend (`LiteralExcerpt` owns exact highlighting).
  Only HTTP(S) locators become external links; controlled identifiers remain text.
- `FlaggedEvidencePanel` owns the existing literal comparison and parsing. Older
  payloads containing tokens without passages show a missing-context explanation.
- Clarification submits the operator's answer and resumes the reviewed step. The
  reconciliation receipt appears before the decision; completed/aborted/running
  threads without a pending request expose status and decision history only.
- Initial loading failures never appear as an empty queue. Missing snapshots have
  an explicit unavailable state. The acknowledgement dialog supports keyboard focus
  and Escape; evidence columns stack on narrow screens.

## Extension and verification

Keep decision/reason/status labels in the existing presentation modules. New
operation families use the typed `HumanReviewPendingRequest` in `types.ts` and the
proposal panel; do not derive new permissions in the UI. `SourceReference` owns
safe-link rendering. `usePolledResource` prevents overlapping and obsolete polls.

Run `npm test -- --run` and `npm run build` here. Tests mock API responses and do not
start LangGraph, send mail or write evaluation/runtime databases. Serve the generated
`dist` through `src/main_layer2_dashboard.py`; browser previews must use isolated
fixtures and must not submit decisions to real threads.

See [UX audit and regression matrix](../../docs/dashboard/ux_review_20260928.md)
for scope, limitations and validation evidence.
