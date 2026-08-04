# Layer 3 — Tool Bus / External Capabilities

This package implements the architecture macro-block described in
`architecture/architecturev3.md` as **3. Tool Bus / External Capabilities**.

- `tool_contract.py` contains the typed capability boundary used by Layer 2.
- `tools/` contains the actor-facing read-only Tool Gateway and adapters.
- `external_operations/` contains the separate governed-external registry,
  normalizers, idempotency ledger, and connector adapters.
- `external_capability_mcp/` contains the configured productive MCP servers for
  the mutating email and database capabilities.

Layer 3 executes or normalizes capability calls. It does not decide policy,
route the LangGraph graph, or resume human review.
