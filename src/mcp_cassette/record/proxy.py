"""Transparent stdio recording proxy.

Sits between an MCP client and a real MCP server on stdio, forwarding newline-delimited
JSON-RPC verbatim in both directions while a :class:`SessionRecorder` taps the traffic.
On any shutdown path the captured session is finalized into a valid cassette.
"""

from __future__ import annotations

import signal

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream

from .._stdio import stderr_stream, stdin_stream, stdout_stream
from ..cassette import RedactionRule, default_redaction_rules
from ..report import write_report as _write_report
from .pump import pump_lines
from .recorder import SessionRecorder


class StdioRecordingProxy:
    """Record a live MCP stdio session into a cassette.

    The proxy spawns ``server_cmd`` and runs three concurrent pumps in one anyio task
    group: client stdin to server stdin, server stdout to client stdout, and server
    stderr to our stderr (stderr is never swallowed — dropping it hides server logs and
    can deadlock a server whose stderr pipe buffer fills).
    """

    def __init__(
        self,
        server_cmd: list[str],
        cassette_path: str,
        redaction: list[RedactionRule] | None = None,
        include_default_redactions: bool = True,
        report_path: str | None = None,
    ) -> None:
        """Initialize the proxy.

        Args:
            server_cmd: The real server command and its arguments.
            cassette_path: Where the recorded cassette is written on shutdown.
            redaction: Additional redaction rules beyond the defaults.
            include_default_redactions: Whether to prepend the default rule set.
            report_path: Optional path to write a JSON session report (message count),
                used by the pytest fixture to detect empty recordings across processes.
        """
        self.server_cmd = server_cmd
        self.cassette_path = cassette_path
        self.report_path = report_path
        rules: list[RedactionRule] = []
        if include_default_redactions:
            rules.extend(default_redaction_rules())
        if redaction:
            rules.extend(redaction)
        self._recorder = SessionRecorder(rules)

    def run(self) -> int:
        """Run the proxy to completion, returning a process exit code."""
        return anyio.run(self._arun)

    async def _arun(self) -> int:
        exit_code = 0
        interrupted = False
        try:
            async with await anyio.open_process(self.server_cmd) as process:
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                client_in = stdin_stream()
                client_out = stdout_stream()
                our_err = stderr_stream()
                try:
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(self._watch_signals, tg.cancel_scope)
                        tg.start_soon(
                            self._client_to_server, client_in, process.stdin
                        )
                        tg.start_soon(
                            self._server_to_client,
                            process.stdout,
                            client_out,
                            tg.cancel_scope,
                        )
                        tg.start_soon(self._forward_stderr, process.stderr, our_err)
                except anyio.get_cancelled_exc_class():
                    interrupted = True
                await process.wait()
                exit_code = process.returncode or 0
        finally:
            self._finalize()
        return 130 if interrupted else exit_code

    async def _client_to_server(
        self, source: ByteReceiveStream, dest: ByteSendStream
    ) -> None:
        await pump_lines(
            source, dest, tap=lambda line: self._recorder.on_line("client", line)
        )
        await dest.aclose()  # forward EOF so the server can shut down

    async def _server_to_client(
        self,
        source: ByteReceiveStream,
        dest: ByteSendStream,
        cancel_scope: anyio.CancelScope,
    ) -> None:
        await pump_lines(
            source, dest, tap=lambda line: self._recorder.on_line("server", line)
        )
        cancel_scope.cancel()  # server closed stdout -> session over

    async def _forward_stderr(
        self, source: ByteReceiveStream, dest: ByteSendStream
    ) -> None:
        await pump_lines(source, dest, tap=None)

    async def _watch_signals(self, cancel_scope: anyio.CancelScope) -> None:
        try:
            with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
                async for _ in signals:
                    cancel_scope.cancel()
                    return
        except (NotImplementedError, ValueError):
            # Signal handling unsupported on this platform (e.g. Windows / non-main
            # thread); rely on normal EOF-driven shutdown instead.
            await anyio.sleep_forever()

    def _finalize(self) -> None:
        cassette = self._recorder.build()
        cassette.save(self.cassette_path)
        if self.report_path is not None:
            _write_report(self.report_path, {"messages": self._recorder.message_count})
