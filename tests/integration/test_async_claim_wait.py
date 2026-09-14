"""An async claim wait never blocks the caller's loop (ITER_05_v4 §04)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import anyio
import pytest

from mcp_cassette import use_cassette_async
from mcp_cassette.session.claim import claim_path

EXAMPLE = Path(__file__).parents[2] / "examples" / "cassettes" / "echo_and_add.mcp.json"


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_waiter_keeps_the_loop_running_and_resolves_to_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "shared.mcp.json"
    ticks: list[float] = []
    result: dict[str, object] = {}

    async def writer(holding: anyio.Event) -> None:
        async with use_cassette_async(cassette, mode="once"):
            holding.set()
            await anyio.sleep(1.0)
            # Stands in for the recording this session would have made.
            shutil.copy(EXAMPLE, cassette)

    async def waiter() -> None:
        started = time.monotonic()
        async with use_cassette_async(cassette, mode="once") as session:
            result["waited"] = time.monotonic() - started
            result["action"] = session._resolve_action()  # noqa: SLF001 — same package
            result["claimed"] = claim_path(cassette).exists()

    async def ticker(done: anyio.Event) -> None:
        while not done.is_set():
            ticks.append(time.monotonic())
            await anyio.sleep(0.05)

    async def main() -> None:
        holding, done = anyio.Event(), anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(ticker, done)
            tg.start_soon(writer, holding)
            await holding.wait()
            await waiter()
            done.set()

    anyio.run(main, backend=backend)
    assert result["waited"] >= 0.8  # type: ignore[operator]
    assert result["action"] == "replay"
    # Re-resolving to replay drops the claim: readers never hold one.
    assert result["claimed"] is False
    # A blocking sleep would have frozen the ticker for the whole second.
    assert len(ticks) >= 10
