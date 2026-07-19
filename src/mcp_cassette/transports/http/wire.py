"""h11-driven HTTP/1.1 serving over anyio TCP, plus SSE event framing.

The wire layer owns framing for the Streamable HTTP transport exactly as
``record/pump.py`` owns newline-delimited stdio framing: an h11 state machine per TCP
connection for HTTP, and hand-rolled Server-Sent Events parsing (SSE is line-based —
the moral twin of stdio's ``buffered_lines``). No ASGI framework; both the recording
proxy and the replay server drive this loop directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial

import anyio
import anyio.abc
import h11


@dataclass
class SseEvent:
    """One decoded Server-Sent Event."""

    data: str
    event_id: str | None = None
    event_type: str | None = None


class SseParser:
    """Incremental (push) SSE parser behind :func:`sse_events`.

    Buffers partial reads, tolerates a missing trailing blank line at EOF, and never
    raises on unknown fields (they are ignored per the SSE spec).
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._data_lines: list[str] = []
        self._event_id: str | None = None
        self._event_type: str | None = None

    def feed(self, chunk: bytes) -> list[SseEvent]:
        """Consume bytes, returning any events the chunk completed."""
        self._buffer += chunk
        events: list[SseEvent] = []
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            event = self._feed_line(line)
            if event is not None:
                events.append(event)
        return events

    def finish(self) -> SseEvent | None:
        """Flush the pending event at EOF (missing trailing blank line)."""
        if self._buffer:
            line = self._buffer.rstrip(b"\r\n").decode("utf-8", errors="replace")
            self._buffer = b""
            event = self._feed_line(line)
            if event is not None:  # pragma: no cover — a lone final line can't dispatch
                return event
        return self._dispatch()

    def _feed_line(self, line: str) -> SseEvent | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data_lines.append(value)
        elif name == "id":
            self._event_id = value
        elif name == "event":
            self._event_type = value
        return None

    def _dispatch(self) -> SseEvent | None:
        if not self._data_lines:
            self._event_type = None
            return None
        event = SseEvent(
            data="\n".join(self._data_lines),
            event_id=self._event_id,
            event_type=self._event_type,
        )
        self._data_lines = []
        self._event_type = None
        return event


async def sse_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[SseEvent]:
    """Yield decoded SSE events (data payload, event id) from a byte stream.

    Mirrors v1's ``buffered_lines``: buffers partial reads, tolerates a missing
    trailing blank line at EOF, never raises on unknown fields (ignores them).

    Args:
        chunks: Any async iterable of byte chunks (an anyio stream's ``receive``
            loop, ``httpx``'s ``aiter_bytes()``, ...).

    Yields:
        Each complete :class:`SseEvent`.
    """
    parser = SseParser()
    async for chunk in chunks:
        for event in parser.feed(chunk):
            yield event
    final = parser.finish()
    if final is not None:
        yield final


def encode_sse_event(
    data: str, *, event_id: str | None = None, event_type: str | None = None
) -> bytes:
    """Serialize one SSE event (multi-line data becomes multiple ``data:`` lines)."""
    lines: list[str] = []
    if event_type is not None:
        lines.append(f"event: {event_type}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.extend(f"data: {part}" for part in data.split("\n"))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


@dataclass
class HttpRequest:
    """One fully-read HTTP request.

    Attributes:
        method: Uppercase request method.
        target: The request target (path + query) as received.
        headers: Header map with lower-cased names; last value wins.
        body: The complete request body.
    """

    method: str
    target: str
    headers: dict[str, str]
    body: bytes


class Responder:
    """Sends exactly one HTTP response for the current request.

    Two shapes: a complete response (:meth:`send`) or a streamed one
    (:meth:`start` then :meth:`send_body` then :meth:`end`), used for SSE. Fault
    injection uses :meth:`abort` to hard-close the connection mid-response.
    """

    def __init__(self, conn: h11.Connection, stream: anyio.abc.ByteStream) -> None:
        """Bind the responder to one connection's state machine and socket."""
        self._conn = conn
        self._stream = stream
        self.started = False
        self.ended = False
        self.aborted = False

    async def send(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str | None = None,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        """Send a complete response with a Content-Length body."""
        hdrs = [(name.encode(), value.encode()) for name, value in (headers or [])]
        if content_type is not None:
            hdrs.append((b"content-type", content_type.encode()))
        hdrs.append((b"content-length", str(len(body)).encode()))
        await self._emit(h11.Response(status_code=status, headers=hdrs))
        self.started = True
        if body:
            await self._emit(h11.Data(data=body))
        await self._emit(h11.EndOfMessage())
        self.ended = True

    async def start(
        self,
        status: int,
        *,
        content_type: str,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        """Begin a chunked streaming response (e.g. ``text/event-stream``)."""
        hdrs = [(name.encode(), value.encode()) for name, value in (headers or [])]
        hdrs.append((b"content-type", content_type.encode()))
        hdrs.append((b"transfer-encoding", b"chunked"))
        await self._emit(h11.Response(status_code=status, headers=hdrs))
        self.started = True

    async def send_body(self, data: bytes) -> None:
        """Send one body chunk of a streaming response, flushed immediately."""
        if data:
            await self._emit(h11.Data(data=data))

    async def end(self) -> None:
        """Finish the streaming response."""
        await self._emit(h11.EndOfMessage())
        self.ended = True

    async def abort(self) -> None:
        """Hard-close the TCP connection, mid-response or before one (faults)."""
        self.aborted = True
        try:
            await self._stream.aclose()
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass

    async def _emit(self, event: h11.Event) -> None:
        data = self._conn.send(event)
        if data:
            await self._stream.send(data)


Handler = Callable[[HttpRequest, Responder], Awaitable[None]]
"""Async callback serving one request via the given responder."""


async def _read_request(
    conn: h11.Connection, stream: anyio.abc.ByteStream
) -> HttpRequest | None:
    request: h11.Request | None = None
    body = b""
    while True:
        event = conn.next_event()
        if event is h11.NEED_DATA:
            try:
                data = await stream.receive()
            except (
                anyio.EndOfStream,
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
            ):
                data = b""
            conn.receive_data(data)
            continue
        if isinstance(event, h11.Request):
            request = event
        elif isinstance(event, h11.Data):
            body += bytes(event.data)
        elif isinstance(event, h11.EndOfMessage):
            assert request is not None  # h11 guarantees Request before EndOfMessage
            headers = {
                name.decode("ascii", errors="replace"): value.decode(
                    "utf-8", errors="replace"
                )
                for name, value in request.headers
            }
            return HttpRequest(
                method=request.method.decode("ascii"),
                target=request.target.decode("ascii", errors="replace"),
                headers=headers,
                body=body,
            )
        else:  # ConnectionClosed or PAUSED: nothing more on this connection
            return None


async def serve_connection(stream: anyio.abc.ByteStream, handler: Handler) -> None:
    """Serve HTTP/1.1 requests on one TCP connection until it closes.

    Keep-alive is honored: after a cleanly-finished response with both sides DONE the
    h11 cycle restarts. A handler that never completes its response (timeout fault)
    simply never returns; a handler that aborted leaves the loop.
    """
    conn = h11.Connection(h11.SERVER)
    try:
        while True:
            request = await _read_request(conn, stream)
            if request is None:
                return
            responder = Responder(conn, stream)
            await handler(request, responder)
            if responder.aborted or not responder.ended:
                return
            if conn.our_state is h11.DONE and conn.their_state is h11.DONE:
                conn.start_next_cycle()
            else:
                return
    except (
        h11.RemoteProtocolError,
        anyio.BrokenResourceError,
        anyio.ClosedResourceError,
    ):
        return
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await stream.aclose()
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass


async def serve_http(
    handler: Handler,
    *,
    port: int = 0,
    task_status: anyio.abc.TaskStatus[int] = anyio.TASK_STATUS_IGNORED,
) -> None:
    """Bind ``127.0.0.1`` and serve connections until cancelled.

    The bound port is reported via ``task_status.started`` (callers pass ``port=0``
    for an ephemeral port and read the real one back). Loopback-only by design: the
    local servers serve non-browser MCP clients, so no CORS and no external binds.
    """
    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=port)
    bound_port: int = listener.listeners[0].extra(anyio.abc.SocketAttribute.local_port)
    task_status.started(bound_port)
    async with listener:
        await listener.serve(partial(serve_connection, handler=handler))
