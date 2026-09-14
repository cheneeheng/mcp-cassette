"""Async library-door unit tests (ITER_05_v4 §04), over asyncio and trio."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from mcp_cassette import CassetteError, use_cassette_async
from mcp_cassette.report import write_report
from mcp_cassette.session import CassetteSession
from mcp_cassette.session.claim import claim_path

backends = pytest.mark.parametrize("backend", ["asyncio", "trio"])


@backends
def test_yields_session_with_resolved_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
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


@backends
def test_aclose_is_idempotent(tmp_path: Path, backend: str) -> None:
    session = CassetteSession(mode="all", cassette_path=tmp_path / "c.mcp.json")

    async def main() -> None:
        await session.aclose()
        await session.aclose()

    anyio.run(main, backend=backend)


@backends
def test_temp_report_dir_is_removed(tmp_path: Path, backend: str) -> None:
    async def main() -> Path:
        async with use_cassette_async(tmp_path / "c.mcp.json", mode="all") as session:
            report_dir = session.report_path.parent
            assert report_dir.is_dir()
            return report_dir

    assert not anyio.run(main, backend=backend).exists()
