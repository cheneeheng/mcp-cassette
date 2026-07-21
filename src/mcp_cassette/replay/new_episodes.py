"""The ``new_episodes`` record mode: replay known interactions, live-record novel ones.

Where a matched request has a recorded answer, it is served from the cassette. A request
that misses falls through to the real server (spawned once), and the novel exchange is
captured and appended. ``initialize`` and client notifications are always forwarded so
the live server has a valid session for the requests that do fall through.

Note: this composes replay with live recording. Interleaving is best-effort for the
serial request/response sessions agent test suites produce; free-running concurrent
server notifications during a fallen-through call are captured but their ordering
relative to intercepted responses is not guaranteed.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from anyio.abc import ByteSendStream

from .._stdio import stderr_stream, stdin_stream, stdout_stream
from ..cassette import (
    Cassette,
    MatchConfig,
    Message,
    PaceConfig,
    RedactionRule,
    default_redaction_rules,
)
from ..matching import Matcher
from ..record.pump import buffered_lines, pump_lines
from ..record.recorder import SessionRecorder
from ..report import write_report
from .pacing import Pacer


class NewEpisodesProxy:
    """Replay matched requests; append novel ones from the real server."""

    def __init__(
        self,
        cassette: Cassette,
        cassette_path: str,
        server_cmd: list[str],
        match: MatchConfig | None = None,
        redaction: list[RedactionRule] | None = None,
        include_default_redactions: bool = True,
        report_path: str | None = None,
        pace: PaceConfig | None = None,
    ) -> None:
        """Initialize the proxy.

        Args:
            cassette: The existing cassette to replay from.
            cassette_path: Where the merged cassette is written on shutdown.
            server_cmd: The real server command for fall-through misses.
            match: Matching configuration.
            redaction: Additional redaction rules for newly recorded messages.
            include_default_redactions: Whether to prepend the default rule set.
            report_path: Optional path for a JSON session report.
            pace: Optional pacing configuration. Applies to replayed hits only —
                fall-through misses go to the real server and are inherently
                live-timed.
        """
        self.cassette = cassette
        self.cassette_path = cassette_path
        self.server_cmd = server_cmd
        self.config = match or MatchConfig()
        self.report_path = report_path
        self._matcher = Matcher(cassette, self.config)
        self._pacer = Pacer(pace)
        rules: list[RedactionRule] = []
        if include_default_redactions:
            rules.extend(default_redaction_rules())
        if redaction:
            rules.extend(redaction)
        self._recorder = SessionRecorder(rules)

    def run(self) -> int:
        """Run to completion, returning the real server's exit code (or 0)."""
        return anyio.run(self._arun)

    async def _arun(self) -> int:
        exit_code = 0
        self._out_lock = anyio.Lock()
        async with await anyio.open_process(self.server_cmd) as process:
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            client_in = stdin_stream()
            client_out = stdout_stream()
            our_err = stderr_stream()
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._client_loop, client_in, client_out, process.stdin)
                tg.start_soon(self._server_loop, process.stdout, client_out)
                tg.start_soon(self._forward_stderr, process.stderr, our_err)
            await process.wait()
            exit_code = process.returncode or 0
        self._finalize()
        return exit_code

    async def _emit(self, client_out: ByteSendStream, data: bytes) -> None:
        # Both the replay path and the live-forward path write to the client; serialize
        # so anyio never sees a concurrent send on the same stream.
        async with self._out_lock:
            await client_out.send(data)

    async def _client_loop(
        self,
        client_in: Any,
        client_out: ByteSendStream,
        server_in: ByteSendStream,
    ) -> None:
        async for line in buffered_lines(client_in):
            obj = _decode(line)
            if obj is not None and _is_replayable_request(obj):
                exchange = self._matcher.find(obj)
                if exchange is not None and exchange.response is not None:
                    await self._replay(obj, exchange, client_out)
                    continue
            # forward (initialize, notifications, or a miss) and record it live
            self._recorder.on_line("client", line)
            await server_in.send(line)
        await server_in.aclose()

    async def _server_loop(self, server_out: Any, client_out: ByteSendStream) -> None:
        async for line in buffered_lines(server_out):
            self._recorder.on_line("server", line)
            await self._emit(client_out, line)

    async def _forward_stderr(self, server_err: Any, our_err: ByteSendStream) -> None:
        await pump_lines(server_err, our_err, tap=None)

    async def _replay(
        self, request_obj: dict[str, Any], exchange: Any, client_out: ByteSendStream
    ) -> None:
        assert exchange.response is not None
        payload = exchange.response.payload
        resp: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
        resp["id"] = request_obj.get("id")
        prev: Message | None = exchange.request
        await self._pacer.wait(prev, exchange.response)
        prev = exchange.response
        await self._emit(client_out, (json.dumps(resp) + "\n").encode("utf-8"))
        for note in exchange.notifications:
            if isinstance(note.payload, dict):
                await self._pacer.wait(prev, note)
                await self._emit(
                    client_out, (json.dumps(note.payload) + "\n").encode("utf-8")
                )
            prev = note

    def _finalize(self) -> None:
        appended = self._recorder.build().messages
        merged: list[Message] = list(self.cassette.messages)
        next_seq = len(merged)
        for msg in appended:
            merged.append(msg.model_copy(update={"seq": next_seq}))
            next_seq += 1
        result = self.cassette.model_copy(update={"messages": merged})
        result.save(self.cassette_path)
        if self.report_path is not None:
            write_report(self.report_path, {"messages": len(merged)})


def _decode(line: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(line.decode("utf-8", errors="replace").strip())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _is_replayable_request(obj: dict[str, Any]) -> bool:
    return (
        obj.get("method") is not None
        and "id" in obj
        and obj.get("method") != "initialize"
    )
