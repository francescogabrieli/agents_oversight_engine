# External operations

This capability owns the small canonical contract used whenever a runtime action may
cross the local boundary. `contracts.py` defines immutable proposals and typed payloads;
`normalizers.py` derives boundary, effect, destination, preview, and digest from runtime
actions or registry facts; `execution.py` exposes the digest-bound execution port.

The contracts and normalizers are framework-independent. SQL normalization uses
`sqlglot` only to parse a single statement and derive AST-owned statement facts. Actual
execution lives below the port in `mcp/`; domain and runtime code do not import the MCP
SDK, SMTP, or database clients.

For communication synthesis, the actor emits the content through a dedicated typed
action. The normalizer therefore receives channel, recipient, subject, and body as
separate fields; the actor cannot supply boundary, effect, destination class, or digest.
The canonical body is newline-normalized but never length-truncated: Judge, OPA, HITL,
and the executor bind the same complete payload and digest. Only `payload_preview` is
bounded for operator-facing display. The email capability schema and SMTP adapter share
an explicit 50,000-character defensive ceiling; oversized content is rejected rather
than silently rewritten.

To extend the capability, add a bounded payload dataclass and a capability-specific
normalizer, map its normalized facts in OPA static policy, register the exact server/tool
mapping, and add a typed MCP argument projection. Do not add semantic harm classification
to a normalizer; that belongs to the judge.

## Execution boundary

`ExecutionIntent` is canonical in Layer 1 and defaults to `proposal_only` for old
payloads/checkpoints. It records what the user asked for; it is not authority.
`execute_after_governance` may enter `READY_EXTERNAL_EXECUTION` only after OPA has
allowed the exact digest or HITL has approved that exact digest.

`ExternalExecutionRequest` carries the immutable proposal, current and authorized
digests, stable idempotency key, intent, authorization source, reviewer, and OPA
reference. `ExternalExecutionReceipt` distinguishes `executed`,
`rejected_before_execution`, `failed`, and `indeterminate`. An indeterminate result is
never retried automatically.

`registry.py` is intentionally separate from the read-only Tool Gateway registry. This
prevents mutating MCP tools from appearing in actor tool binding. The current productive
mappings are:

- `communication.email.send` → MCP `communication/send_email`;
- `database.statement.execute` → MCP `database/execute_statement`.

Both are disabled in the default config. Copy
`configs/layer2_external_capabilities.example.json`, configure secrets through
environment variables, and set `LAYER2_EXTERNAL_CAPABILITY_CONFIG_PATH`. If enabled
mappings exist, composition selects `MCPExternalOperationExecutor`; otherwise it uses
the fail-closed `DeferredExternalOperationExecutor`.

Client and server maintain durable idempotency ledgers. The guarantee is at-most-once
per stable attempt key, not universal exactly-once delivery. A deterministic retry uses
a new bounded attempt scope; an unresolved key returns `indeterminate`.
