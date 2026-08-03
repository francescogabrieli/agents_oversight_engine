# Human-in-the-loop capability

This package owns typed review requests, the durable decision-inbox port,
`interrupt()` / `Command(resume=...)` handling, and dashboard-facing read models.
The dashboard is decision ingress only: it validates and enqueues a request-bound
command. The leased runtime owner consumes that command and resumes the graph in
its original process. LangGraph checkpoints remain the only source for resuming
an interrupted execution; the inbox is transport and delivery audit, not graph
state.

The CLI heartbeats its owner lease while resume work is in flight. A replacement
terminal may use `--attach-thread` to recover only a checkpoint still waiting on
the exact request and reclaim the same command after an interrupted delivery. The
included SQLite adapter is local-PoC infrastructure; multi-host deployments must
provide a shared transactional adapter for `ReviewDecisionInboxPort`.

Dashboard projections live in `hitl.dashboard`; the root `hitl_dashboard.py`
module remains a compatibility facade for existing callers.

Review actions are reason-scoped. Unsafe/uncertain Judge review exposes
modify/replan/abort; capability approval exposes approve/modify/abort;
clarification and indeterminate-receipt reconciliation expose only applicable
typed actions. Modification requires a typed valid edit at submit time.
Requests contain operation id, digest, target, proposal summary, Judge findings,
and the typed proposal where applicable. Approval is valid only for the exact
current digest; editing creates a new immutable digest, rebinds the pending
completion candidate and Evidence selection, clears prior approval/Judge
authority, and routes to `READY_GOVERNANCE` before any execution seam.
Read-only approval rejects an unchanged mutating proposal.
