"""Transparent stdio recording proxy.

Sits between an MCP client and a real MCP server on stdio, forwarding newline-delimited
JSON-RPC verbatim in both directions while a :class:`SessionRecorder` taps the traffic.
On any shutdown path the captured session is finalized into a valid cassette.
"""

from __future__ import annotations

import os
import signal

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream, Process

from .._stdio import stderr_stream, stdin_stream, stdout_stream
from ..cassette import Cassette, RedactionRule, default_redaction_rules
from ..report import write_report as _write_report
from . import checkpoint
from .checkpoint import DEFAULT_CHECKPOINT_INTERVAL
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
        checkpoint_interval: float | None = DEFAULT_CHECKPOINT_INTERVAL,
    ) -> None:
        """Initialize the proxy.

        Args:
            server_cmd: The real server command and its arguments.
            cassette_path: Where the recorded cassette is written on shutdown.
            redaction: Additional redaction rules beyond the defaults.
            include_default_redactions: Whether to prepend the default rule set.
            report_path: Optional path to write a JSON session report (message count),
                used by the pytest fixture to detect empty recordings across processes.
            checkpoint_interval: Seconds between crash-safety checkpoints to
                ``<cassette>.partial``; ``None`` or non-positive disables them.
        """
        self.server_cmd = server_cmd
        self.cassette_path = cassette_path
        self.report_path = report_path
        self.checkpoint_interval = checkpoint_interval
        rules: list[RedactionRule] = []
        if include_default_redactions:
            rules.extend(default_redaction_rules())
        if redaction:
            rules.extend(redaction)
        self._recorder = SessionRecorder(rules)
        self._signal_received = False

    def run(self) -> int:
        """Run the proxy to completion, returning a process exit code."""
        return anyio.run(self._arun)

    async def _arun(self) -> int:
        exit_code = 0
        try:
            async with await anyio.open_process(self.server_cmd) as process:
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                client_in = stdin_stream()
                client_out = stdout_stream()
                our_err = stderr_stream()
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._watch_signals, process)
                    tg.start_soon(self._client_to_server, client_in, process.stdin)
                    tg.start_soon(
                        self._server_to_client,
                        process.stdout,
                        client_out,
                        tg.cancel_scope,
                    )
                    tg.start_soon(self._forward_stderr, process.stderr, our_err)
                    tg.start_soon(
                        checkpoint.run,
                        self.checkpoint_interval,
                        self._snapshot,
                        self.cassette_path,
                    )
                await process.wait()
                exit_code = process.returncode or 0
        finally:
            # Reached only on the normal (server-EOF) path; an interrupt hard-exits
            # from the signal watcher before unwinding here (see _interrupt_shutdown).
            self._finalize()
        return exit_code

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

    async def _watch_signals(self, process: Process) -> None:
        try:
            with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
                async for _ in signals:
                    self._interrupt_shutdown(process)
                    return
        except (NotImplementedError, ValueError):
            # asyncio has no add_signal_handler on Windows; fall back to a plain
            # signal.signal handler that we poll from the loop.
            await self._watch_signals_windows(process)

    async def _watch_signals_windows(self, process: Process) -> None:
        # Install a stdlib handler for SIGINT and SIGBREAK (Ctrl+C / Ctrl+Break) that
        # flips a flag; overriding SIGBREAK also pre-empts the OS default abrupt
        # termination (STATUS_CONTROL_C_EXIT) so we get to shut down on our terms.
        self._signal_received = False

        def _handler(signum: int, frame: object) -> None:
            self._signal_received = True

        installed = False
        for name in ("SIGINT", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
                installed = True
            except (ValueError, OSError):
                # Not the main thread; can't install a handler here.
                pass
        if not installed:
            # No graceful-interrupt path available; rely on EOF-driven shutdown.
            await anyio.sleep_forever()
        while not self._signal_received:
            await anyio.sleep(0.1)
        self._interrupt_shutdown(process)

    def _interrupt_shutdown(self, process: Process) -> None:
        """Terminate the child, finalize the cassette, and hard-exit with code 130.

        The client's stdin read runs in an un-cancellable worker thread on every
        platform (anyio ``FileReadStream`` reads via a worker thread), so a targeted
        SIGINT/SIGTERM cannot interrupt it and a task-group unwind would hang waiting
        on it. Stop the child so its pipes close, persist whatever was captured, then
        exit without joining that thread — it dies with the process.

        Args:
            process: The spawned server process to terminate.
        """
        with anyio.CancelScope(shield=True):
            try:
                process.terminate()
            except (ProcessLookupError, OSError):
                pass
        self._finalize()
        os._exit(130)

    def _snapshot(self) -> Cassette | None:
        if self._recorder.message_count == 0:
            return None
        return self._recorder.build()

    def _finalize(self) -> None:
        cassette = self._recorder.build()
        cassette.save(self.cassette_path)
        checkpoint.discard(self.cassette_path)
        if self.report_path is not None:
            _write_report(self.report_path, {"messages": self._recorder.message_count})
