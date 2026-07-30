# Governance signal services

This package produces typed, inspectable facts for OPA; it never selects a runtime
directive.

- **Evidence Integrity & Provenance** verifies selected-reference identity,
  uniqueness, content, lineage, step association, title-only fragility, and the
  immutable candidate/snapshot/selection digest binding.
- **Trajectory Health** observes state deltas, replan basis, rejected payloads,
  revisions, recovery, and stuckness. Point confidence is contextual uncertainty.
- **Governed External Semantic Judge** is OPA-admitted only for an exact
  `STEP_COMPLETED` governed-external candidate and assesses its digest against the
  complete bounded observation universe.

OPA is the sole final directive authority. See
[governance_pillars_research_basis.md](../../../docs/governance_pillars_research_basis.md).
