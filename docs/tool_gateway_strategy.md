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
- `local_runtime`: reserved for future local read-only tools

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
- every observed tool result can become `ObservedEvidence`

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

## Thesis Framing

Modern agent systems may use built-in tools, function calling, native APIs, or MCP servers.
This architecture adopts MCP as an interoperability layer behind a governed Tool Gateway.
The agent remains autonomous at the task interface, while external capability use remains internally controllable, auditable, and compatible with future human oversight and authorization layers.
