"""The sync door refuses a running event loop (ITER_05_v4 §04 decision 2)."""

from __future__ import annotations

from pathlib import Path

import anyio
import anyio.to_thread
import pytest

from mcp_cassette import use_cassette


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_sync_door_inside_a_running_loop_raises(tmp_path: Path, backend: str) -> None:
    async def main() -> None:
        with pytest.raises(RuntimeError, match="use_cassette_async"):
            with use_cassette(tmp_path / "c.mcp.json", mode="all"):
                pass

    anyio.run(main, backend=backend)


def test_sync_door_from_a_worker_thread_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)

    def work() -> str:
        with use_cassette(tmp_path / "c.mcp.json", mode="all") as session:
            return session.mode

    async def main() -> str:
        return await anyio.to_thread.run_sync(work)

    assert anyio.run(main) == "all"
