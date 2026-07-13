"""A tiny transport-level scripted MCP client for tests.

Runs a command (a real server, the recording proxy, or the replay server) as a
subprocess, writes newline-delimited JSON-RPC requests to its stdin, and collects the
JSON objects it writes to stdout. Deliberately does not use the official client SDK —
mcp-cassette is transport-level, and so are its tests.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REFERENCE_SERVER = str(Path(__file__).parent / "reference_server" / "server.py")

PROTOCOL_VERSION = "2024-11-05"


def reference_server_cmd(*extra: str) -> list[str]:
    """Command that launches the reference MCP server."""
    return [sys.executable, REFERENCE_SERVER, *extra]


@dataclass
class SessionResult:
    """The outcome of a scripted session."""

    messages: list[dict[str, Any]]
    returncode: int
    stderr: str

    def responses(self) -> list[dict[str, Any]]:
        """Server responses (objects carrying an ``id`` and no ``method``)."""
        return [m for m in self.messages if "id" in m and "method" not in m]

    def notifications(self) -> list[dict[str, Any]]:
        """Server notifications (objects carrying a ``method`` and no ``id``)."""
        return [m for m in self.messages if "method" in m and "id" not in m]

    def response_for(self, msg_id: Any) -> dict[str, Any] | None:
        """The response whose ``id`` equals ``msg_id``, if present."""
        for m in self.messages:
            if m.get("id") == msg_id and "method" not in m:
                return m
        return None


def initialize_sequence() -> list[dict[str, Any]]:
    """The standard opening handshake: initialize + initialized notification."""
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "scripted-client", "version": "1.0"},
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


def _count_requests(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if "id" in m and "method" in m)


def run_session(
    cmd: list[str],
    messages: list[dict[str, Any]],
    *,
    timeout: float = 30.0,
    expected_responses: int | None = None,
    settle: float = 5.0,
    env: dict[str, str] | None = None,
) -> SessionResult:
    """Run ``cmd`` as a subprocess, send ``messages``, and collect stdout objects.

    Requests are written up front, but stdin is held open until the expected number of
    responses has arrived (or the session settles), so a real server is never sent EOF
    while a request is still in flight. This models an interactive client without
    per-request round-trip coupling.

    Args:
        cmd: The subprocess command.
        messages: JSON-RPC objects to send, in order.
        timeout: Hard ceiling in seconds before the subprocess is killed.
        expected_responses: How many responses to await before closing stdin. Defaults
            to the number of request messages sent. Pass a lower number when a fault is
            expected to suppress a response.
        settle: Seconds of stdout inactivity after which the wait gives up (for faults
            that never respond).
        env: Optional environment overrides (merged over the current environment).

    Returns:
        A :class:`SessionResult`.
    """
    if expected_responses is not None:
        expected = expected_responses
    else:
        expected = _count_requests(messages)
    full_env = {**os.environ, **env} if env is not None else None
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=full_env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    out_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    stderr_chunks: list[bytes] = []
    reader = threading.Thread(target=_read_stdout, args=(proc.stdout, out_queue))
    err_reader = threading.Thread(
        target=_read_stderr, args=(proc.stderr, stderr_chunks)
    )
    reader.start()
    err_reader.start()

    for m in messages:
        proc.stdin.write(json.dumps(m).encode("utf-8") + b"\n")
    proc.stdin.flush()

    objs: list[dict[str, Any]] = []
    responses = 0
    deadline = time.monotonic() + timeout
    last_activity = time.monotonic()
    while responses < expected and time.monotonic() < deadline:
        try:
            item = out_queue.get(timeout=0.2)
        except queue.Empty:
            # Only give up on inactivity once the session has actually started
            # producing output; startup (proxy + server import) can take seconds.
            if objs and time.monotonic() - last_activity > settle:
                break
            continue
        if item is None:  # stdout closed
            break
        objs.append(item)
        last_activity = time.monotonic()
        if "id" in item and "method" not in item:
            responses += 1

    # Close stdin so the engine/server shuts down, then drain trailing output
    # (anchored notifications, final lines) until EOF.
    try:
        proc.stdin.close()
    except OSError:
        pass
    _drain(out_queue, objs, deadline)
    try:
        proc.wait(timeout=max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    reader.join(timeout=5)
    err_reader.join(timeout=5)
    return SessionResult(
        messages=objs,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
    )


def _read_stdout(stdout: Any, out_queue: queue.Queue[dict[str, Any] | None]) -> None:
    # readline() delivers each line as soon as it is available; iterating the file
    # object read-ahead-buffers on a pipe and would delay real-time delivery.
    while True:
        line = stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out_queue.put(obj)
    out_queue.put(None)


def _read_stderr(stderr: Any, chunks: list[bytes]) -> None:
    while True:
        line = stderr.readline()
        if not line:
            break
        chunks.append(line)


def _drain(
    out_queue: queue.Queue[dict[str, Any] | None],
    objs: list[dict[str, Any]],
    deadline: float,
) -> None:
    idle_until = time.monotonic() + 1.0
    while time.monotonic() < deadline and time.monotonic() < idle_until:
        try:
            item = out_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if item is None:
            return
        objs.append(item)
        idle_until = time.monotonic() + 1.0
