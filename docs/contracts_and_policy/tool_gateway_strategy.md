# Tool Gateway Strategy

## Position

The Tool Gateway is the governed capability boundary of the autonomous runtime.
The user-facing system remains task-level and autonomous, while tool execution is internally typed, allowlisted, observable, and normalized into evidence.

MCP is now used as an operational backend for tool calls, but it is not exposed directly to the actor.
The actor emits `ToolRequest`; the gateway decides whether the request can be executed and how it maps to a configured backend.

## Current Baseline

Supported backends:

- `mcp`: configured local MCP servers with explicit allowlists
- `openai`: OpenAI-backed `web_search`
- `http_fetch`: controlled URL text extraction
- `local_runtime`: configuration-registered controlled documents and CSV snapshots

Current local-runtime tools:

- `controlled_document.read`
- `controlled_dataset.read`

Their source registry is `configs/layer2_controlled_corpus.json`. The actor may
select only a registered stable ID; it cannot provide a filesystem path, SQL
statement, or generic query language. Resources are confined to the tracked
`evaluation_corpus/` directory and observations keep a `controlled-*://` locator.

Current MCP test tools:

- `mcp.local_test.echo`
- `mcp.local_test.read_fixture`
- `mcp.local_test.text_stats`
- `mcp.local_test.calculate_expression`

The MCP path is intentionally constrained:

- no free tool discovery is exposed to the actor
- no write-like MCP tools are enabled
- no credentials are present in prompts or `ToolRequest`
- every MCP result is converted into `ToolExecutionResult`
- adapters return minimal `ToolObservation` records containing concrete content and an optional locator

## Authentication Boundary

Authentication and authorization are not implemented in this slice.
They are an explicit future hardening layer above the MCP backend.

The intended future model is:

```text
Tool Gateway
  -> runtime identity / authn
  -> access group / authz
  -> configured MCP server
  -> allowlisted tool
```

This keeps the current PoC useful without pretending that local allowlists are a complete enterprise security model.
The current baseline still provides a meaningful safety floor through configuration, read-only capability constraints, parameter validation, and evidence normalization.

## Separate external execution registry

The Tool Gateway registry remains actor-facing and read-only. Mutating operations use
`configs/layer2_external_capabilities.json`, consumed only after OPA/HITL authorization:

```text
actor -> ToolRequest -> read-only Tool Gateway registry -> evidence

authorized ExternalExecutionRequest
  -> external capability registry
  -> exact MCP server + exact allowlisted tool
  -> external system
  -> ExternalExecutionReceipt
```

The external registry records connector, server, tool, input schema/version,
consequence profile, timeout, idempotency support, bounded retry, and defensive
constraints. Server commands and environment-key allowlists are administrator
configuration, never actor text.

Current adapters are SMTP email and profile-bound SQLite statements, disabled by
default. Database input is a registered profile id, one AST-validated statement,
separate parameters, and an idempotency key; it never contains an actor-provided DSN.
Email credentials and sender identity also remain adapter environment configuration.

## Thesis Framing

Modern agent systems may use built-in tools, function calling, native APIs, or MCP servers.
This architecture adopts MCP as an interoperability layer behind a governed Tool Gateway.
The agent remains autonomous at the task interface, while external capability use remains internally controllable, auditable, and compatible with future human oversight and authorization layers.
