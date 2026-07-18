"""Wire-layer unit tests (SKELETON_v2 §04): h11 loop and SSE framing round-trips."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import Any

import anyio
import httpx
import pytest

from mcp_cassette.transports.http import wire
from mcp_cassette.transports.http.wire import (
    HttpRequest,
    Responder,
    SseEvent,
    SseParser,
    encode_sse_event,
    sse_events,
)

# --- SSE parsing --------------------------------------------------------------------


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _collect(*parts: bytes) -> list[SseEvent]:
    return [event async for event in sse_events(_chunks(*parts))]


def test_sse_single_event() -> None:
    events = anyio.run(partial(_collect, b"data: hello\n\n"))
    assert events == [SseEvent(data="hello")]


def test_sse_multi_data_lines_joined() -> None:
    events = anyio.run(partial(_collect, b"data: a\ndata: b\n\n"))
    assert events[0].data == "a\nb"


def test_sse_buffers_partial_reads_across_chunks() -> None:
    events = anyio.run(partial(_collect, b"da", b"ta: hel", b"lo\n", b"\n"))
    assert events == [SseEvent(data="hello")]


def test_sse_id_and_event_fields() -> None:
    events = anyio.run(partial(_collect, b"event: message\nid: 7\ndata: x\n\n"))
    assert events[0].event_id == "7"
    assert events[0].event_type == "message"


def test_sse_ignores_comments_and_unknown_fields() -> None:
    events = anyio.run(
        partial(_collect, b": comment\nretry: 100\nbogus: y\ndata: x\n\n")
    )
    assert events == [SseEvent(data="x")]


def test_sse_missing_trailing_blank_line_at_eof() -> None:
    events = anyio.run(partial(_collect, b"data: last"))
    assert events == [SseEvent(data="last")]


def test_sse_crlf_lines() -> None:
    events = anyio.run(partial(_collect, b"data: x\r\n\r\n"))
    assert events == [SseEvent(data="x")]


def test_sse_event_without_data_is_dropped() -> None:
    events = anyio.run(partial(_collect, b"event: ping\n\ndata: real\n\n"))
    assert [e.data for e in events] == ["real"]


def test_encode_sse_event_round_trips() -> None:
    encoded = encode_sse_event("line1\nline2", event_id="3")
    events = anyio.run(partial(_collect, encoded))
    assert events[0].data == "line1\nline2"
    assert events[0].event_id == "3"


def test_sse_parser_finish_is_idempotent() -> None:
    parser = SseParser()
    parser.feed(b"data: x\n\n")
    assert parser.finish() is None


# --- h11 server loop ----------------------------------------------------------------


async def _with_server(
    handler: Callable[[HttpRequest, Responder], Awaitable[None]],
    client_fn: Callable[[str], Awaitable[None]],
) -> None:
    async with anyio.create_task_group() as tg:
        port = await tg.start(partial(wire.serve_http, handler))
        await client_fn(f"http://127.0.0.1:{port}")
        tg.cancel_scope.cancel()


def test_json_request_response_round_trip() -> None:
    async def handler(request: HttpRequest, responder: Responder) -> None:
        body = json.loads(request.body)
        reply = {"echo": body["value"], "target": request.target}
        await responder.send(
            200, json.dumps(reply).encode(), content_type="application/json"
        )

    async def client(url: str) -> None:
        async with httpx.AsyncClient() as http:
            response = await http.post(f"{url}/mcp", json={"value": 42})
            assert response.status_code == 200
            assert response.json() == {"echo": 42, "target": "/mcp"}

    anyio.run(partial(_with_server, handler, client))


def test_keep_alive_serves_sequential_requests() -> None:
    seen: list[str] = []

    async def handler(request: HttpRequest, responder: Responder) -> None:
        seen.append(request.method)
        await responder.send(200, b"ok", content_type="text/plain")

    async def client(url: str) -> None:
        async with httpx.AsyncClient() as http:
            first = await http.post(url, content=b"a")
            second = await http.post(url, content=b"b")
            assert first.status_code == second.status_code == 200

    anyio.run(partial(_with_server, handler, client))
    assert seen == ["POST", "POST"]


def test_streamed_sse_response() -> None:
    async def handler(request: HttpRequest, responder: Responder) -> None:
        await responder.start(200, content_type="text/event-stream")
        for i in range(3):
            await responder.send_body(encode_sse_event(json.dumps({"n": i})))
        await responder.end()

    async def client(url: str) -> None:
        async with httpx.AsyncClient() as http:
            async with http.stream("GET", url) as response:
                assert response.headers["content-type"] == "text/event-stream"
                events = [event async for event in sse_events(response.aiter_bytes())]
        assert [json.loads(e.data)["n"] for e in events] == [0, 1, 2]

    anyio.run(partial(_with_server, handler, client))


def test_request_headers_are_lowercased_and_body_complete() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: HttpRequest, responder: Responder) -> None:
        captured["headers"] = request.headers
        captured["body"] = request.body
        await responder.send(202, b"")

    async def client(url: str) -> None:
        async with httpx.AsyncClient() as http:
            response = await http.post(
                url, content=b"x" * 1000, headers={"X-Custom": "Value"}
            )
            assert response.status_code == 202

    anyio.run(partial(_with_server, handler, client))
    assert captured["headers"]["x-custom"] == "Value"
    assert captured["body"] == b"x" * 1000


def test_abort_closes_connection_mid_body() -> None:
    async def handler(request: HttpRequest, responder: Responder) -> None:
        await responder.start(200, content_type="application/json")
        await responder.send_body(b'{"partial')
        await responder.abort()

    async def client(url: str) -> None:
        async with httpx.AsyncClient() as http:
            with pytest.raises(httpx.HTTPError):
                await http.post(url)

    anyio.run(partial(_with_server, handler, client))
