"""Deterministic Streamable HTTP replay server.

Rebuilds a local mock MCP server from an http cassette: answers ``POST /mcp`` from
recorded responses (re-stamping the JSON-RPC ``id``), mirrors each exchange's recorded
response mode (single JSON body vs SSE stream), delivers recorded GET-stream messages
anchored to their triggering exchange, emits recorded server-initiated requests with
release-on-response gating, and injects faults at the same hook point as stdio replay
(after match, before the response write). No network in the response path; the only
outbound traffic is the ``new_episodes`` fall-through.
"""

from __future__ import annotations

import hashlib
import json
import sys
from functools import partial
from typing import Any

import anyio
import anyio.abc
import httpx

from ..._signals import wait_for_interrupt
from ...cassette import (
    Cassette,
    FaultOverlay,
    MatchConfig,
    Message,
    default_redaction_rules,
)
from ...matching import Exchange, Matcher
from ...record.recorder import SessionRecorder
from ...replay.faults import Injector, make_error_response
from ...replay.server import UNMATCHED_CODE, apply_protocol_version
from ...replay.server_requests import ServerRequestTracker
from ...report import write_report
from . import wire
from .wire import HttpRequest, Responder, encode_sse_event

_ACCEPT_BOTH = "application/json, text/event-stream"


class HttpReplayServer:
    """Serve recorded responses from an http cassette as a drop-in mock server."""

    def __init__(
        self,
        cassette: Cassette,
        match: MatchConfig | None = None,
        faults: FaultOverlay | None = None,
        port: int = 0,
        report_path: str | None = None,
        fallthrough_url: str | None = None,
        cassette_path: str | None = None,
    ) -> None:
        """Initialize the replay server.

        Args:
            cassette: The loaded http cassette to serve.
            match: Matching configuration (transport-independent, v1 semantics).
            faults: Optional fault overlay applied at replay time.
            port: Local port to bind (``0`` = ephemeral; bound URL is reported).
            report_path: Optional path for a JSON session report (misses).
            fallthrough_url: Real server URL for ``new_episodes``: a replay miss is
                forwarded live and the novel exchange appended to the cassette.
            cassette_path: Where the merged cassette is saved (``new_episodes``).

        Raises:
            ValueError: If the cassette's transport is not ``http`` (a stdio
                cassette belongs to :class:`~mcp_cassette.replay.server.ReplayServer`
                / ``server_command``).
        """
        if cassette.transport != "http":
            raise ValueError(
                f"cassette transport is {cassette.transport!r}, not 'http'; replay "
                "stdio cassettes with ReplayServer / mcp_cassette.server_command"
            )
        self.cassette = cassette
        self.config = match or MatchConfig()
        self.report_path = report_path
        self._port = port
        self._matcher = Matcher(cassette, self.config)
        self._injector = Injector(faults)
        self._tracker = ServerRequestTracker(cassette)
        digest = hashlib.sha256(cassette.model_dump_json().encode("utf-8")).hexdigest()
        self.session_id = f"mcc-{digest[:8]}"
        self._issued = False
        self._match_lock = anyio.Lock()
        self._disconnected = False
        self._finalized = False
        self.bound_url: str | None = None
        self._serve_scope: anyio.CancelScope | None = None

        self._post_messages: dict[int, list[Message]] = {}
        for m in cassette.messages:
            if (
                m.sender == "server"
                and m.channel == "post"
                and m.exchange is not None
                and m.kind != "raw"
            ):
                self._post_messages.setdefault(m.exchange, []).append(m)

        requests = [
            m for m in cassette.messages if m.sender == "client" and m.kind == "request"
        ]
        self._get_plan = [
            m for m in cassette.messages if m.channel == "get" and m.kind != "raw"
        ]
        self._get_anchor: dict[int, int | None] = {}
        for m in self._get_plan:
            anchor: int | None = None
            for r in requests:
                if r.seq < m.seq:
                    anchor = r.seq
                else:
                    break
            self._get_anchor[m.seq] = anchor
        self._get_unreleased = list(self._get_plan)
        self._get_ready: list[Message] = []
        self._get_event = anyio.Event()
        self._get_connected = False
        self._get_delivered = 0

        self._initialize_exchange = self._find_initialize_exchange()

        self._fallthrough_url = fallthrough_url
        self._cassette_path = cassette_path
        self._up_client: httpx.AsyncClient | None = None
        self._up_session: str | None = None
        self._up_lock = anyio.Lock()
        self._new_recorder = (
            SessionRecorder(default_redaction_rules())
            if fallthrough_url is not None
            else None
        )
        recorded_exchanges = [
            m.exchange for m in cassette.messages if m.exchange is not None
        ]
        self._new_exchange = (max(recorded_exchanges) + 1) if recorded_exchanges else 0

    @property
    def misses(self) -> list[str]:
        """Unmatched-request summaries seen so far (fixture failure signal)."""
        return self._matcher.misses

    def run(self) -> int:
        """Run until interrupted, returning a process exit code.

        Returns:
            ``0`` on a clean session (or after a disconnect fault, or under
            ``new_episodes`` where misses fall through by design), ``3`` if any
            request went unmatched — the CI-visible failure signal.
        """
        return anyio.run(self._arun)

    async def _arun(self) -> int:
        async with anyio.create_task_group() as tg:
            url = await tg.start(self.serve)
            sys.stderr.write(f"mcp-cassette: replaying at {url}\n")
            sys.stderr.flush()
            tg.start_soon(self._watch_signals, tg.cancel_scope)
        if self._disconnected or self._fallthrough_url is not None:
            return 0
        return 3 if self._matcher.misses else 0

    async def _watch_signals(self, scope: anyio.CancelScope) -> None:
        await wait_for_interrupt()
        scope.cancel()

    async def serve(
        self,
        *,
        task_status: anyio.abc.TaskStatus[str] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        """Serve until cancelled, reporting the bound URL via ``task_status``."""
        try:
            async with anyio.create_task_group() as tg:
                self._serve_scope = tg.cancel_scope
                port = await tg.start(
                    partial(wire.serve_http, self._handle, port=self._port)
                )
                self.bound_url = f"http://127.0.0.1:{port}/mcp"
                task_status.started(self.bound_url)
        finally:
            with anyio.CancelScope(shield=True):
                if self._up_client is not None:
                    await self._up_client.aclose()
                self.finalize()

    def finalize(self) -> None:
        """Write the report (and merged cassette for ``new_episodes``) once."""
        if self._finalized:
            return
        self._finalized = True
        self._print_summaries()
        if self._new_recorder is not None and self._cassette_path is not None:
            merged: list[Message] = list(self.cassette.messages)
            next_seq = len(merged)
            for msg in self._new_recorder.build().messages:
                merged.append(msg.model_copy(update={"seq": next_seq}))
                next_seq += 1
            result = self.cassette.model_copy(update={"messages": merged})
            result.save(self._cassette_path)
            if self.report_path is not None:
                write_report(self.report_path, {"messages": len(merged)})
            return
        if self.report_path is not None:
            write_report(self.report_path, {"misses": self._matcher.misses})

    def _print_summaries(self) -> None:
        pending = self._tracker.pending_summaries()
        if pending:
            sys.stderr.write(
                f"mcp-cassette: {len(pending)} server-initiated request(s) still "
                "pending (gated messages never released):\n"
            )
            for line in pending:
                sys.stderr.write(f"  - {line}\n")
        undelivered = len(self._get_plan) - self._get_delivered
        if undelivered > 0:
            sys.stderr.write(
                f"mcp-cassette: warning: {undelivered} recorded GET-stream message(s) "
                "were never delivered (client did not read the GET stream to the "
                "release point; a client that ignores optional streams is "
                "conforming)\n"
            )
        if self._matcher.misses and self._fallthrough_url is None:
            sys.stderr.write(
                f"mcp-cassette: {len(self._matcher.misses)} unmatched request(s):\n"
            )
            for miss in self._matcher.misses:
                sys.stderr.write(f"  - {miss}\n")
        sys.stderr.flush()

    # ------------------------------------------------------------------ handlers

    async def _handle(self, request: HttpRequest, responder: Responder) -> None:
        if request.method == "POST":
            await self._handle_post(request, responder)
        elif request.method == "GET" and "text/event-stream" in request.headers.get(
            "accept", ""
        ):
            await self._handle_get(request, responder)
        elif request.method == "DELETE":
            await responder.send(200, b"")
        else:
            await responder.send(405, b"", headers=[("allow", "GET, POST, DELETE")])

    async def _handle_post(self, request: HttpRequest, responder: Responder) -> None:
        try:
            obj = json.loads(request.body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            await responder.send(400, b"", content_type="text/plain")
            return
        if not isinstance(obj, dict):
            await responder.send(400, b"", content_type="text/plain")
            return
        method = obj.get("method")
        has_id = "id" in obj
        if method is not None and has_id:
            await self._handle_request(request, obj, responder)
        elif method is not None:
            await responder.send(202, b"")
        else:
            # A client response: if it answers an emitted server request it opens
            # the release gate (accept-anything). Either way, 202 per the spec.
            self._tracker.on_client_message(obj)
            await responder.send(202, b"")

    async def _handle_request(
        self, request: HttpRequest, obj: dict[str, Any], responder: Responder
    ) -> None:
        method = obj.get("method")
        if method == "initialize":
            await self._handle_initialize(obj, responder)
            return
        if not self._session_ok(request):
            # Per the Streamable HTTP spec a missing/unknown session id is 404,
            # which well-behaved clients handle by re-initializing.
            await responder.send(404, b"", content_type="text/plain")
            return
        async with self._match_lock:
            exchange = self._matcher.find(obj)
            fault = self._injector.consult(method) if exchange is not None else None
        if exchange is None or exchange.response is None:
            if self._fallthrough_url is not None:
                await self._fallthrough(request, responder)
                return
            await self._send_unmatched(obj, responder)
            return
        await self._respond_matched(exchange, fault, obj, responder)

    async def _handle_initialize(
        self, obj: dict[str, Any], responder: Responder
    ) -> None:
        init = self._initialize_exchange
        if init is None or init.response is None:
            err = make_error_response(
                obj.get("id"),
                UNMATCHED_CODE,
                "mcp-cassette: no recorded initialize response",
            )
            await responder.send(
                200, json.dumps(err).encode(), content_type="application/json"
            )
            return
        async with self._match_lock:
            fault = self._injector.consult("initialize")
        self._issued = True

        def transform(payload: dict[str, Any]) -> None:
            apply_protocol_version(self.config, obj, payload)

        await self._respond_matched(
            init,
            fault,
            obj,
            responder,
            extra_headers=[("mcp-session-id", self.session_id)],
            transform=transform,
        )
        self._release_get(None)

    def _session_ok(self, request: HttpRequest) -> bool:
        return self._issued and (
            request.headers.get("mcp-session-id") == self.session_id
        )

    # ------------------------------------------------------------ matched serving

    async def _respond_matched(
        self,
        exchange: Exchange,
        fault: Any,
        obj: dict[str, Any],
        responder: Responder,
        extra_headers: list[tuple[str, str]] | None = None,
        transform: Any = None,
    ) -> None:
        assert exchange.response is not None
        live_id = obj.get("id")
        if fault is not None:
            handled = await self._apply_fault(
                fault, exchange, live_id, responder, extra_headers, transform
            )
            if handled:
                return
        await self._serve_exchange(
            exchange, live_id, responder, extra_headers, transform
        )
        self._release_get(exchange.request.seq)

    async def _serve_exchange(
        self,
        exchange: Exchange,
        live_id: str | int | None,
        responder: Responder,
        extra_headers: list[tuple[str, str]] | None = None,
        transform: Any = None,
    ) -> None:
        assert exchange.response is not None
        response_msg = exchange.response
        ex_id = exchange.request.exchange
        msgs = self._post_messages.get(ex_id, []) if ex_id is not None else []
        sse_mode = len(msgs) > 1
        if not sse_mode:
            # Recorded as a single JSON body -> replay as application/json.
            payload = self._restamp(response_msg, live_id)
            if transform is not None:
                transform(payload)
            await self._tracker.wait_ready(response_msg.seq, ex_id)
            await responder.send(
                200,
                json.dumps(payload).encode("utf-8"),
                content_type="application/json",
                headers=extra_headers,
            )
            return
        # Recorded as an SSE stream -> replay the exchange's messages in seq order
        # (progress notifications before the response, exactly as captured), then
        # close the stream. t_offset_ms is ignored — no sleeps.
        await responder.start(
            200, content_type="text/event-stream", headers=extra_headers
        )
        for m in msgs:
            await self._tracker.wait_ready(m.seq, ex_id)
            if m.seq == response_msg.seq:
                payload = self._restamp(response_msg, live_id)
                if transform is not None:
                    transform(payload)
            elif isinstance(m.payload, dict):
                payload = m.payload
            else:  # pragma: no cover — raw messages are excluded at grouping
                continue
            await responder.send_body(encode_sse_event(json.dumps(payload)))
            if m.kind == "request":
                state = self._tracker.state_for_seq(m.seq)
                if state is not None:
                    self._tracker.mark_emitted(state)
        await responder.end()

    async def _apply_fault(
        self,
        fault: Any,
        exchange: Exchange,
        live_id: str | int | None,
        responder: Responder,
        extra_headers: list[tuple[str, str]] | None,
        transform: Any,
    ) -> bool:
        assert exchange.response is not None
        ftype = fault.type
        if ftype == "delay":
            await anyio.sleep(fault.params.get("ms", 0) / 1000)
            return False
        if ftype == "timeout":
            # Headers sent, body never finishes; other connections keep serving —
            # a hung tool, not a dead server.
            ex_id = exchange.request.exchange
            msgs = self._post_messages.get(ex_id, []) if ex_id is not None else []
            content_type = "text/event-stream" if len(msgs) > 1 else "application/json"
            await responder.start(200, content_type=content_type)
            await anyio.sleep_forever()
        if ftype == "error":
            err = make_error_response(
                live_id,
                fault.params.get("code", -32603),
                fault.params.get("message", "mcp-cassette injected error"),
            )
            await responder.send(
                200, json.dumps(err).encode(), content_type="application/json"
            )
            return True
        if ftype == "malformed":
            strategy = fault.params.get("strategy", "truncate")
            payload = self._restamp(exchange.response, live_id)
            if strategy == "not_json":
                await responder.send(
                    200, b"this is not json", content_type="application/json"
                )
            elif strategy == "wrong_id":
                payload["id"] = "mcp-cassette-unknown-id"
                await responder.send(
                    200, json.dumps(payload).encode(), content_type="application/json"
                )
            else:  # truncate: close mid-body after partial bytes
                text = json.dumps(payload)
                await responder.start(200, content_type="application/json")
                await responder.send_body(text[: max(1, len(text) // 2)].encode())
                await responder.abort()
            return True
        # disconnect: close the TCP connection — before the response (default) or
        # just after; a live GET stream is closed too (server death kills all).
        if fault.params.get("after_response", False):
            await self._serve_exchange(
                exchange, live_id, responder, extra_headers, transform
            )
        await responder.abort()
        self._disconnected = True
        if self._serve_scope is not None:  # pragma: no branch — set before serving
            self._serve_scope.cancel()
        return True

    def _restamp(self, response: Message, live_id: str | int | None) -> dict[str, Any]:
        payload = response.payload
        obj: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
        obj["id"] = live_id
        return obj

    async def _send_unmatched(self, obj: dict[str, Any], responder: Responder) -> None:
        # Delivered as a 200 JSON body — a transport-level error would mask the
        # message that names the miss.
        method = obj.get("method", "<none>")
        digest = json.dumps(obj.get("params"), sort_keys=True, separators=(",", ":"))
        err = make_error_response(
            obj.get("id"),
            UNMATCHED_CODE,
            f"mcp-cassette: no recorded interaction matches {method} (params={digest})",
        )
        await responder.send(
            200, json.dumps(err).encode(), content_type="application/json"
        )

    def _find_initialize_exchange(self) -> Exchange | None:
        for ex in self._matcher._exchanges:  # noqa: SLF001 — same package, intentional
            payload = ex.request.payload
            if isinstance(payload, dict) and payload.get("method") == "initialize":
                return ex
        return None

    # ------------------------------------------------------------------ GET stream

    async def _handle_get(self, request: HttpRequest, responder: Responder) -> None:
        if not self._session_ok(request):
            await responder.send(404, b"", content_type="text/plain")
            return
        if self._get_connected:
            await responder.send(409, b"", content_type="text/plain")
            return
        self._get_connected = True
        try:
            await responder.start(200, content_type="text/event-stream")
            while True:
                message = await self._next_get_message()
                await self._tracker.wait_ready(message.seq, message.exchange)
                if not isinstance(message.payload, dict):
                    continue  # pragma: no cover — raw messages excluded at planning
                try:
                    await responder.send_body(
                        encode_sse_event(json.dumps(message.payload))
                    )
                except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                    return  # client closed its listening stream
                self._get_delivered += 1
                if message.kind == "request":
                    state = self._tracker.state_for_seq(message.seq)
                    if state is not None:
                        self._tracker.mark_emitted(state)
        finally:
            self._get_connected = False

    async def _next_get_message(self) -> Message:
        while True:
            if self._get_ready:
                return self._get_ready.pop(0)
            event = self._get_event
            await event.wait()

    def _release_get(self, anchor_seq: int | None) -> None:
        released = [
            m for m in self._get_unreleased if self._get_anchor[m.seq] == anchor_seq
        ]
        if not released:
            return
        released_seqs = {m.seq for m in released}
        self._get_unreleased = [
            m for m in self._get_unreleased if m.seq not in released_seqs
        ]
        self._get_ready.extend(released)
        event = self._get_event
        self._get_event = anyio.Event()
        event.set()

    # ------------------------------------------------------------- new_episodes

    async def _fallthrough(self, request: HttpRequest, responder: Responder) -> None:
        assert self._fallthrough_url is not None
        assert self._new_recorder is not None
        async with self._up_lock:
            client = await self._ensure_upstream(responder)
        if client is None:
            return
        exchange = self._new_exchange
        self._new_exchange += 1
        self._new_recorder.on_message(
            "client",
            request.body.decode("utf-8", errors="replace"),
            exchange=exchange,
        )
        headers = {
            "accept": _ACCEPT_BOTH,
            "content-type": request.headers.get("content-type", "application/json"),
            "accept-encoding": "identity",
        }
        if self._up_session is not None:
            headers["mcp-session-id"] = self._up_session
        try:
            upstream = await client.send(
                client.build_request(
                    "POST",
                    self._fallthrough_url,
                    content=request.body,
                    headers=headers,
                ),
                stream=True,
            )
        except httpx.TransportError as exc:
            await responder.send(
                502,
                f"mcp-cassette: new_episodes fall-through failed: {exc}\n".encode(),
                content_type="text/plain",
            )
            return
        try:
            content_type = upstream.headers.get("content-type", "")
            if content_type.startswith("text/event-stream"):
                await responder.start(upstream.status_code, content_type=content_type)
                parser = wire.SseParser()
                async for chunk in upstream.aiter_raw():
                    await responder.send_body(chunk)
                    for event in parser.feed(chunk):
                        self._new_recorder.on_message(
                            "server", event.data, exchange=exchange, channel="post"
                        )
                final = parser.finish()
                if final is not None:
                    self._new_recorder.on_message(
                        "server", final.data, exchange=exchange, channel="post"
                    )
                await responder.end()
            else:
                body = await upstream.aread()
                if body.strip():
                    self._new_recorder.on_message(
                        "server",
                        body.decode("utf-8", errors="replace"),
                        exchange=exchange,
                        channel="post",
                    )
                await responder.send(
                    upstream.status_code,
                    body,
                    content_type=content_type or None,
                )
        finally:
            await upstream.aclose()

    async def _ensure_upstream(self, responder: Responder) -> httpx.AsyncClient | None:
        """Open the live session lazily: handshake once, on the first miss.

        The synthetic handshake is ours, not the agent's traffic, so it is not
        recorded — only the novel exchange is appended.
        """
        if self._up_client is not None:
            return self._up_client
        assert self._fallthrough_url is not None
        init = self._initialize_exchange
        init_payload: dict[str, Any]
        if init is not None and isinstance(init.request.payload, dict):
            init_payload = dict(init.request.payload)
        else:  # pragma: no cover — an http cassette always records initialize
            init_payload = {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
            }
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        try:
            response = await client.post(
                self._fallthrough_url,
                json=init_payload,
                headers={"accept": _ACCEPT_BOTH, "accept-encoding": "identity"},
            )
            self._up_session = response.headers.get("mcp-session-id")
            await response.aread()
            notify_headers = {"accept": _ACCEPT_BOTH}
            if self._up_session is not None:
                notify_headers["mcp-session-id"] = self._up_session
            await client.post(
                self._fallthrough_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=notify_headers,
            )
        except httpx.TransportError as exc:
            await client.aclose()
            await responder.send(
                502,
                f"mcp-cassette: cannot reach {self._fallthrough_url}: {exc}\n".encode(),
                content_type="text/plain",
            )
            return None
        self._up_client = client
        return client
