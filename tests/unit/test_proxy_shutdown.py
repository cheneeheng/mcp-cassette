"""In-process tests for the recording proxy's signal watchers.

The real interrupt paths end in ``os._exit`` (which discards coverage data) or need
platform signal delivery, so they are exercised here directly with a stubbed child
process and a mocked ``os._exit``. These tests deliberately call private methods: the
alternative is leaving the whole shutdown path unverified.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest

from mcp_cassette.cassette import Cassette
from mcp_cassette.record.proxy import StdioRecordingProxy


class _FakeProcess:
    def __init__(self, fail_terminate: bool = False, returncode: int = 0) -> None:
        self.terminated = False
        self._fail = fail_terminate
        self._returncode = returncode

    def terminate(self) -> None:
        if self._fail:
            raise ProcessLookupError
        self.terminated = True

    async def wait(self) -> int:
        return self._returncode


class _EofStream:
    """A receive stream that is already at EOF (the server closed its stdout)."""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        raise anyio.EndOfStream


class _NullSink:
    async def send(self, item: bytes) -> None:
        pass


@pytest.fixture()
def restore_handlers() -> Iterator[None]:
    saved = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            saved[sig] = signal.getsignal(sig)
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def _proxy(tmp_path: Path) -> StdioRecordingProxy:
    return StdioRecordingProxy(
        server_cmd=["unused"],
        cassette_path=str(tmp_path / "c.json"),
        report_path=str(tmp_path / "r.json"),
    )


def test_windows_watcher_finalizes_on_sigint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_handlers: None,
) -> None:
    proxy = _proxy(tmp_path)
    proxy._recorder.on_line("client", b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')  # noqa: SLF001
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    process = _FakeProcess()

    async def run() -> None:
        async with anyio.create_task_group() as tg:

            async def trigger() -> None:
                await anyio.sleep(0.3)
                signal.raise_signal(signal.SIGINT)

            tg.start_soon(trigger)
            await proxy._watch_signals_windows(process)  # noqa: SLF001

    anyio.run(run)
    assert exits == [130]
    assert process.terminated is True
    # cassette and report were finalized before exiting
    assert Cassette.load(tmp_path / "c.json").messages[0].method == "ping"
    assert (tmp_path / "r.json").exists()


def test_windows_watcher_tolerates_already_dead_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_handlers: None,
) -> None:
    proxy = _proxy(tmp_path)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    # exercise the SIGBREAK-absent branch on every platform
    monkeypatch.delattr(signal, "SIGBREAK", raising=False)
    process = _FakeProcess(fail_terminate=True)

    async def run() -> None:
        async with anyio.create_task_group() as tg:

            async def trigger() -> None:
                await anyio.sleep(0.2)
                proxy._signal_received = True  # noqa: SLF001

            tg.start_soon(trigger)
            await proxy._watch_signals_windows(process)  # noqa: SLF001

    anyio.run(run)
    assert exits == [130]
    # nothing was captured, so finalize writes the report but no empty cassette
    assert not (tmp_path / "c.json").exists()
    assert (tmp_path / "r.json").exists()


def test_windows_watcher_degrades_to_eof_when_handlers_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _proxy(tmp_path)

    def deny(*args: Any, **kwargs: Any) -> None:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(signal, "signal", deny)

    async def run() -> None:
        with anyio.move_on_after(0.2):
            await proxy._watch_signals_windows(_FakeProcess())  # noqa: SLF001

    anyio.run(run)  # returns: watcher slept until cancelled, no exit attempted
    assert not (tmp_path / "c.json").exists()


def test_watch_signals_platform_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_handlers: None,
) -> None:
    # POSIX: the anyio signal receiver cancels the scope. Windows: asyncio has no
    # add_signal_handler, so the wrapper falls back to the polling watcher.
    proxy = _proxy(tmp_path)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    process = _FakeProcess()
    sig = signal.SIGINT if sys.platform == "win32" else signal.SIGTERM

    async def run() -> None:
        async with anyio.create_task_group() as tg:

            async def trigger() -> None:
                await anyio.sleep(0.3)
                signal.raise_signal(sig)

            tg.start_soon(trigger)
            tg.start_soon(proxy._watch_signals, process)  # noqa: SLF001

    anyio.run(run)
    # Both paths hard-exit 130: neither the anyio receiver nor the Windows poller can
    # cancel the un-cancellable stdin worker-thread read, so both terminate and exit.
    assert exits == [130]
    assert process.terminated is True


def test_server_death_with_client_stdin_open_finalizes_and_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The server closes stdout while the client still holds our stdin. Cancelling the
    # task group cannot help — the stdin read is parked in a non-daemon anyio worker
    # thread — so the proxy finalizes and hard-exits with the server's own code.
    proxy = _proxy(tmp_path)
    proxy._recorder.on_line("client", b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')  # noqa: SLF001
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)

    async def run() -> None:
        async with anyio.create_task_group() as tg:
            await proxy._server_to_client(  # noqa: SLF001
                _EofStream(),  # type: ignore[arg-type]
                _NullSink(),  # type: ignore[arg-type]
                tg.cancel_scope,
                _FakeProcess(returncode=5),  # type: ignore[arg-type]
            )

    anyio.run(run)
    assert exits == [5]
    assert Cassette.load(tmp_path / "c.json").messages[0].method == "ping"


def test_server_eof_after_client_eof_unwinds_without_exiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The normal path: the client closed stdin first, so nothing is blocked and the
    # session ends by cancelling the task group — no hard exit, no lost coverage.
    proxy = _proxy(tmp_path)
    proxy._client_eof = True  # noqa: SLF001
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)

    async def run() -> None:
        async with anyio.create_task_group() as tg:
            await proxy._server_to_client(  # noqa: SLF001
                _EofStream(),  # type: ignore[arg-type]
                _NullSink(),  # type: ignore[arg-type]
                tg.cancel_scope,
                _FakeProcess(),  # type: ignore[arg-type]
            )

    anyio.run(run)
    assert exits == []
