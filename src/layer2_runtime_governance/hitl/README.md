# Human-in-the-loop capability

This package owns typed review requests, `interrupt()` / `Command(resume=...)`
handling, and dashboard-facing read models. LangGraph checkpoints remain the
only source for resuming an interrupted execution.

Dashboard projections live in `hitl.dashboard`; the root `hitl_dashboard.py`
module remains a compatibility facade for existing callers.

Every review exposes the same seven decisions, including
`modify_and_continue`. Modification still requires a typed valid edit at submit
time. Requests contain operation id, digest, target, proposal summary, judge
findings, and the typed proposal where applicable. Approval is valid only for
the exact current digest; editing creates a new immutable digest, clears prior
approval/judge authority, and routes to `READY_GOVERNANCE` before any execution
seam. Read-only approval rejects an unchanged mutating proposal.
