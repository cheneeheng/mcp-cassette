"""HTTP recording proxy edge tests (ITER_01_v2 §04) against in-process stub upstreams.

The stub upstream is our own ``wire.serve_http`` with a per-test handler, so every
upstream shape (5xx at first contact, mid-session death, non-SSE GET answers, SSE
streams cut mid-event) is exercised without a subprocess — the unit-layer contract.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

import mcp_cassette.transports.http.proxy as proxy_module
from mcp_cassette.cassette import Cassette, RedactionRule
from mcp_cassette.transports.http import wire
from mcp_cassette.transports.http.proxy import RecordingProxy
from mcp_cassette.transports.http.wire import (
    HttpRequest,
    Responder,
    encode_sse_event,
)

INIT_REQ = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

ClientFn = Callable[[str, httpx.AsyncClient, RecordingProxy], Awaitable[None]]
UpstreamHandler = Callable[[HttpRequest, Responder], Awaitable[None]]


def _drive(
    tmp_path: Path,
    upstream_handler: UpstreamHandler,
    client_fn: ClientFn,
    **proxy_kw: Any,
) -> tuple[RecordingProxy, Path]:
    """Run proxy + stub upstream + async client in one loop; return after finalize."""
    cassette_path = tmp_path / "c.json"
    holder: dict[str, RecordingProxy] = {}

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            up_port = await tg.start(partial(wire.serve_http, upstream_handler))
            proxy = RecordingProxy(
                server_url=f"http://127.0.0.1:{up_port}/mcp",
                cassette_path=str(cassette_path),
                **proxy_kw,
            )
            holder["proxy"] = proxy
            url = await tg.start(proxy.serve)
            async with httpx.AsyncClient(timeout=10) as client:
                await client_fn(str(url), client, proxy)
            tg.cancel_scope.cancel()

    anyio.run(main)
    return holder["proxy"], cassette_path


def test_first_contact_5xx_is_fatal_and_writes_no_cassette(tmp_path: Path) -> None:
    async def upstream(request: HttpRequest, responder: Responder) -> None:
        await responder.send(500, b"boom", content_type="text/plain")

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        response = await client.post(url, json=INIT_REQ)
        assert response.status_code == 502
        assert "first contact" in response.text

    proxy, cassette_path = _drive(tmp_path, upstream, client_fn)
    assert proxy.fatal_error is not None
    assert "answered 500 at first contact" in proxy.fatal_error
    assert not cassette_path.exists()


def test_upstream_death_after_first_contact_is_not_fatal(tmp_path: Path) -> None:
    calls = 0

    async def upstream(request: HttpRequest, responder: Responder) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await responder.send(
                200,
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode(),
                content_type="application/json",
                headers=[("mcp-session-id", "up-1")],
            )
        else:
            await responder.abort()  # connection dies without a response

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        ok = await client.post(url, json=INIT_REQ)
        assert ok.status_code == 200
        bad = await client.post(
            url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        assert bad.status_code == 502  # named failure, but the session survives
        assert proxy.fatal_error is None

    proxy, cassette_path = _drive(tmp_path, upstream, client_fn)
    cassette = Cassette.load(cassette_path)
    assert cassette.session_id == "up-1"  # captured from the first upstream response
    assert any(m.kind == "response" for m in cassette.messages)


def test_second_concurrent_get_is_refused_409(tmp_path: Path) -> None:
    async def upstream(request: HttpRequest, responder: Responder) -> None:
        if request.method == "GET":
            await responder.start(200, content_type="text/event-stream")
            await anyio.sleep_forever()
        else:  # pragma: no cover - first contact only
            await responder.send(200, b"{}", content_type="application/json")

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        headers = {"accept": "text/event-stream"}
        async with client.stream("GET", url, headers=headers) as first:
            assert first.status_code == 200
            second = await client.get(url, headers=headers)
            assert second.status_code == 409  # spec: at most one listening stream

    _drive(tmp_path, upstream, client_fn)


def test_get_with_non_sse_upstream_answer_is_relayed(tmp_path: Path) -> None:
    async def upstream(request: HttpRequest, responder: Responder) -> None:
        await responder.send(405, b"no listening stream", content_type="text/plain")

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        # 405 at first contact is not a 5xx, so it is relayed, not fatal.
        response = await client.get(url, headers={"accept": "text/event-stream"})
        assert response.status_code == 405
        assert response.text == "no listening stream"
        assert proxy.fatal_error is None

    _drive(tmp_path, upstream, client_fn)


def test_get_first_contact_failure_is_fatal(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    holder: dict[str, RecordingProxy] = {}

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            proxy = RecordingProxy(
                server_url="http://127.0.0.1:9/mcp",  # dead: discard port
                cassette_path=str(cassette_path),
            )
            holder["proxy"] = proxy
            url = await tg.start(proxy.serve)
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    str(url), headers={"accept": "text/event-stream"}
                )
                assert response.status_code == 502
            tg.cancel_scope.cancel()

    anyio.run(main)
    assert holder["proxy"].fatal_error is not None
    assert not cassette_path.exists()


def test_plain_request_is_forwarded_verbatim(tmp_path: Path) -> None:
    async def upstream(request: HttpRequest, responder: Responder) -> None:
        assert request.method == "DELETE"
        await responder.send(200, b"bye", content_type="text/plain")

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        response = await client.delete(url)
        assert response.status_code == 200
        assert response.text == "bye"

    proxy, cassette_path = _drive(tmp_path, upstream, client_fn)
    # A session-management request carries no JSON-RPC message: nothing recorded.
    assert Cassette.load(cassette_path).messages == []


def test_plain_forward_to_dead_upstream_is_502(tmp_path: Path) -> None:
    calls = 0

    async def upstream(request: HttpRequest, responder: Responder) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await responder.send(
                200,
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode(),
                content_type="application/json",
            )
        else:
            await responder.abort()  # connection dies without a response

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        ok = await client.post(url, json=INIT_REQ)
        assert ok.status_code == 200
        bad = await client.delete(url)  # session-management path, not JSON-RPC
        assert bad.status_code == 502
        assert proxy.fatal_error is None  # after first contact: named, not fatal

    _drive(tmp_path, upstream, client_fn)


def test_sse_upstream_death_mid_stream_keeps_captured_events(tmp_path: Path) -> None:
    event = {"jsonrpc": "2.0", "method": "notifications/progress"}

    async def upstream(request: HttpRequest, responder: Responder) -> None:
        await responder.start(200, content_type="text/event-stream")
        await responder.send_body(encode_sse_event(json.dumps(event)))
        await responder.abort()  # upstream dies mid-stream

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        with pytest.raises(httpx.HTTPError):
            async with client.stream("POST", url, json=INIT_REQ) as response:
                async for _ in response.aiter_bytes():
                    pass

    proxy, cassette_path = _drive(tmp_path, upstream, client_fn)
    cassette = Cassette.load(cassette_path)
    tapped = [m for m in cassette.messages if m.method == "notifications/progress"]
    assert tapped and tapped[0].channel == "post"  # keep what was captured


def test_sse_final_event_without_trailing_blank_line_is_tapped(
    tmp_path: Path,
) -> None:
    event = {"jsonrpc": "2.0", "method": "notifications/last"}

    async def upstream(request: HttpRequest, responder: Responder) -> None:
        await responder.start(200, content_type="text/event-stream")
        # A complete data line but no dispatching blank line before EOF.
        await responder.send_body(f"data: {json.dumps(event)}\n".encode())
        await responder.end()

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        response = await client.post(url, json=INIT_REQ)
        assert response.status_code == 200

    proxy, cassette_path = _drive(tmp_path, upstream, client_fn)
    cassette = Cassette.load(cassette_path)
    assert any(m.method == "notifications/last" for m in cassette.messages)


def test_empty_post_body_records_nothing(tmp_path: Path) -> None:
    async def upstream(request: HttpRequest, responder: Responder) -> None:
        await responder.send(202, b"")

    async def client_fn(
        url: str, client: httpx.AsyncClient, proxy: RecordingProxy
    ) -> None:
        response = await client.post(url, content=b"")
        assert response.status_code == 202

    proxy, cassette_path = _drive(tmp_path, upstream, client_fn)
    assert Cassette.load(cassette_path).messages == []


def test_redaction_composition_without_defaults(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp",
        cassette_path=str(cassette_path),
        redaction=[RedactionRule(locator="*mytoken*")],
        include_default_redactions=False,
    )
    assert proxy.message_count == 0
    proxy._recorder.on_message(
        "client",
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "x",
                "params": {"mytoken": "s3cret", "password": "kept"},
            }
        ),
        exchange=0,
    )
    assert proxy.message_count == 1
    proxy.finalize()
    payload = Cassette.load(cassette_path).messages[0].payload
    assert isinstance(payload, dict)
    assert payload["params"]["mytoken"] == "REDACTED"
    assert payload["params"]["password"] == "kept"  # defaults disabled


# --- run(): full process-shaped lifecycles, in-process --------------------------------


def _wait_bound(proxy: RecordingProxy, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while proxy.bound_url is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert proxy.bound_url is not None
    return proxy.bound_url


def test_run_exits_2_on_fatal_first_contact(tmp_path: Path) -> None:
    # Regression: a fatal first contact must end run() itself (exit 2), not just
    # serve() — the outer task group holds the signal watcher.
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )
    rc: list[int] = []
    thread = threading.Thread(target=lambda: rc.append(proxy.run()), daemon=True)
    thread.start()
    url = _wait_bound(proxy)
    response = httpx.post(url, json=INIT_REQ, timeout=10)
    assert response.status_code == 502
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert rc == [2]
    assert not cassette_path.exists()


def test_run_exits_130_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interrupted = threading.Event()

    async def fake_wait() -> None:
        while not interrupted.is_set():
            await anyio.sleep(0.01)

    monkeypatch.setattr(proxy_module, "wait_for_interrupt", fake_wait)
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )
    rc: list[int] = []
    thread = threading.Thread(target=lambda: rc.append(proxy.run()), daemon=True)
    thread.start()
    _wait_bound(proxy)
    interrupted.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert rc == [130]
    assert cassette_path.exists()  # interrupt still finalizes the cassette
