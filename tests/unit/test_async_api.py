"""Async library-door unit tests (ITER_05_v4 §04), over asyncio and trio."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import anyio
import pytest

from mcp_cassette import CassetteError, Fault, use_cassette_async
from mcp_cassette.report import write_report
from mcp_cassette.session import CassetteSession
from mcp_cassette.session.claim import ClaimFile, claim_path

HTTP_CASSETTE = (
    Path(__file__).parents[2] / "examples" / "cassettes" / "http_echo_and_add.mcp.json"
)
DEAD_URL = "http://127.0.0.1:9/mcp"

backends = pytest.mark.parametrize("backend", ["asyncio", "trio"])


@pytest.fixture(autouse=True)
def _no_inherited_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let each test's ``mode=`` argument decide the mode.

    ``MCP_CASSETTE_MODE`` outranks ``mode=`` by design, and CI sets it to ``none``
    (a project invariant), so without this every ``mode="all"`` here silently became
    ``none`` and any test that resolved an action failed on a missing cassette.
    """
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)


@backends
def test_yields_session_with_resolved_mode(tmp_path: Path, backend: str) -> None:
    cassette = tmp_path / "c.mcp.json"

    async def main() -> str:
        async with use_cassette_async(cassette, mode="all") as session:
            assert isinstance(session, CassetteSession)
            # A recording session holds the write claim for the whole block.
            assert claim_path(cassette).exists()
            return session.mode

    assert anyio.run(main, backend=backend) == "all"
    assert not claim_path(cassette).exists()


@backends
def test_clean_exit_afinalizes(tmp_path: Path, backend: str) -> None:
    calls: list[str] = []
    original = CassetteSession.afinalize

    async def spy(self: CassetteSession) -> None:
        calls.append("afinalize")
        await original(self)

    async def main() -> None:
        async with use_cassette_async(tmp_path / "c.mcp.json", mode="all"):
            pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(CassetteSession, "afinalize", spy)
        anyio.run(main, backend=backend)
    assert calls == ["afinalize"]


@backends
def test_clean_exit_raises_on_a_reported_miss(tmp_path: Path, backend: str) -> None:
    async def main() -> None:
        async with use_cassette_async(tmp_path / "c.mcp.json", mode="all") as session:
            session._last_action = "replay"  # noqa: SLF001 — same package
            write_report(str(session.report_path), {"misses": ["tools/call echo"]})

    with pytest.raises(CassetteError, match="1 unmatched request"):
        anyio.run(main, backend=backend)


@backends
def test_raising_body_propagates_without_cassette_error(
    tmp_path: Path, backend: str
) -> None:
    async def main() -> None:
        async with use_cassette_async(tmp_path / "c.mcp.json", mode="all") as session:
            # The same miss that raises on a clean exit must not bury the real error.
            session._last_action = "replay"  # noqa: SLF001 — same package
            write_report(str(session.report_path), {"misses": ["tools/call echo"]})
            raise RuntimeError("boom")

    # Unwrapped: the caller's error is not delivered inside an ExceptionGroup.
    with pytest.raises(RuntimeError, match="boom"):
        anyio.run(main, backend=backend)


class _EndBlockError(Exception):
    """Ends a block early so the door skips report checks on a no-traffic session."""


@backends
def test_aclose_is_idempotent(tmp_path: Path, backend: str) -> None:
    cassette = tmp_path / "c.mcp.json"

    async def main() -> None:
        async with use_cassette_async(cassette, mode="all") as session:
            session.server_url("http://127.0.0.1:9/mcp")  # an in-loop recording proxy
            assert claim_path(cassette).exists()
            await session.aclose()
            await session.aclose()  # the door's own cleanup makes a third call
            assert session._in_loop is None  # noqa: SLF001 — same package
            assert not claim_path(cassette).exists()
            raise _EndBlockError

    with pytest.raises(_EndBlockError):
        anyio.run(main, backend=backend)


@backends
def test_temp_report_dir_is_removed(tmp_path: Path, backend: str) -> None:
    async def main() -> Path:
        async with use_cassette_async(tmp_path / "c.mcp.json", mode="all") as session:
            report_dir = session.report_path.parent
            assert report_dir.is_dir()
            return report_dir

    assert not anyio.run(main, backend=backend).exists()


@backends
def test_explicit_report_path_is_used_and_kept(tmp_path: Path, backend: str) -> None:
    report = tmp_path / "keep" / "report.json"

    async def main() -> Path:
        async with use_cassette_async(
            tmp_path / "c.mcp.json", mode="all", report_path=report
        ) as session:
            return session.report_path

    assert anyio.run(main, backend=backend) == report
    assert tmp_path.is_dir()  # a caller-owned location is never cleaned up


def _port_open(url: str) -> bool:
    port = int(url.rsplit(":", 1)[1].split("/", 1)[0])
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


@backends
def test_faulted_session_serves_in_loop_and_stops_with_the_block(backend: str) -> None:

    async def main() -> str:
        async with use_cassette_async(HTTP_CASSETTE, mode="none") as session:
            faulted = session.with_faults(Fault.error("tools/call", code=-1))
            url = faulted.server_url(DEAD_URL)
            await anyio.sleep(0.1)  # let the in-loop server task start listening
            assert await anyio.to_thread.run_sync(_port_open, url)
        assert faulted._in_loop is None  # noqa: SLF001 — same package
        return url

    url = anyio.run(main, backend=backend)
    assert not _port_open(url)


@backends
def test_bind_failure_closes_the_socket_and_propagates(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    made: list[socket.socket] = []

    class _Unbindable(socket.socket):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

        def bind(self, address: Any) -> None:
            raise OSError("address already in use")

    async def main() -> None:
        async with use_cassette_async(HTTP_CASSETTE, mode="none") as session:
            monkeypatch.setattr("mcp_cassette.session.socket.socket", _Unbindable)
            session.server_url(DEAD_URL)

    with pytest.raises(OSError, match="already in use"):
        anyio.run(main, backend=backend)
    (sock,) = made
    assert sock.fileno() == -1  # closed, not leaked


@backends
def test_live_writer_claim_raises_cassette_error_on_entry(
    tmp_path: Path, backend: str
) -> None:
    cassette = tmp_path / "c.mcp.json"
    entered: list[bool] = []

    async def main() -> None:
        async with use_cassette_async(cassette, mode="all"):
            entered.append(True)  # pragma: no cover — must not be reached

    holder = ClaimFile(cassette, "all")
    holder.acquire()
    try:
        with pytest.raises(CassetteError, match="held for writing"):
            anyio.run(main, backend=backend)
    finally:
        holder.release()
    assert entered == []
