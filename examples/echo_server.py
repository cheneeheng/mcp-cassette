"""A tiny MCP-style stdio server, pure standard library, no ``mcp`` SDK.

It speaks newline-delimited JSON-RPC 2.0 over stdin/stdout — exactly the transport
mcp-cassette records and replays — so it doubles as a self-contained "real server" for
the examples. Two tools are exposed:

* ``echo`` — returns the given text, plus a per-call random ``token``. The token is the
  non-deterministic bit: recorded once, and on replay mcp-cassette returns that same
  recorded token every time, which is the whole point of a cassette.
* ``add`` — returns the sum of two integers.
* ``summarize`` — asks the *client* to sample a summary mid-call
  (``sampling/createMessage``, a server-initiated request), then returns it. Needs a
  bidirectional transport, so it works over stdio but not through
  ``echo_http_server.py``.

Run directly over stdio::

    python examples/echo_server.py
"""

from __future__ import annotations

import itertools
import json
import secrets
import sys
from collections.abc import Callable
from typing import Any

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back, with a random per-call token.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "summarize",
        "description": "Summarize text by asking the client's LLM (sampling).",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]

Converse = Callable[[dict[str, Any]], dict[str, Any]]

_sampling_ids = itertools.count(1)


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text_content(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _summarize(msg_id: Any, text: str, converse: Converse | None) -> dict[str, Any]:
    """Ask the client to sample a summary, then wrap its answer in a tool result."""
    if converse is None:
        return _error(msg_id, -32603, "summarize needs a bidirectional transport")
    answer = converse(
        {
            "jsonrpc": "2.0",
            "id": f"s{next(_sampling_ids)}",
            "method": "sampling/createMessage",
            "params": {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": f"Summarize: {text}"},
                    }
                ],
                "maxTokens": 64,
            },
        }
    )
    content = (answer.get("result") or {}).get("content") or {}
    summary = content.get("text") if isinstance(content, dict) else None
    return _result(msg_id, _text_content(f"summary: {summary or '(no answer)'}"))


def handle(
    request: dict[str, Any], converse: Converse | None = None
) -> dict[str, Any] | None:
    """Handle one JSON-RPC request; return a response or ``None`` for a notification.

    Args:
        request: The decoded JSON-RPC object.
        converse: Optional callback for server-initiated requests: sends the given
            request to the client and blocks until its response arrives. Without it
            the ``summarize`` tool answers with an error.
    """
    method = request.get("method")
    msg_id = request.get("id")
    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-example", "version": "1.0.0"},
            },
        )
    if method == "notifications/initialized":
        return None  # notification: no response
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            token = secrets.token_hex(4)
            return _result(msg_id, _text_content(f"{args.get('text', '')} [{token}]"))
        if name == "add":
            total = int(args.get("a", 0)) + int(args.get("b", 0))
            return _result(msg_id, _text_content(str(total)))
        if name == "summarize":
            return _summarize(msg_id, str(args.get("text", "")), converse)
        return _error(msg_id, -32602, f"unknown tool: {name}")
    if msg_id is None:
        return None  # unknown notification
    return _error(msg_id, -32601, f"method not found: {method}")


def main() -> None:
    """Read JSON-RPC lines from stdin and write responses to stdout until EOF."""

    def converse(request: dict[str, Any]) -> dict[str, Any]:
        """Send a server-initiated request and block until its response arrives."""
        sys.stdout.write(json.dumps(request) + "\n")
        sys.stdout.flush()
        while True:
            reply = sys.stdin.readline()
            if not reply:
                raise SystemExit(0)  # client went away mid-conversation
            reply = reply.strip()
            if not reply:
                continue
            obj = json.loads(reply)
            if obj.get("id") == request["id"] and "method" not in obj:
                return obj
            # One conversation at a time in this tiny example; anything else
            # arriving while we wait is dropped.

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = handle(json.loads(line), converse)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
