from __future__ import annotations

import ast
import json
import operator
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


# Read one MCP-style framed JSON-RPC message from stdin.
def read_message() -> dict[str, Any] | None:
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header += chunk

    headers, remaining = header.split(b"\r\n\r\n", 1)
    content_length = parse_content_length(headers)
    body = remaining
    while len(body) < content_length:
        chunk = sys.stdin.buffer.read(content_length - len(body))
        if not chunk:
            return None
        body += chunk

    return json.loads(body.decode("utf-8"))


# Extract Content-Length from the incoming MCP frame.
def parse_content_length(headers: bytes) -> int:
    for raw_line in headers.split(b"\r\n"):
        line = raw_line.decode("ascii", errors="replace")
        if line.lower().startswith("content-length:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("Missing Content-Length")


# Write one MCP-style framed JSON-RPC response to stdout.
def write_message(payload: dict[str, Any]) -> None:
    raw_payload = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw_payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw_payload)
    sys.stdout.buffer.flush()


# Run the minimal MCP request loop used by gateway tests.
def main() -> None:
    while True:
        message = read_message()
        if message is None:
            return

        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "local_test_mcp", "version": "1.0.0"},
                    },
                }
            )
            continue

        if method == "tools/call":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": call_tool(
                        tool_name=str(params.get("name", "")),
                        arguments=params.get("arguments") or {},
                    ),
                }
            )
            continue

        write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        )


# Dispatch one allowlisted local test tool.
def call_tool(*, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if tool_name == "echo":
            return text_result(str(arguments.get("text", "")))
        if tool_name == "read_fixture":
            return text_result(read_fixture(str(arguments.get("path", ""))))
        if tool_name == "text_stats":
            return text_result(build_text_stats(arguments))
        if tool_name == "calculate_expression":
            value = evaluate_expression(str(arguments.get("expression", "")))
            return text_result(str(value))
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]}


# Build one MCP text content result.
def text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


# Read only fixture files beneath this tests/fixtures directory.
def read_fixture(path_value: str) -> str:
    fixture_root = Path(__file__).resolve().parent
    candidate = (fixture_root / path_value).resolve()
    if fixture_root not in candidate.parents and candidate != fixture_root:
        raise ValueError("Fixture path is outside the allowed fixture directory.")
    if candidate.name.startswith("."):
        raise ValueError("Hidden fixture files are blocked.")
    return candidate.read_text(encoding="utf-8")


# Produce compact deterministic text statistics.
def build_text_stats(arguments: dict[str, Any]) -> str:
    text = str(arguments.get("text", ""))
    limit = int(arguments.get("limit", 5))
    words = [word.lower() for word in text.split() if word.strip()]
    top_words = Counter(words).most_common(max(1, min(limit, 20)))
    payload = {
        "characters": len(text),
        "lines": len(text.splitlines()) or (1 if text else 0),
        "words": len(words),
        "top_words": top_words,
    }
    return json.dumps(payload, sort_keys=True)


# Evaluate arithmetic using a small AST allowlist instead of eval.
def evaluate_expression(expression: str) -> int | float:
    if not expression.strip():
        raise ValueError("Expression cannot be empty.")
    tree = ast.parse(expression, mode="eval")
    return evaluate_node(tree.body)


# Recursively evaluate only numeric literals and arithmetic operators.
def evaluate_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPERATORS:
        left = evaluate_node(node.left)
        right = evaluate_node(node.right)
        return ALLOWED_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_OPERATORS:
        return ALLOWED_OPERATORS[type(node.op)](evaluate_node(node.operand))
    raise ValueError("Expression contains unsupported syntax.")


if __name__ == "__main__":
    main()
