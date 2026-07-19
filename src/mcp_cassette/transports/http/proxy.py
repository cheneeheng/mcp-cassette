"""Streamable HTTP recording proxy.

A local reverse proxy: the agent points at the bound URL, the proxy forwards every
request to the real remote MCP server with ``httpx`` and taps a copy of each JSON-RPC
message into a :class:`~mcp_cassette.record.recorder.SessionRecorder`. Streaming is
passthrough — SSE events are forwarded as they arrive, never buffered — and bodies are
forwarded verbatim. Headers are never written to the cassette: exactly two things are
lifted from them, ``Mcp-Session-Id`` (stored once at cassette level) and the response
content type (framing decision).
"""

from __future__ import annotations

import sys
import time
from functools import partial

import anyio
import anyio.abc
import httpx

from ..._signals import wait_for_interrupt
from ...cassette import Cassette, Channel, RedactionRule, default_redaction_rules
from ...record import checkpoint
from ...record.checkpoint import DEFAULT_CHECKPOINT_INTERVAL
from ...record.recorder import SessionRecorder
from ...report import write_report
from . import wire
from .wire import HttpRequest, Responder, SseParser

_HOP_BY_HOP = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        # Forwarded verbatim bytes must stay parseable and re-servable, so upstream
        # compression is disabled (a hop-by-hop necessity, not a semantic rewrite).
        "accept-encoding",
    }
)


class RecordingProxy:
    """Record a live Streamable HTTP MCP session into a cassette.

    The proxy is observational only: bodies are forwarded verbatim, events are
    re-framed never, and the only headers injected are hop-by-hop necessities.
    ``Authorization`` (and every other header) is forwarded upstream untouched but
    never written — there is no cassette field it could occupy.
    """

    def __init__(
        self,
        server_url: str,
        cassette_path: str,
        redaction: list[RedactionRule] | None = None,
        include_default_redactions: bool = True,
        port: int = 0,
        report_path: str | None = None,
        max_idle: float | None = None,
        checkpoint_interval: float | None = DEFAULT_CHECKPOINT_INTERVAL,
    ) -> None:
        """Initialize the proxy.

        Args:
            server_url: The real remote MCP endpoint (e.g. ``https://.../mcp``).
            cassette_path: Where the recorded cassette is written on shutdown.
            redaction: Additional redaction rules beyond the defaults.
            include_default_redactions: Whether to prepend the default rule set.
            port: Local port to bind (``0`` = ephemeral; bound URL is reported).
            report_path: Optional path for a JSON session report (message count).
            max_idle: End the recording after this many seconds without client
                activity (the unattended-CI escape hatch; default off).
            checkpoint_interval: Seconds between crash-safety checkpoints to
                ``<cassette>.partial``; ``None`` or non-positive disables them.
        """
        self.server_url = server_url
        self.cassette_path = cassette_path
        self.report_path = report_path
        self.max_idle = max_idle
        self.checkpoint_interval = checkpoint_interval
        self._port = port
        rules: list[RedactionRule] = []
        if include_default_redactions:
            rules.extend(default_redaction_rules())
        if redaction:
            rules.extend(redaction)
        self._recorder = SessionRecorder(rules)
        self._exchange = 0
        self._session_id: str | None = None
        self._get_open = False
        self._upstream_ok = False
        self._fatal: str | None = None
        self._finalized = False
        self._interrupted = False
        self._last_activity = time.monotonic()
        self.bound_url: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._serve_scope: anyio.CancelScope | None = None
        self._run_scope: anyio.CancelScope | None = None

    @property
    def message_count(self) -> int:
        """Number of messages captured so far."""
        return self._recorder.message_count

    @property
    def fatal_error(self) -> str | None:
        """The first-contact failure that aborted the recording, if any."""
        return self._fatal

    def run(self) -> int:
        """Run the proxy to completion, returning a process exit code.

        Returns:
            ``0`` after a clean shutdown (e.g. ``--max-idle``), ``130`` after an
            operator interrupt, ``2`` when the upstream was unreachable at first
            contact (no cassette file is created — a cassette of nothing but a
            failed connect is noise).
        """
        return anyio.run(self._arun)

    async def _arun(self) -> int:
        async with anyio.create_task_group() as tg:
            self._run_scope = tg.cancel_scope
            url = await tg.start(self.serve)
            sys.stderr.write(
                f"mcp-cassette: recording at {url} -> point the agent there\n"
            )
            sys.stderr.flush()
            tg.start_soon(self._watch_signals, tg.cancel_scope)
            if self.max_idle is not None:
                tg.start_soon(self._watch_idle, tg.cancel_scope)
        if self._fatal is not None:
            sys.stderr.write(f"mcp-cassette record: {self._fatal}\n")
            return 2
        return 130 if self._interrupted else 0

    async def serve(
        self,
        *,
        task_status: anyio.abc.TaskStatus[str] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        """Serve until cancelled, reporting the bound URL via ``task_status``.

        On any exit path (cancellation included) the captured session is finalized
        into a cassette — unless the upstream failed at first contact, in which case
        no cassette file is created.
        """
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        try:
            async with anyio.create_task_group() as tg:
                self._serve_scope = tg.cancel_scope
                port = await tg.start(
                    partial(wire.serve_http, self._handle, port=self._port)
                )
                self.bound_url = f"http://127.0.0.1:{port}/mcp"
                task_status.started(self.bound_url)
                tg.start_soon(
                    checkpoint.run,
                    self.checkpoint_interval,
                    self._snapshot,
                    self.cassette_path,
                )
        finally:
            with anyio.CancelScope(shield=True):
                await self._client.aclose()
                self.finalize()

    def _snapshot(self) -> Cassette | None:
        # No file may exist for a session that never reached the upstream — a cassette
        # of nothing but a failed connect is noise, checkpoints included.
        if not self._upstream_ok or self._recorder.message_count == 0:
            return None
        return self._recorder.build(
            transport="http",
            server_url=self.server_url,
            session_id=self._session_id,
        )

    def finalize(self) -> None:
        """Write the cassette (and report) once; skipped after a first-contact error."""
        if self._finalized:
            return
        self._finalized = True
        if self._fatal is not None:
            checkpoint.discard(self.cassette_path)
            return
        cassette = self._recorder.build(
            transport="http",
            server_url=self.server_url,
            session_id=self._session_id,
        )
        cassette.save(self.cassette_path)
        checkpoint.discard(self.cassette_path)
        if self.report_path is not None:
            write_report(self.report_path, {"messages": self._recorder.message_count})

    async def _handle(self, request: HttpRequest, responder: Responder) -> None:
        self._last_activity = time.monotonic()
        if request.method == "POST":
            await self._handle_post(request, responder)
        elif request.method == "GET" and "text/event-stream" in request.headers.get(
            "accept", ""
        ):
            await self._handle_get(request, responder)
        else:
            await self._forward_plain(request, responder)

    def _next_exchange(self) -> int:
        n = self._exchange
        self._exchange += 1
        return n

    def _upstream_headers(self, request: HttpRequest) -> dict[str, str]:
        headers = {
            name: value
            for name, value in request.headers.items()
            if name not in _HOP_BY_HOP
        }
        headers["accept-encoding"] = "identity"
        return headers

    async def _send_upstream(
        self, request: HttpRequest, responder: Responder
    ) -> httpx.Response | None:
        assert self._client is not None
        upstream_request = self._client.build_request(
            request.method,
            self.server_url,
            content=request.body,
            headers=self._upstream_headers(request),
        )
        try:
            upstream = await self._client.send(upstream_request, stream=True)
        except httpx.TransportError as exc:
            await self._upstream_failure(responder, f"cannot reach upstream: {exc}")
            return None
        if not self._upstream_ok and upstream.status_code >= 500:
            await upstream.aclose()
            await self._upstream_failure(
                responder, f"upstream answered {upstream.status_code} at first contact"
            )
            return None
        self._upstream_ok = True
        if self._session_id is None:
            sid = upstream.headers.get("mcp-session-id")
            if sid is not None:
                self._session_id = sid
        return upstream

    async def _upstream_failure(self, responder: Responder, detail: str) -> None:
        message = f"{self.server_url}: {detail}"
        if not self._upstream_ok:
            self._fatal = message
        await responder.send(
            502, f"mcp-cassette: {message}\n".encode(), content_type="text/plain"
        )
        if self._fatal is not None:
            # Cancel the whole run, not just serve(): under run() the outer task
            # group also holds the signal watcher, which would otherwise keep the
            # process alive until an operator interrupt.
            for scope in (self._serve_scope, self._run_scope):
                if scope is not None:
                    scope.cancel()

    def _relay_headers(self, upstream: httpx.Response) -> list[tuple[str, str]]:
        skip = _HOP_BY_HOP | {"content-type", "content-encoding", "date"}
        return [
            (name, value)
            for name, value in upstream.headers.items()
            if name.lower() not in skip
        ]

    async def _handle_post(self, request: HttpRequest, responder: Responder) -> None:
        exchange = self._next_exchange()
        text = request.body.decode("utf-8", errors="replace")
        if text.strip():
            self._recorder.on_message("client", text, exchange=exchange)
        upstream = await self._send_upstream(request, responder)
        if upstream is None:
            return
        try:
            content_type = upstream.headers.get("content-type", "")
            if content_type.startswith("text/event-stream"):
                await self._relay_sse(upstream, responder, exchange, "post")
            else:
                body = await upstream.aread()
                if body.strip():
                    self._recorder.on_message(
                        "server",
                        body.decode("utf-8", errors="replace"),
                        exchange=exchange,
                        channel="post",
                    )
                await responder.send(
                    upstream.status_code,
                    body,
                    content_type=content_type or None,
                    headers=self._relay_headers(upstream),
                )
        finally:
            await upstream.aclose()

    async def _handle_get(self, request: HttpRequest, responder: Responder) -> None:
        if self._get_open:
            # The Streamable HTTP spec allows the server to hold one listening
            # stream; a second concurrent GET is refused.
            await responder.send(409, b"", content_type="text/plain")
            return
        exchange = self._next_exchange()
        self._get_open = True
        try:
            upstream = await self._send_upstream(request, responder)
            if upstream is None:
                return
            try:
                content_type = upstream.headers.get("content-type", "")
                if content_type.startswith("text/event-stream"):
                    await self._relay_sse(upstream, responder, exchange, "get")
                else:
                    body = await upstream.aread()
                    await responder.send(
                        upstream.status_code,
                        body,
                        content_type=content_type or None,
                        headers=self._relay_headers(upstream),
                    )
            finally:
                await upstream.aclose()
        finally:
            self._get_open = False

    async def _relay_sse(
        self,
        upstream: httpx.Response,
        responder: Responder,
        exchange: int,
        channel: Channel,
    ) -> None:
        # Forward each chunk to the client as it arrives (flush per event) while a
        # copy is parsed for the tap; buffering the stream would break mid-stream
        # server->client requests and distort timing.
        await responder.start(
            upstream.status_code,
            content_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=self._relay_headers(upstream),
        )
        parser = SseParser()
        try:
            async for chunk in upstream.aiter_raw():
                await responder.send_body(chunk)
                self._last_activity = time.monotonic()
                for event in parser.feed(chunk):
                    self._tap_event(event.data, exchange, channel)
        except (httpx.TransportError, anyio.BrokenResourceError):
            # Either side dropped mid-stream; keep what was captured.
            await responder.abort()
            return
        final = parser.finish()
        if final is not None:
            self._tap_event(final.data, exchange, channel)
        await responder.end()

    def _tap_event(self, data: str, exchange: int, channel: Channel) -> None:
        self._recorder.on_message("server", data, exchange=exchange, channel=channel)

    async def _forward_plain(self, request: HttpRequest, responder: Responder) -> None:
        upstream = await self._send_upstream(request, responder)
        if upstream is None:
            return
        try:
            body = await upstream.aread()
            await responder.send(
                upstream.status_code,
                body,
                content_type=upstream.headers.get("content-type") or None,
                headers=self._relay_headers(upstream),
            )
        finally:
            await upstream.aclose()

    async def _watch_idle(self, scope: anyio.CancelScope) -> None:
        assert self.max_idle is not None
        while True:
            await anyio.sleep(min(self.max_idle, 0.25))
            if time.monotonic() - self._last_activity > self.max_idle:
                scope.cancel()
                return

    async def _watch_signals(self, scope: anyio.CancelScope) -> None:
        await wait_for_interrupt()
        self._interrupted = True
        scope.cancel()
