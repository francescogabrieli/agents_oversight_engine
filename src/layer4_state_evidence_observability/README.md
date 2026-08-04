# Layer 4 — State / Evidence / Observability

This package implements the architecture macro-block described in
`docs/architecture/architecturev3.md` as **4. State, Evidence & Observability**.

- `observability/` builds immutable trace and replay projections.
- `behavioral/` owns the non-authoritative historical read model.
- `oversight/` contains persisted oversight assessment history contracts.
- `infrastructure/` owns checkpoint, runtime-artifact, retention, and review
  decision transport persistence.

Layer 4 records and projects runtime information. It is not a second policy
decision point and cannot authorize runtime continuation or external execution.

