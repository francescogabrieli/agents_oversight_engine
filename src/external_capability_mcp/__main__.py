from __future__ import annotations

import argparse

from .database_server import build_database_server
from .email_server import build_email_server


# Parse one explicit server family so a process exposes no unrelated tools.
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a governed external-capability MCP server.")
    parser.add_argument("server", choices=("communication", "database"))
    return parser.parse_args()


# Start exactly one FastMCP stdio server selected by deployment configuration.
def main() -> None:
    args = _parse_args()
    server = (
        build_email_server()
        if args.server == "communication"
        else build_database_server()
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
