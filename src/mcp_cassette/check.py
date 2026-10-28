"""The upgrade tripwire: re-launch a server and compare its tools with the cassette.

A third-party server launched through ``uvx`` or ``npx`` is whatever version resolves
that day. ``check`` starts it, performs the handshake, reads ``tools/list`` to the last
page, and compares the result with the committed cassette's tool surface. It needs no
agent, no model call and no test suite, so it is one CI step per server.

Every line in both directions goes through a :class:`SessionRecorder`, so the fresh
session is redacted exactly as the baseline was before the two are compared, and
``--record`` writes an ordinary cassette.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream, Process

from . import __version__
from .cassette import Cassette, default_redaction_rules
from .diffing import ToolChange, _diff_tools
from .lint.engine import extract_surfaces, latest_tools
from .record.pump import buffered_lines
from .record.recorder import SessionRecorder
from .redaction import append_redactor
from .redaction.redactor import build_redactor
from .session import resolve_mode

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
"""Sent when ``--record`` writes a first baseline; the server answers with its own."""

DEFAULT_TIMEOUT = 120.0
"""Seconds from launch to the last ``tools/list`` page: a first ``uvx`` or ``npx``
run downloads the package inside it."""

STDERR_TAIL_LINES = 20
_GRACE = 2.0
_HANDSHAKE_METHODS = frozenset({"initialize", "tools/list"})

Outcome = Literal["unchanged", "changed", "recorded", "did_not_start"]


class CheckUsageError(ValueError):
    """The check cannot run as asked (CLI exit ``2``)."""


@dataclass
class CheckResult:
    """What one ``check`` run found.

    Attributes:
        outcome: ``unchanged`` / ``changed`` (exit 0 / 5), ``recorded`` (a baseline
            was written, exit 0), or ``did_not_start`` (exit 7).
        changes: Tool surface changes against the baseline, as ``diff --tools-only``
            reports them.
        tool_count: Tools the server listed (0 when it did not start).
        version_change: ``(old, new)`` server version when it moved; information
            only, never a failure.
        failure: Why the server did not start, ending with its last stderr lines.
    """

    outcome: Outcome
    changes: list[ToolChange] = field(default_factory=list)
    tool_count: int = 0
    version_change: tuple[str, str] | None = None
    failure: str | None = None


def check_server(
    cassette_path: str | os.PathLike[str],
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    record: bool = False,
) -> CheckResult:
    """Re-launch ``command`` and compare its tools with the baseline cassette.

    Args:
        cassette_path: The committed baseline, or where ``record`` writes one.
        command: The server command and its arguments.
        env: Variables added to the inherited environment, e.g. a dummy key.
        timeout: Seconds from launch to the last ``tools/list`` page.
        record: Write the baseline instead of comparing. Allowed when no cassette
            exists or the existing one holds only the handshake, so a fixture
            session that also drives replay is never replaced by its tool list.

    Returns:
        The :class:`CheckResult`.

    Raises:
        CheckUsageError: When the check cannot run as asked.
        UnsupportedFormatVersion: If the baseline is from a newer mcp-cassette.
        OSError: If the baseline cannot be read.
        ValueError: If the baseline is malformed, or names a redaction pack that
            cannot be found.
    """
    path = Path(cassette_path)
    baseline = Cassette.load(path) if path.exists() else None
    _validate(path, baseline, record=record)

    if record:
        recorder = SessionRecorder(redactor=build_redactor())
    else:
        assert baseline is not None  # _validate: no baseline means --record
        # Redact the fresh session the way the baseline was, or a pack or flag used
        # at record time would read as drift in every description it touched.
        redactor = append_redactor(baseline.redaction)
        recorder = (
            SessionRecorder(redactor=redactor)
            if redactor is not None
            else SessionRecorder(default_redaction_rules())
        )
    protocol_version = (
        baseline.protocol_version if baseline else None
    ) or DEFAULT_PROTOCOL_VERSION
    full_env = {**os.environ, **env} if env else None
    failure = anyio.run(
        _session, list(command), full_env, timeout, recorder, protocol_version
    )
    if failure is not None:
        return CheckResult("did_not_start", failure=failure)

    fresh = recorder.build()
    tool_count = len(latest_tools(fresh))
    if record:
        fresh.save(path)
        return CheckResult("recorded", tool_count=tool_count)
    assert baseline is not None
    changes = _diff_tools(baseline, fresh)
    old_info, new_info = baseline.server_info, fresh.server_info
    version_change = (
        (old_info.version, new_info.version)
        if old_info and new_info and old_info.version != new_info.version
        else None
    )
    return CheckResult(
        "changed" if changes else "unchanged",
        changes=changes,
        tool_count=tool_count,
        version_change=version_change,
    )


def _validate(path: Path, baseline: Cassette | None, *, record: bool) -> None:
    if record:
        if resolve_mode() == "none":
            raise CheckUsageError(
                "--record writes a cassette, and MCP_CASSETTE_MODE=none forbids "
                "recording; write the baseline locally and commit it"
            )
        if baseline is not None and not _handshake_only(baseline):
            raise CheckUsageError(
                f"{path} holds a recorded session beyond the handshake, and --record "
                "would replace it with the tool list alone; re-record it through your "
                "tests with MCP_CASSETTE_MODE=all instead"
            )
        return
    if baseline is None:
        raise CheckUsageError(
            f"no baseline at {path}; run once with --record to write it, review it, "
            "and commit it"
        )
    if baseline.transport != "stdio":
        raise CheckUsageError(
            f"{path} was recorded over {baseline.transport}; check re-launches "
            "stdio servers only"
        )
    tool_lists, _ = extract_surfaces(baseline)
    if not tool_lists:
        raise CheckUsageError(
            f"{path} has no tools/list result to compare against; write one with "
            "--record"
        )


def _handshake_only(cassette: Cassette) -> bool:
    return all(
        m.method in _HANDSHAKE_METHODS
        for m in cassette.messages
        if m.sender == "client" and m.kind == "request"
    )


class _ClosedError(Exception):
    """The server closed stdout before answering."""


class _ErrorReplyError(Exception):
    """The server answered with a JSON-RPC error."""


class _Client:
    """A minimal JSON-RPC client over the server's stdio, tapping both directions."""

    def __init__(
        self,
        stdin: ByteSendStream,
        stdout: ByteReceiveStream,
        recorder: SessionRecorder,
    ) -> None:
        self._stdin = stdin
        self._lines: AsyncIterator[bytes] = buffered_lines(stdout)
        self._recorder = recorder
        self._next_id = 0

    async def request(
        self, method: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)
        while True:
            try:
                line = await anext(self._lines)
            except StopAsyncIteration:
                raise _ClosedError from None
            self._recorder.on_line("server", line)
            obj = _decode(line)
            if obj is None:
                continue  # stdout noise: recorded as raw, never fatal
            if "method" in obj and "id" in obj:
                await self._answer(obj)
            elif obj.get("id") == msg_id:
                if "error" in obj:
                    raise _ErrorReplyError(json.dumps(obj["error"]))
                result = obj.get("result")
                return result if isinstance(result, dict) else {}

    async def notify(self, method: str) -> None:
        await self._send({"jsonrpc": "2.0", "method": method})

    async def _answer(self, request: dict[str, Any]) -> None:
        # We declared no capabilities, so the only request we can serve is ping.
        # Answering the rest keeps a server that waits on its own request from
        # stalling until the timeout and reading as "did not start".
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        if request["method"] == "ping":
            reply["result"] = {}
        else:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        await self._send(reply)

    async def _send(self, message: dict[str, Any]) -> None:
        line = (json.dumps(message) + "\n").encode("utf-8")
        self._recorder.on_line("client", line)
        await self._stdin.send(line)


def _decode(line: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


async def _session(
    command: list[str],
    env: dict[str, str] | None,
    timeout: float,
    recorder: SessionRecorder,
    protocol_version: str,
) -> str | None:
    """Run the handshake and the paged tool list; return why it failed, or ``None``."""
    # npx on Windows is npx.cmd, which CreateProcess finds only by its full name.
    command = [shutil.which(command[0]) or command[0], *command[1:]]
    try:
        process = await anyio.open_process(command, env=env)
    except OSError as exc:
        return f"could not launch {command[0]!r}: {exc}"
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    client = _Client(process.stdin, process.stdout, recorder)
    step = "initialize"
    reason: str | None = None
    exited_alone = False
    async with anyio.create_task_group() as tg:
        tg.start_soon(_collect_stderr, process.stderr, tail)
        try:
            with anyio.fail_after(timeout):
                await client.request(
                    "initialize",
                    {
                        "protocolVersion": protocol_version,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "mcp-cassette-check",
                            "version": __version__,
                        },
                    },
                )
                await client.notify("notifications/initialized")
                step = "tools/list"
                cursor: object = None
                while True:
                    params = {"cursor": cursor} if isinstance(cursor, str) else None
                    cursor = (await client.request("tools/list", params)).get(
                        "nextCursor"
                    )
                    if not isinstance(cursor, str):
                        break
        except TimeoutError:
            reason = f"no answer to {step} within {timeout:g}s"
        except (_ClosedError, anyio.BrokenResourceError, anyio.ClosedResourceError):
            reason = f"it closed its pipes before answering {step}"
            exited_alone = True
        except _ErrorReplyError as exc:
            reason = f"it answered {step} with an error: {exc}"
        await _stop(process)
        tg.cancel_scope.cancel()
    if reason is None:
        return None
    if exited_alone:
        reason += f" (exit code {process.returncode})"
    lines = "".join(f"\n  {line}" for line in tail)
    return f"{reason}{'; its last stderr lines:' + lines if lines else ''}"


async def _collect_stderr(stream: ByteReceiveStream, tail: deque[str]) -> None:
    async for line in buffered_lines(stream):
        tail.append(line.decode("utf-8", errors="replace").rstrip())


async def _stop(process: Process) -> None:
    """Close stdin, then terminate, then kill: a polite server exits at stdin EOF."""
    with anyio.CancelScope(shield=True):
        await process.stdin.aclose()  # type: ignore[union-attr]
        with anyio.move_on_after(_GRACE):
            await process.wait()
            return
        if sys.platform == "win32":
            # npx.cmd runs node under cmd.exe; killing cmd.exe alone leaves node
            # holding the pipes, so take the whole tree.
            await anyio.run_process(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False
            )
        else:
            process.terminate()
            with anyio.move_on_after(_GRACE):
                await process.wait()
                return
            process.kill()  # pragma: no cover — needs a server that ignores SIGTERM
        await process.wait()
