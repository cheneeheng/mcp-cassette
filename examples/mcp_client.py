"""A minimal transport-level JSON-RPC client used by the examples.

Launches a server *command* (the real server, mcp-cassette's recording proxy, or its
replay server — they are interchangeable, which is the point), sends newline-delimited
JSON-RPC requests, and collects the JSON objects written back. Pure standard library;
no MCP client SDK, because mcp-cassette works at the transport level.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from typing import Any

PROTOCOL_VERSION = "2024-11-05"


def initialize() -> list[dict[str, Any]]:
    """The opening handshake: ``initialize`` request + ``initialized`` notification."""
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "example-client", "version": "1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def tool_call(msg_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A ``tools/call`` request object."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def run(
    cmd: list[str],
    messages: list[dict[str, Any]],
    responder: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Run ``cmd``, send every message, and return the JSON objects it emits.

    Stdin is held open until every request has a response, so a real server is never
    sent EOF mid-request; then stdin closes and the process is drained to exit.

    Args:
        cmd: The server command to launch (real, recording proxy, or replay server).
        messages: JSON-RPC objects to send, in order.
        responder: Optional callback for server-initiated requests (sampling,
            elicitation): called with the decoded request; a returned dict is written
            back as the client's response, ``None`` leaves it unanswered.

    Returns:
        The JSON objects the server wrote to stdout, in arrival order.
    """
    expected = sum(1 for m in messages if "id" in m and "method" in m)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdin is not None and proc.stdout is not None

    objects: list[dict[str, Any]] = []
    stdout = proc.stdout
    stdin = proc.stdin
    write_lock = threading.Lock()  # the reader thread answers server requests

    def send(obj: dict[str, Any]) -> None:
        with write_lock:
            stdin.write(json.dumps(obj).encode("utf-8") + b"\n")
            stdin.flush()

    def reader() -> None:
        for raw in stdout:
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue  # non-JSON server chatter; ignore
            objects.append(obj)
            if responder is not None and "id" in obj and "method" in obj:
                reply = responder(obj)
                if reply is not None:
                    try:
                        send(reply)
                    except OSError:
                        pass  # stdin already closed at session end

    thread = threading.Thread(target=reader)
    thread.start()

    for message in messages:
        send(message)

    # Wait for all responses to land before closing stdin (startup can take a second).
    import time

    deadline = time.monotonic() + 30
    while (
        sum(1 for o in objects if "id" in o and "method" not in o) < expected
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)

    with write_lock:
        proc.stdin.close()
    proc.wait(timeout=10)
    thread.join(timeout=5)
    return objects
