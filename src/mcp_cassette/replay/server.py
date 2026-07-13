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
from anyio.abc import ByteSendStream

from .._stdio import stdin_stream, stdout_stream
from ..cassette import (
    Cassette,
    FaultOverlay,
    MatchConfig,
    Message,
    UnsupportedCassetteFeature,
)
from ..matching import Matcher, detect_server_initiated_requests
from ..record.pump import buffered_lines
from ..report import write_report
from .faults import Injector, make_error_response, make_malformed_line

UNMATCHED_CODE = -32001


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
    ) -> None:
        """Initialize the replay server.

        Args:
            cassette: The loaded cassette to serve.
            match: Matching configuration (defaults to :class:`MatchConfig` defaults).
            faults: Optional fault overlay applied at replay time.
            report_path: Optional path to write a JSON session report (misses), used by
                the pytest fixture to fail tests across processes.

        Raises:
            UnsupportedCassetteFeature: If the cassette contains server-initiated
                requests (sampling/elicitation), which replay cannot serve in the MVP.
        """
        self.report_path = report_path
        if detect_server_initiated_requests(cassette):
            raise UnsupportedCassetteFeature(
                "server-initiated requests; see roadmap"
            )
        self.cassette = cassette
        self.config = match or MatchConfig()
        self._matcher = Matcher(cassette, self.config)
        self._injector = Injector(faults)
        self._initialize_exchange = self._find_initialize_exchange()
        self._emitted_leading = False

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
        try:
            async for line in buffered_lines(stdin):
                await self._handle_line(line, stdout)
        except _Disconnect:
            await stdout.aclose()
            self._write_report()
            return 0
        self._report_unused_faults()
        self._write_report()
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
        # notifications and stray client responses need no reply

    async def _handle_request(
        self, obj: dict[str, Any], out: ByteSendStream
    ) -> None:
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
        await self._apply_fault_and_respond(
            fault, recorded, msg_id, exchange.notifications, out
        )

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
        self._apply_protocol_version(obj, response)
        fault = self._injector.consult("initialize")
        await self._apply_fault_and_respond(
            fault, response, msg_id, init.notifications, out
        )
        await self._emit_leading_notifications(out)

    async def _apply_fault_and_respond(
        self,
        fault: Any,
        response_obj: dict[str, Any],
        msg_id: str | int | None,
        notifications: list[Message],
        out: ByteSendStream,
    ) -> None:
        if fault is None:
            await self._send(out, response_obj)
            await self._emit_notifications(notifications, out)
            return

        ftype = fault.type
        if ftype == "delay":
            await anyio.sleep(fault.params.get("ms", 0) / 1000)
            await self._send(out, response_obj)
            await self._emit_notifications(notifications, out)
        elif ftype == "timeout":
            return  # never respond; queue position is spent
        elif ftype == "error":
            err = make_error_response(
                msg_id,
                fault.params.get("code", -32603),
                fault.params.get("message", "mcp-cassette injected error"),
            )
            await self._send(out, err)
            await self._emit_notifications(notifications, out)
        elif ftype == "malformed":
            strategy = fault.params.get("strategy", "truncate")
            await out.send(make_malformed_line(response_obj, strategy))
            await self._emit_notifications(notifications, out)
        elif ftype == "disconnect":
            if fault.params.get("after_response", False):
                await self._send(out, response_obj)
            raise _Disconnect

    async def _emit_notifications(
        self, notifications: list[Message], out: ByteSendStream
    ) -> None:
        for note in notifications:
            if isinstance(note.payload, dict):
                await self._send(out, note.payload)

    async def _emit_leading_notifications(self, out: ByteSendStream) -> None:
        if self._emitted_leading:
            return
        self._emitted_leading = True
        await self._emit_notifications(self._matcher.leading_notifications, out)

    async def _send(self, out: ByteSendStream, obj: dict[str, Any]) -> None:
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

    def _apply_protocol_version(
        self, request_obj: dict[str, Any], response_obj: dict[str, Any]
    ) -> None:
        result = response_obj.get("result")
        if not isinstance(result, dict):
            return
        recorded_pv = result.get("protocolVersion")
        params = request_obj.get("params")
        requested_pv = (
            params.get("protocolVersion") if isinstance(params, dict) else None
        )
        mismatch = (
            requested_pv is not None
            and recorded_pv is not None
            and requested_pv != recorded_pv
        )
        if self.config.rewrite_protocol_version:
            if requested_pv is not None:
                result["protocolVersion"] = requested_pv
        elif mismatch:
            warnings.warn(
                f"mcp-cassette: client requested protocolVersion {requested_pv} but "
                f"cassette recorded {recorded_pv}; replaying recorded value",
                stacklevel=2,
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
