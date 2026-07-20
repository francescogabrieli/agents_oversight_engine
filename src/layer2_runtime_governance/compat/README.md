# Layer 2 compatibility aliases

This package is a temporary migration surface for callers that cannot yet use
the canonical capability packages. New production code must never import from
`compat`; it must use `execution`, `tools`, `hitl.dashboard`, or
`infrastructure.runtime_artifacts` directly.

The root-level `state.py`, `governance.py`, and `replanning.py` are deliberately
outside this package because old LangGraph checkpoints reference those qualified
type paths during deserialization.
