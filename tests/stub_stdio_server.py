"""A configurable stdio MCP server stub, pure standard library, for ``check`` tests.

The FastMCP reference server cannot page ``tools/list``, misbehave on cue, or crash
before the handshake, so the tripwire's edge cases run against this instead. Flags:

* ``--pages N`` — split the tool list across ``N`` pages joined by ``nextCursor``.
* ``--server-requests`` — before answering ``tools/list``, send a ``ping`` and a
  ``roots/list`` request plus a log notification, and wait for both answers.
* ``--noise`` — print one non-JSON line to stdout at startup.
* ``--exit CODE`` — write a line to stderr and exit with ``CODE`` before reading.
* ``--hang`` — never answer, and ignore stdin closing.
* ``--error-on METHOD`` — answer ``METHOD`` with a JSON-RPC error.
* ``--describe-env VAR`` — append the value of ``$VAR`` to the ``echo`` description.
* ``--description TEXT`` — replace the ``echo`` description.

Run directly over stdio::

    python tests/stub_stdio_server.py --pages 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any


def _send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _read() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    decoded: dict[str, Any] = json.loads(line)
    return decoded


def _tools(args: argparse.Namespace) -> list[dict[str, Any]]:
    description = args.description
    if args.describe_env:
        description += f" [{os.environ.get(args.describe_env, '<unset>')}]"
    return [
        {
            "name": "echo",
            "description": description,
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
            },
        },
    ]


def _ask_client(next_id: int) -> None:
    pending = {next_id, next_id + 1}
    _send({"jsonrpc": "2.0", "id": next_id, "method": "ping"})
    _send({"jsonrpc": "2.0", "id": next_id + 1, "method": "roots/list"})
    _send(
        {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"level": "info", "data": "stub asked the client"},
        }
    )
    while pending:
        reply = _read()
        if reply is None:
            sys.exit(0)
        pending.discard(reply.get("id"))


def main() -> None:
    """Serve over stdio until stdin closes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--server-requests", action="store_true")
    parser.add_argument("--noise", action="store_true")
    parser.add_argument("--exit", type=int, default=None)
    parser.add_argument("--hang", action="store_true")
    parser.add_argument("--error-on", default=None)
    parser.add_argument("--describe-env", default=None)
    parser.add_argument("--description", default="Echo text back.")
    args = parser.parse_args()

    if args.exit is not None:
        sys.stderr.write("stub: package download failed\n")
        sys.stderr.flush()
        sys.exit(args.exit)
    if args.hang:
        while True:
            time.sleep(3600)
    if args.noise:
        sys.stdout.write("stub server starting up\n")
        sys.stdout.flush()

    tools = _tools(args)
    per_page = -(-len(tools) // args.pages)
    asked = False
    while (request := _read()) is not None:
        method = request.get("method")
        msg_id = request.get("id")
        if msg_id is None:
            continue
        if method == args.error_on:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": f"{method} is broken"},
                }
            )
            continue
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub", "version": "1.0.0"},
            }
        elif method == "tools/list":
            if args.server_requests and not asked:
                asked = True
                _ask_client(1000)
            cursor = (request.get("params") or {}).get("cursor")
            page = int(cursor.removeprefix("page-")) if cursor else 0
            result = {"tools": tools[page * per_page : (page + 1) * per_page]}
            if page + 1 < args.pages:
                result["nextCursor"] = f"page-{page + 1}"
        else:
            result = {}
        _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


if __name__ == "__main__":
    main()
