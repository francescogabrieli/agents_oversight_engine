from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP


# Build a real official-SDK server used only for transport lifecycle tests.
def build_test_server() -> FastMCP:
    server = FastMCP("external-execution-integration-fixture")

    @server.tool(name="slow_operation")
    def slow_operation(delay_seconds: float) -> dict[str, object]:
        """Wait long enough for the client timeout path to close this session."""
        time.sleep(delay_seconds)
        return {"completed": True}

    return server


# Run the fixture only when launched by the configured stdio transport.
def main() -> None:
    build_test_server().run(transport="stdio")


if __name__ == "__main__":
    main()
