"""HTTP replay server edge tests (ITER_02_v2 §04) over hand-built cassettes.

Protocol edges the round-trip integration tests do not reach: malformed POSTs,
session-management methods, cassettes with no recorded initialize, SSE-mode
initialize with protocol rewrite, the after-response disconnect variant, and the
full ``run()`` lifecycle (in-process, interrupt-free on every OS).
"""

from __future__ import annotations

import copy
import json
import socket
import struct
import threading
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from scripted_http_client import ACCEPT_BOTH, in_process_server, run_http_session

import mcp_cassette.transports.http.server as server_module
from mcp_cassette.cassette import (
    Cassette,
    Fault,
    FaultOverlay,
    MatchConfig,
    Message,
)
from mcp_cassette.transports.http import wire
from mcp_cassette.transports.http.server import HttpReplayServer
from mcp_cassette.transports.http.wire import HttpRequest, Responder

INIT_REQ = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
}
INIT_RESP = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "serverInfo": {"name": "hand-built", "version": "1.0"},
    },
}
ECHO_REQ = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "echo", "arguments": {"text": "hi"}},
}
ECHO_RESP = {
    "jsonrpc": "2.0",
    "id": 2,
    "result": {"content": [{"type": "text", "text": "hi"}]},
}


def _msg(
    seq: int,
    sender: str,
    kind: str,
    payload: dict[str, Any],
    *,
    method: str | None = None,
    msg_id: str | int | None = None,
    exchange: int | None = None,
    channel: str | None = None,
) -> Message:
    return Message(
        seq=seq,
        t_offset_ms=seq,
        sender=sender,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        method=method,
        msg_id=msg_id,
        # Deep copy so in-place transforms (protocol rewrite) on one test's
        # served payload cannot leak into the module-level template dicts.
        payload=copy.deepcopy(payload),
        exchange=exchange,
        channel=channel,  # type: ignore[arg-type]
    )


def _cassette(*, sse_initialize: bool = False) -> Cassette:
    messages = [
        _msg(
            0, "client", "request", INIT_REQ, method="initialize", msg_id=1, exchange=0
        )
    ]
    if sse_initialize:
        messages.append(
            _msg(
                1,
                "server",
                "notification",
                {"jsonrpc": "2.0", "method": "notifications/message"},
                method="notifications/message",
                exchange=0,
                channel="post",
            )
        )
    n = len(messages)
    messages += [
        _msg(n, "server", "response", INIT_RESP, msg_id=1, exchange=0, channel="post"),
        _msg(
            n + 1,
            "client",
            "request",
            ECHO_REQ,
            method="tools/call",
            msg_id=2,
            exchange=1,
        ),
        _msg(
            n + 2,
            "server",
            "response",
            ECHO_RESP,
            msg_id=2,
            exchange=1,
            channel="post",
        ),
    ]
    return Cassette(
        recorded_at=datetime(2026, 7, 18, tzinfo=UTC),
        transport="http",
        messages=messages,
    )


def _initialize(client: httpx.Client, url: str) -> dict[str, str]:
    init = client.post(url, json=INIT_REQ, headers={"accept": ACCEPT_BOTH})
    return {
        "accept": ACCEPT_BOTH,
        "mcp-session-id": init.headers["mcp-session-id"],
    }


def test_post_bad_json_is_400() -> None:
    server = HttpReplayServer(_cassette())
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                url, content=b"{not json", headers={"content-type": "application/json"}
            )
            assert response.status_code == 400


def test_post_non_object_json_is_400() -> None:
    server = HttpReplayServer(_cassette())
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                url, content=b"[1, 2]", headers={"content-type": "application/json"}
            )
            assert response.status_code == 400


def test_delete_is_200_and_other_methods_405() -> None:
    server = HttpReplayServer(_cassette())
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            assert client.delete(url).status_code == 200
            put = client.put(url, content=b"x")
            assert put.status_code == 405
            assert put.headers["allow"] == "GET, POST, DELETE"


def test_initialize_without_recorded_response_errors() -> None:
    no_init = Cassette(
        recorded_at=datetime(2026, 7, 18, tzinfo=UTC),
        transport="http",
        messages=[
            _msg(
                0,
                "client",
                "request",
                ECHO_REQ,
                method="tools/call",
                msg_id=2,
                exchange=0,
            ),
            _msg(
                1, "server", "response", ECHO_RESP, msg_id=2, exchange=0, channel="post"
            ),
        ],
    )
    server = HttpReplayServer(no_init)
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=INIT_REQ, headers={"accept": ACCEPT_BOTH})
            assert response.status_code == 200
            body = response.json()
            assert body["error"]["code"] == -32001
            assert "no recorded initialize response" in body["error"]["message"]


def test_get_without_session_is_404() -> None:
    server = HttpReplayServer(_cassette())
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            response = client.get(url, headers={"accept": "text/event-stream"})
            assert response.status_code == 404


def test_second_get_is_409() -> None:
    server = HttpReplayServer(_cassette())
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _initialize(client, url)
            stream_headers = {
                "accept": "text/event-stream",
                "mcp-session-id": headers["mcp-session-id"],
            }
            with client.stream("GET", url, headers=stream_headers) as first:
                assert first.status_code == 200
                second = client.get(url, headers=stream_headers)
                assert second.status_code == 409


def test_sse_mode_initialize_applies_protocol_rewrite() -> None:
    server = HttpReplayServer(
        _cassette(sse_initialize=True),
        match=MatchConfig(rewrite_protocol_version=True),
    )
    requested = dict(INIT_REQ)
    requested["params"] = {"protocolVersion": "2099-01-01", "capabilities": {}}
    with in_process_server(server.serve) as url:
        result = run_http_session(url, [requested])
    # The initialize exchange was recorded as SSE: notification then response.
    assert [m.get("method") for m in result.messages][0] == "notifications/message"
    response = result.response_for(1)
    assert response is not None
    assert response["result"]["protocolVersion"] == "2099-01-01"


def test_disconnect_after_response_serves_then_dies() -> None:
    overlay = FaultOverlay(faults=[Fault.disconnect("tools/call", after_response=True)])
    server = HttpReplayServer(_cassette(), faults=overlay)
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _initialize(client, url)
            response = client.post(url, json=ECHO_REQ, headers=headers)
            # The recorded response is served in full first...
            assert response.json()["result"]["content"][0]["text"] == "hi"
            # ...then the server is dead.
            with pytest.raises(httpx.HTTPError):
                client.post(url, json=ECHO_REQ, headers=headers)


def test_finalize_is_idempotent(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    server = HttpReplayServer(_cassette(), report_path=str(report))
    server.finalize()
    written = report.read_text(encoding="utf-8")
    server.finalize()
    assert report.read_text(encoding="utf-8") == written


def test_get_stream_client_disconnect_frees_the_stream() -> None:
    # A client that vanishes mid-listen must be noticed on the next write and the
    # single listening-stream slot freed for a reconnect.
    cassette = _cassette()
    n = len(cassette.messages)
    for i in range(2):
        cassette.messages.append(
            _msg(
                n + i,
                "server",
                "notification",
                {"jsonrpc": "2.0", "method": f"notifications/g{i}"},
                method=f"notifications/g{i}",
                channel="get",
            )
        )
    server = HttpReplayServer(cassette)
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _initialize(client, url)
            target = httpx.URL(url)
            sock = socket.create_connection((target.host, target.port), timeout=10)
            request = (
                f"GET {target.path} HTTP/1.1\r\n"
                f"host: {target.host}\r\n"
                "accept: text/event-stream\r\n"
                f"mcp-session-id: {headers['mcp-session-id']}\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += sock.recv(1024)
            assert b" 200 " in buf
            # Hard-close (RST on close) so the server's next write fails at once
            # instead of draining into a half-closed socket.
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            sock.close()
            # Serving the echo exchange releases the get-channel notifications,
            # forcing a write to the dead stream.
            response = client.post(url, json=ECHO_REQ, headers=headers)
            assert response.status_code == 200
            stream_headers = {
                "accept": "text/event-stream",
                "mcp-session-id": headers["mcp-session-id"],
            }
            deadline = time.monotonic() + 10
            status = 0
            while time.monotonic() < deadline:
                with client.stream("GET", url, headers=stream_headers) as retry:
                    status = retry.status_code
                if status == 200:  # the slot was freed; 409 while still held
                    break
                time.sleep(0.05)
            assert status == 200


def test_new_episodes_fallthrough_without_upstream_session(tmp_path: Path) -> None:
    # Upstream that never issues a session id, answers one miss as SSE without a
    # trailing blank line, and answers another with an empty 202 body.
    cassette_path = tmp_path / "c.json"
    _cassette().save(cassette_path)

    async def upstream(request: HttpRequest, responder: Responder) -> None:
        obj = json.loads(request.body)
        method = obj.get("method")
        if method == "initialize":
            await responder.send(
                200,
                json.dumps(
                    {"jsonrpc": "2.0", "id": obj.get("id"), "result": {}}
                ).encode(),
                content_type="application/json",
            )
        elif method == "tools/list":
            await responder.start(200, content_type="text/event-stream")
            payload = {"jsonrpc": "2.0", "id": obj["id"], "result": {"tools": []}}
            # A complete data line but no dispatching blank line before EOF.
            await responder.send_body(f"data: {json.dumps(payload)}\n".encode())
            await responder.end()
        else:  # notifications/initialized handshake, empty-answer posts
            await responder.send(202, b"")

    holder: dict[str, HttpReplayServer] = {}

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            up_port = await tg.start(partial(wire.serve_http, upstream))
            server = HttpReplayServer(
                Cassette.load(cassette_path),
                fallthrough_url=f"http://127.0.0.1:{up_port}/mcp",
                cassette_path=str(cassette_path),
            )
            holder["server"] = server
            url = await tg.start(server.serve)
            async with httpx.AsyncClient(timeout=10) as client:
                init = await client.post(
                    str(url), json=INIT_REQ, headers={"accept": ACCEPT_BOTH}
                )
                headers = {
                    "accept": ACCEPT_BOTH,
                    "mcp-session-id": init.headers["mcp-session-id"],
                }
                listed = await client.post(
                    str(url),
                    json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                    headers=headers,
                )
                assert listed.status_code == 200
                assert listed.headers["content-type"].startswith("text/event-stream")
                assert "tools" in listed.text  # the unterminated event arrived
                empty = await client.post(
                    str(url),
                    json={"jsonrpc": "2.0", "id": 8, "method": "tools/refresh"},
                    headers=headers,
                )
                assert empty.status_code == 202
            tg.cancel_scope.cancel()

    anyio.run(main)
    holder["server"].finalize()
    merged = Cassette.load(cassette_path)
    appended = merged.messages[4:]
    assert [(m.sender, m.method) for m in appended] == [
        ("client", "tools/list"),
        ("server", None),  # the SSE final event was still tapped
        ("client", "tools/refresh"),  # the empty 202 body recorded nothing
    ]


# --- run(): full process-shaped lifecycles, in-process --------------------------------


def _wait_bound(server: HttpReplayServer, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while server.bound_url is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.bound_url is not None
    return server.bound_url


def test_run_exits_0_after_disconnect_fault() -> None:
    # Regression: a disconnect fault must end run() itself (exit 0), not just
    # serve() — the outer task group holds the signal watcher.
    overlay = FaultOverlay(faults=[Fault.disconnect("tools/call")])
    server = HttpReplayServer(_cassette(), faults=overlay)
    rc: list[int] = []
    thread = threading.Thread(target=lambda: rc.append(server.run()), daemon=True)
    thread.start()
    url = _wait_bound(server)
    with httpx.Client(timeout=10) as client:
        headers = _initialize(client, url)
        with pytest.raises(httpx.HTTPError):
            client.post(url, json=ECHO_REQ, headers=headers)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert rc == [0]  # a disconnect fault is a scripted outcome, not a failure


def test_run_exits_3_on_misses_after_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interrupted = threading.Event()

    async def fake_wait() -> None:
        while not interrupted.is_set():
            await anyio.sleep(0.01)

    monkeypatch.setattr(server_module, "wait_for_interrupt", fake_wait)
    server = HttpReplayServer(_cassette())
    rc: list[int] = []
    thread = threading.Thread(target=lambda: rc.append(server.run()), daemon=True)
    thread.start()
    url = _wait_bound(server)
    with httpx.Client(timeout=10) as client:
        headers = _initialize(client, url)
        miss = client.post(
            url,
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
            headers=headers,
        )
        assert miss.json()["error"]["code"] == -32001
    interrupted.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert rc == [3]  # the CI-visible failure signal
