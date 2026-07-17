# Human-in-the-loop capability

This package owns typed review requests, `interrupt()` / `Command(resume=...)`
handling, and dashboard-facing read models. LangGraph checkpoints remain the
only source for resuming an interrupted execution.

Dashboard projections live in `hitl.dashboard`; the root `hitl_dashboard.py`
module remains a compatibility facade for existing callers.
