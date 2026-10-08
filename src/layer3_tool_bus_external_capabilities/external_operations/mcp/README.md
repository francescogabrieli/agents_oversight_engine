# MCP external execution adapter

This package implements the infrastructure side of
`ExternalOperationExecutorPort` with the official MCP Python SDK. The connection
pool is created by the Layer 2 composition root; it is not a mutable singleton.
The dependency is pinned as `mcp>=1.27,<2` in `requirements-production.txt`;
upstream provenance is https://github.com/modelcontextprotocol/python-sdk.

The pool supports configured `stdio` servers, reuses initialized sessions,
serializes calls per session, enforces server/tool allowlists and timeouts, drops
interrupted sessions, and closes child processes explicitly. Only environment
keys listed by the server definition are forwarded, and server stderr is not
copied into runtime traces.

`MCPExternalOperationExecutor` receives an already authorized
`ExternalExecutionRequest`, validates the registry mapping and typed payload,
calls the exact configured tool, and converts its closed response into an
`ExternalExecutionReceipt`. MCP is a transport adapter, never a governance
authority.

## Configuration

The default registry disables every external capability. Set
`LAYER2_EXTERNAL_CAPABILITY_CONFIG_PATH` to an explicit opt-in config based on
`configs/layer2_external_capabilities.example.json`. Each stdio definition controls:

- fixed command and arguments;
- enabled state and exact tool allowlist;
- timeout and working directory;
- the only environment variable names forwarded to the child process.

When `working_directory` is omitted, the registry resolves the repository's `src`
directory with native filesystem separators. An explicit value remains unchanged
apart from `{repo_root}` expansion.

The pool maintains one lazily initialized session per server, serializes calls on that
session, and uses per-server creation locks so concurrent graph workers cannot race two
processes into the same slot. Broken or timed-out sessions are removed and their process
is closed.

The executor validates the typed argument projection against the registry schema before
the MCP call and validates the closed `external_execution.tool_response.v1` response
after it. A timeout after invocation begins becomes `indeterminate`, never retryable.
