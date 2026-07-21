"""Deterministic replay server.

Rebuilds a mock MCP server from a cassette: reads client JSON-RPC requests from stdin,
answers them from recorded responses (re-stamping the ``id``), emits recorded server
notifications anchored to their triggering request, and optionally injects faults. No
network, no subprocess, no wall-clock reads in the response path.
"""

from __future__ import annotations

import json
import sys
import warnings
from typing import Any

import anyio
import anyio.abc
from anyio.abc import ByteSendStream

from .._stdio import stdin_stream, stdout_stream
from ..cassette import Cassette, FaultOverlay, MatchConfig, Message, PaceConfig
from ..matching import Matcher
from ..record.pump import buffered_lines
from ..report import write_report
from .faults import Injector, make_error_response, make_malformed_line
from .pacing import Pacer
from .server_requests import ServerRequestTracker

UNMATCHED_CODE = -32001


def apply_protocol_version(
    config: MatchConfig,
    request_obj: dict[str, Any],
    response_obj: dict[str, Any],
) -> None:
    """Apply the ``rewrite_protocol_version`` policy to an initialize response.

    Shared by the stdio and HTTP replay servers — matching and rewrite semantics
    are transport-independent by design.

    Args:
        config: The active matching configuration.
        request_obj: The live client's initialize request.
        response_obj: The recorded initialize response about to be sent (mutated).
    """
    result = response_obj.get("result")
    if not isinstance(result, dict):
        return
    recorded_pv = result.get("protocolVersion")
    params = request_obj.get("params")
    requested_pv = params.get("protocolVersion") if isinstance(params, dict) else None
    mismatch = (
        requested_pv is not None
        and recorded_pv is not None
        and requested_pv != recorded_pv
    )
    if config.rewrite_protocol_version:
        if requested_pv is not None:
            result["protocolVersion"] = requested_pv
    elif mismatch:
        warnings.warn(
            f"mcp-cassette: client requested protocolVersion {requested_pv} but "
            f"cassette recorded {recorded_pv}; replaying recorded value",
            stacklevel=2,
        )


class _Disconnect(Exception):  # noqa: N818 — internal control-flow signal, not an error
    """Internal signal: a disconnect fault fired; close pipes and exit 0."""


class ReplayServer:
    """Serve recorded responses from a cassette as a drop-in stdio MCP server."""

    def __init__(
        self,
        cassette: Cassette,
        match: MatchConfig | None = None,
        faults: FaultOverlay | None = None,
        report_path: str | None = None,
        pace: PaceConfig | None = None,
    ) -> None:
        """Initialize the replay server.

        Args:
            cassette: The loaded cassette to serve.
            match: Matching configuration (defaults to :class:`MatchConfig` defaults).
            faults: Optional fault overlay applied at replay time.
            report_path: Optional path to write a JSON session report (misses), used by
                the pytest fixture to fail tests across processes.
            pace: Optional pacing configuration; off by default, in which case the
                response path performs no sleep and reads no clock.
        """
        self.report_path = report_path
        self.cassette = cassette
        self.config = match or MatchConfig()
        self._matcher = Matcher(cassette, self.config)
        self._injector = Injector(faults)
        self._pacer = Pacer(pace)
        self._tracker = ServerRequestTracker(cassette)
        self._initialize_exchange = self._find_initialize_exchange()
        self._emitted_leading = False
        self._out_lock = anyio.Lock()
        self._disconnect_requested = False
        self._deferred: anyio.abc.TaskGroup | None = None

    def run(self) -> int:
        """Run the server to completion, returning a process exit code.

        Returns:
            ``0`` on a clean session with no misses, ``3`` if any request went
            unmatched (the CI-visible failure signal), ``0`` on a disconnect fault.
        """
        return anyio.run(self._arun)

    async def _arun(self) -> int:
        stdin = stdin_stream()
        stdout = stdout_stream()
        async with anyio.create_task_group() as tg:
            self._deferred = tg
            async for line in buffered_lines(stdin):
                try:
                    await self._handle_line(line, stdout)
                except _Disconnect:
                    # Raised on the direct (ungated) response path; the task
                    # group would wrap it in an ExceptionGroup, so convert to
                    # the flag the gated path already uses.
                    self._disconnect_requested = True
                    break
            # EOF or disconnect: abandon still-gated responses — stdio shutdown
            # is EOF-driven, and the pending-request summary names the cause.
            tg.cancel_scope.cancel()
        if self._disconnect_requested:
            await stdout.aclose()
            self._write_report()
            return 0
        self._report_unused_faults()
        self._write_report()
        self._print_pending_server_requests()
        if self._matcher.misses:
            self._print_miss_summary()
            return 3
        return 0

    def _write_report(self) -> None:
        if self.report_path is not None:
            write_report(self.report_path, {"misses": self._matcher.misses})

    async def _handle_line(self, line: bytes, out: ByteSendStream) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return  # ignore junk from the client
        if not isinstance(obj, dict):
            return
        method = obj.get("method")
        has_id = "id" in obj
        if method is not None and has_id:
            await self._handle_request(obj, out)
        elif method is None and has_id:
            # A client response: if it answers an emitted server request it opens
            # the release gate (accept-anything); stray responses need no reply.
            self._tracker.on_client_message(obj)
        # notifications need no reply

    async def _handle_request(self, obj: dict[str, Any], out: ByteSendStream) -> None:
        method = obj.get("method")
        msg_id = obj.get("id")
        if method == "initialize":
            await self._handle_initialize(obj, out)
            return

        exchange = self._matcher.find(obj)
        if exchange is None or exchange.response is None:
            await self._send(out, self._unmatched_error(obj))
            return

        fault = self._injector.consult(method)
        recorded = self._restamp(exchange.response, msg_id)
        # Emit server requests recorded inside this exchange (sampling/elicitation
        # triggered by the call) before the response, then honor the release gate:
        # the recorded response only existed because the agent answered. A gated
        # response is finished in a deferred task so later stdin lines (the agent's
        # answer, other calls) keep being processed.
        await self._emit_server_requests(
            exchange.request.seq, "during", out, exchange.request
        )
        if self._tracker.would_block(exchange.response.seq, None):
            assert self._deferred is not None  # set before any line is handled
            self._deferred.start_soon(
                self._finish_gated, fault, recorded, msg_id, exchange, out
            )
            return
        await self._apply_fault_and_respond(
            fault,
            recorded,
            msg_id,
            exchange.notifications,
            out,
            response_msg=exchange.response,
            prev=exchange.request,
        )
        await self._emit_server_requests(
            exchange.request.seq, "after", out, exchange.response
        )

    async def _finish_gated(
        self,
        fault: Any,
        recorded: dict[str, Any],
        msg_id: str | int | None,
        exchange: Any,
        out: ByteSendStream,
    ) -> None:
        assert exchange.response is not None
        try:
            await self._tracker.wait_ready(exchange.response.seq, None)
            await self._apply_fault_and_respond(
                fault,
                recorded,
                msg_id,
                exchange.notifications,
                out,
                response_msg=exchange.response,
                prev=exchange.request,
            )
            await self._emit_server_requests(
                exchange.request.seq, "after", out, exchange.response
            )
        except _Disconnect:
            self._disconnect_requested = True
            assert self._deferred is not None
            self._deferred.cancel_scope.cancel()

    async def _handle_initialize(
        self, obj: dict[str, Any], out: ByteSendStream
    ) -> None:
        msg_id = obj.get("id")
        init = self._initialize_exchange
        if init is None or init.response is None:
            await self._send(
                out,
                make_error_response(
                    msg_id,
                    UNMATCHED_CODE,
                    "mcp-cassette: no recorded initialize response",
                ),
            )
            return
        response = self._restamp(init.response, msg_id)
        apply_protocol_version(self.config, obj, response)
        fault = self._injector.consult("initialize")
        await self._apply_fault_and_respond(
            fault,
            response,
            msg_id,
            init.notifications,
            out,
            response_msg=init.response,
            prev=init.request,
        )
        await self._emit_leading_notifications(out, init.response)
        await self._emit_server_requests(None, "initialize", out, init.response)
        await self._emit_server_requests(init.request.seq, "during", out, init.response)
        await self._emit_server_requests(init.request.seq, "after", out, init.response)

    async def _apply_fault_and_respond(
        self,
        fault: Any,
        response_obj: dict[str, Any],
        msg_id: str | int | None,
        notifications: list[Message],
        out: ByteSendStream,
        response_msg: Message,
        prev: Message | None,
    ) -> None:
        # Order is pace, then fault: a delay fault is additive on top of recorded
        # latency ("the server was already slow, then got slower"). A timeout never
        # responds, so its pacing sleep is skipped rather than spent before silence.
        if fault is not None and fault.type == "timeout":
            return  # never respond; queue position is spent
        await self._pacer.wait(prev, response_msg)
        if fault is None:
            await self._send(out, response_obj)
            await self._emit_notifications(notifications, out, response_msg)
            return

        ftype = fault.type
        if ftype == "delay":
            await anyio.sleep(fault.params.get("ms", 0) / 1000)
            await self._send(out, response_obj)
            await self._emit_notifications(notifications, out, response_msg)
        elif ftype == "error":
            err = make_error_response(
                msg_id,
                fault.params.get("code", -32603),
                fault.params.get("message", "mcp-cassette injected error"),
            )
            await self._send(out, err)
            await self._emit_notifications(notifications, out, response_msg)
        elif ftype == "malformed":
            strategy = fault.params.get("strategy", "truncate")
            async with self._out_lock:
                await out.send(make_malformed_line(response_obj, strategy))
            await self._emit_notifications(notifications, out, response_msg)
        elif ftype == "disconnect":  # pragma: no branch — FaultType is exhaustive
            if fault.params.get("after_response", False):
                await self._send(out, response_obj)
            raise _Disconnect

    async def _emit_notifications(
        self,
        notifications: list[Message],
        out: ByteSendStream,
        prev: Message | None = None,
    ) -> None:
        # `prev` walks forward so a burst of progress notifications keeps its
        # recorded rhythm instead of all arriving at the response's offset.
        for note in notifications:
            if isinstance(note.payload, dict):
                await self._pacer.wait(prev, note)
                await self._send(out, note.payload)
            prev = note

    async def _emit_leading_notifications(
        self, out: ByteSendStream, prev: Message | None = None
    ) -> None:
        if self._emitted_leading:
            return
        self._emitted_leading = True
        await self._emit_notifications(self._matcher.leading_notifications, out, prev)

    async def _emit_server_requests(
        self,
        anchor_seq: int | None,
        trigger: str,
        out: ByteSendStream,
        prev: Message | None = None,
    ) -> None:
        # Emitted with the *recorded* msg_id (the payload carries it verbatim), so
        # the agent's response can be matched back to the recording by JSON-RPC id.
        for state in self._tracker.triggered_by(anchor_seq, trigger):  # type: ignore[arg-type]  # Trigger literal
            if isinstance(state.message.payload, dict):
                await self._pacer.wait(prev, state.message)
                await self._send(out, state.message.payload)
                self._tracker.mark_emitted(state)
            prev = state.message

    async def _send(self, out: ByteSendStream, obj: dict[str, Any]) -> None:
        # Deferred gated responses write concurrently with the read loop; the lock
        # keeps line framing intact.
        async with self._out_lock:
            await out.send((json.dumps(obj) + "\n").encode("utf-8"))

    def _restamp(self, response: Message, msg_id: str | int | None) -> dict[str, Any]:
        payload = response.payload
        obj: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
        obj["id"] = msg_id
        return obj

    def _unmatched_error(self, obj: dict[str, Any]) -> dict[str, Any]:
        method = obj.get("method", "<none>")
        digest = json.dumps(obj.get("params"), sort_keys=True, separators=(",", ":"))
        return make_error_response(
            obj.get("id"),
            UNMATCHED_CODE,
            f"mcp-cassette: no recorded interaction matches {method} (params={digest})",
        )

    def _find_initialize_exchange(self) -> Any:
        for ex in self._matcher_exchanges():
            payload = ex.request.payload
            if isinstance(payload, dict) and payload.get("method") == "initialize":
                return ex
        return None

    def _matcher_exchanges(self) -> list[Any]:
        return self._matcher._exchanges  # noqa: SLF001 — same package, intentional

    def _report_unused_faults(self) -> None:
        for fault in self._injector.unused_faults():
            warnings.warn(
                f"mcp-cassette: fault {fault.type} on {fault.target.method} matched "
                "nothing in this session",
                stacklevel=2,
            )

    def _print_miss_summary(self) -> None:
        sys.stderr.write(
            f"mcp-cassette: {len(self._matcher.misses)} unmatched request(s):\n"
        )
        for miss in self._matcher.misses:
            sys.stderr.write(f"  - {miss}\n")
        sys.stderr.flush()

    def _print_pending_server_requests(self) -> None:
        pending = self._tracker.pending_summaries()
        if not pending:
            return
        sys.stderr.write(
            f"mcp-cassette: {len(pending)} server-initiated request(s) still pending "
            "(gated messages never released):\n"
        )
        for line in pending:
            sys.stderr.write(f"  - {line}\n")
        sys.stderr.flush()
