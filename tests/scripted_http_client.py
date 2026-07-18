"""A tiny transport-level scripted Streamable HTTP MCP client for tests.

POSTs newline-agnostic JSON-RPC objects to a ``/mcp`` endpoint with ``httpx``,
parses JSON and SSE response modes, tracks the ``Mcp-Session-Id`` lifecycle, and can
hold a GET listening stream. Deliberately does not use the official client SDK —
mcp-cassette is transport-level, and so are its tests.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from anyio.from_thread import start_blocking_portal

from mcp_cassette.transports.http.wire import SseParser

REFERENCE_HTTP_SERVER = str(
    Path(__file__).parent / "reference_http_server" / "server.py"
)

ACCEPT_BOTH = "application/json, text/event-stream"


def free_port() -> int:
    """Pick a currently-free TCP port (best effort)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_reference_http_server(
    port: int, *, json_response: bool = False
) -> subprocess.Popen[bytes]:
    """Launch the reference Streamable HTTP server and wait until it accepts."""
    cmd = [sys.executable, REFERENCE_HTTP_SERVER, "--port", str(port)]
    if json_response:
        cmd.append("--json-response")
    proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
    wait_for_port(port)
    return proc


def wait_for_port(port: int, timeout: float = 30.0) -> None:
    """Poll until 127.0.0.1:port accepts a TCP connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"port {port} never came up")


@contextmanager
def in_process_server(serve: Any) -> Iterator[str]:
    """Run an mcp-cassette http engine's ``serve`` in a background portal thread.

    Yields the bound URL; on exit cancels the task (the engine finalizes its
    cassette/report in its shielded ``finally``).
    """
    with start_blocking_portal() as portal:
        future, url = portal.start_task(serve)
        try:
            yield str(url)
        finally:
            future.cancel()


@dataclass
class HttpSessionResult:
    """The outcome of a scripted Streamable HTTP session."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    get_messages: list[dict[str, Any]] = field(default_factory=list)
    statuses: list[int] = field(default_factory=list)
    session_id: str | None = None

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


def run_http_session(
    url: str,
    messages: list[dict[str, Any]],
    *,
    open_get: bool = False,
    expected_get: int = 0,
    extra_headers: dict[str, str] | None = None,
    responder: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    timeout: float = 30.0,
) -> HttpSessionResult:
    """POST each message in order, collecting every server->client object.

    Args:
        url: The ``/mcp`` endpoint (proxy, replay server, or real server).
        messages: JSON-RPC objects to POST, in order.
        open_get: Open a GET listening stream after the first message.
        expected_get: Wait (bounded) until this many GET-stream messages arrived.
        extra_headers: Extra request headers (e.g. a planted ``Authorization``).
        responder: Optional callback for server-initiated requests: called with
            the decoded request; a returned dict is POSTed back as the client's
            response, ``None`` leaves it unanswered.
        timeout: Per-request timeout ceiling in seconds.

    Returns:
        A :class:`HttpSessionResult`.
    """
    result = HttpSessionResult()
    lock = threading.Lock()
    client = httpx.Client(timeout=httpx.Timeout(timeout, read=timeout))
    get_client = httpx.Client(timeout=httpx.Timeout(timeout, read=timeout))
    get_thread: threading.Thread | None = None

    def base_headers() -> dict[str, str]:
        headers = {"content-type": "application/json", "accept": ACCEPT_BOTH}
        if result.session_id is not None:
            headers["mcp-session-id"] = result.session_id
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def handle(obj: dict[str, Any], *, from_get: bool) -> None:
        with lock:
            result.messages.append(obj)
            if from_get:
                result.get_messages.append(obj)
        if responder is not None and "id" in obj and "method" in obj:
            reply = responder(obj)
            if reply is not None:
                post(reply)

    def post(obj: dict[str, Any]) -> None:
        try:
            with client.stream(
                "POST",
                url,
                content=json.dumps(obj).encode("utf-8"),
                headers=base_headers(),
            ) as response:
                result.statuses.append(response.status_code)
                sid = response.headers.get("mcp-session-id")
                if sid is not None:
                    result.session_id = sid
                content_type = response.headers.get("content-type", "")
                if content_type.startswith("text/event-stream"):
                    parser = SseParser()
                    for chunk in response.iter_bytes():
                        for event in parser.feed(chunk):
                            handle(json.loads(event.data), from_get=False)
                    final = parser.finish()
                    if final is not None:
                        handle(json.loads(final.data), from_get=False)
                elif content_type.startswith("application/json"):
                    body = response.read()
                    if body.strip():
                        handle(json.loads(body), from_get=False)
                else:
                    response.read()
        except httpx.HTTPError:
            result.statuses.append(-1)

    def get_loop() -> None:
        headers = dict(base_headers())
        headers["accept"] = "text/event-stream"
        headers.pop("content-type", None)
        try:
            with get_client.stream("GET", url, headers=headers) as response:
                if not response.headers.get("content-type", "").startswith(
                    "text/event-stream"
                ):
                    return
                parser = SseParser()
                for chunk in response.iter_bytes():
                    for event in parser.feed(chunk):
                        handle(json.loads(event.data), from_get=True)
        except (httpx.HTTPError, RuntimeError):
            pass  # stream torn down at session end

    first, *rest = messages
    post(first)
    if open_get:
        get_thread = threading.Thread(target=get_loop, daemon=True)
        get_thread.start()
        time.sleep(0.2)  # let the listening stream attach before more POSTs
    for message in rest:
        post(message)
    if expected_get:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with lock:
                if len(result.get_messages) >= expected_get:
                    break
            time.sleep(0.05)
    client.close()
    get_client.close()
    if get_thread is not None:
        get_thread.join(timeout=5)
    return result
