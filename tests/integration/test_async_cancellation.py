"""A cancelled async-door body still releases everything (ITER_05_v4 decision 3)."""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import pytest

from mcp_cassette import use_cassette_async
from mcp_cassette.session.claim import claim_path

# Never contacted: the body is cancelled before the agent sends anything.
UNREACHED_URL = "http://127.0.0.1:9/mcp"


def _is_closed(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return False
    except OSError:
        return True


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_cancelled_body_releases_claim_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "c.mcp.json"
    seen: dict[str, int] = {}

    async def main() -> None:
        with anyio.move_on_after(0.5) as scope:
            async with use_cassette_async(cassette, mode="all") as session:
                url = session.server_url(UNREACHED_URL)
                seen["port"] = int(urlsplit(url).port or 0)
                assert claim_path(cassette).exists()
                await anyio.sleep_forever()
        assert scope.cancelled_caught

    anyio.run(main, backend=backend)
    assert not claim_path(cassette).exists()
    assert _is_closed(seen["port"])
