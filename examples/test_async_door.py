"""The async library door, ``use_cassette_async``, under asyncio and trio.

Agent harnesses are mostly async. ``use_cassette`` reaches them only across a blocking
portal thread and refuses to run inside an event loop at all. ``use_cassette_async``
serves the replay HTTP server as a task in the caller's own loop instead, with no
thread to join at shutdown, and a cancelled body still stops the server.

Replays the committed ``http_echo_and_add`` cassette, offline. For a runnable script
that also records, see ``library_mode_async.py``. Needs the ``[http]`` extra.
"""

from __future__ import annotations

from pathlib import Path

import anyio
import anyio.to_thread
import pytest
from mcp_client import initialize, tool_call

import mcp_cassette as mcc

pytest.importorskip("h11", reason="the HTTP examples need mcp-cassette[http]")
pytest.importorskip("httpx", reason="the HTTP examples need mcp-cassette[http]")

from mcp_http_client import run  # noqa: E402 — needs httpx, checked just above

CASSETTE = Path(__file__).parent / "cassettes" / "http_echo_and_add.mcp.json"
DEAD_URL = "http://127.0.0.1:9/mcp"  # replay never contacts the real server


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_async_replay_in_the_callers_loop(backend: str) -> None:
    async def main() -> str:
        async with mcc.use_cassette_async(CASSETTE, mode="none") as session:
            url = session.server_url(DEAD_URL)
            messages = [*initialize(), tool_call(3, "add", {"a": 40, "b": 2})]
            # The scripted client is synchronous; a real async agent would await.
            objects = await anyio.to_thread.run_sync(run, url, messages)
        return next(o for o in objects if o.get("id") == 3)["result"]["content"][0][
            "text"
        ]

    assert anyio.run(main, backend=backend) == "42"


def test_sync_door_refuses_a_running_loop() -> None:
    """Inside an event loop the sync door would deadlock, so it names the async one."""

    async def main() -> None:
        with mcc.use_cassette(CASSETTE, mode="none"):
            pass  # pragma: no cover — never entered

    with pytest.raises(RuntimeError, match="use_cassette_async"):
        anyio.run(main)
