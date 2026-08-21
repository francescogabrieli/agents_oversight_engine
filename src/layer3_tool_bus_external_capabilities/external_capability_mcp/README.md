# Governed external capability MCP servers

This deployable package is the trust boundary behind the Layer 2 external
executor. It exposes only two typed tools over the official MCP Python SDK:

- `send_email` uses an explicitly configured SMTP account and sender identity;
- `execute_statement` uses an allowlisted database profile and currently supports
  SQLite with a single parameterized statement and explicit transaction.

The servers do not evaluate policy, select capabilities, accept shell commands,
or receive credentials in tool arguments. Authentication and connection identity
come only from process environment or injected configuration. A durable local
idempotency ledger prevents a repeated key from silently repeating an operation;
an unresolved prior attempt is returned as `indeterminate`.

The MCP SDK supplies transport/session framing only. The child process is trusted as a
connector inside the configured infrastructure boundary; OPA/HITL authorization remains
in the parent LangGraph runtime. SMTP/database authentication is provider-specific and
is injected into the child environment through the server's explicit environment-key
allowlist.

Run one server directly:

```powershell
.\.venv\Scripts\python.exe -m layer3_tool_bus_external_capabilities.external_capability_mcp communication
.\.venv\Scripts\python.exe -m layer3_tool_bus_external_capabilities.external_capability_mcp database
```

Normally the composition-root-owned MCP pool starts these commands from the
external capability registry. Add a new capability by creating a bounded server
tool, registering the exact server/tool mapping, and adding a typed payload
projection in the Layer 2 MCP executor.

## Environment

Communication:

- `LAYER2_SMTP_HOST`, `LAYER2_SMTP_PORT`;
- `LAYER2_SMTP_SENDER`;
- optional `LAYER2_SMTP_USERNAME` and `LAYER2_SMTP_PASSWORD` as a pair;
- `LAYER2_SMTP_USE_TLS`, `LAYER2_SMTP_USE_SSL`;
- `LAYER2_SMTP_TIMEOUT_SECONDS`.

Database:

- `LAYER2_DATABASE_PROFILES_CONFIG_PATH`;
- each profile's `dsn_env`, also named in the MCP server environment allowlist.

Shared:

- `LAYER2_EXTERNAL_SERVER_LEDGER_PATH`.

The database implementation currently supports SQLite profiles only. It uses an
explicit transaction, AST single-statement validation, named parameters, busy timeout,
and rollback on deterministic database failure. For an allowed `SELECT`, the response
contains the column names, total row count, and at most 100 result rows alongside the
transaction reference; non-`SELECT` statements return the affected row count. SMTP
returns provider acceptance and a stable Message-ID; partial recipient acceptance or
lost confirmation is `indeterminate`.
