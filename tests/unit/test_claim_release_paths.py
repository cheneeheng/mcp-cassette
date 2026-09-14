"""Every finalize path releases the claim, the hard-exit ones included (ITER_04_v4 d9).

``os._exit`` skips ``finally`` blocks and discards subprocess coverage, so the three
paths that reach it are driven in-process with ``os._exit`` mocked, as
``test_proxy_shutdown`` does.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from test_proxy_shutdown import _FakeProcess

from mcp_cassette.cassette import Cassette
from mcp_cassette.record.proxy import StdioRecordingProxy, exit_on_server_death
from mcp_cassette.replay.new_episodes import NewEpisodesProxy
from mcp_cassette.session.claim import ClaimFile, claim_path

REQUEST = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'


def _claim(cassette: Path) -> ClaimFile:
    claim = ClaimFile(cassette, "all")
    claim.acquire()
    return claim


def _stdio_proxy(tmp_path: Path) -> StdioRecordingProxy:
    cassette = tmp_path / "c.mcp.json"
    proxy = StdioRecordingProxy(
        server_cmd=["unused"],
        cassette_path=str(cassette),
        claim=_claim(cassette),
    )
    proxy._recorder.on_line("client", REQUEST)  # noqa: SLF001
    return proxy


def test_interrupt_path_releases_before_the_hard_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy = _stdio_proxy(tmp_path)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)

    async def interrupt() -> None:
        proxy._interrupt_shutdown(_FakeProcess())  # type: ignore[arg-type]  # noqa: SLF001

    anyio.run(interrupt)
    assert exits == [130]
    assert Cassette.load(tmp_path / "c.mcp.json").messages
    assert not claim_path(tmp_path / "c.mcp.json").exists()


def test_server_death_path_releases_and_keeps_the_server_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy = _stdio_proxy(tmp_path)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    anyio.run(
        exit_on_server_death,
        _FakeProcess(returncode=7),  # type: ignore[arg-type]
        proxy._finalize,  # noqa: SLF001
    )
    assert exits == [7]
    assert not claim_path(tmp_path / "c.mcp.json").exists()


def test_new_episodes_finalize_releases(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    proxy = NewEpisodesProxy(
        cassette=Cassette(recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
        cassette_path=str(cassette),
        server_cmd=["unused"],
        claim=_claim(cassette),
    )
    proxy._finalize()  # noqa: SLF001
    assert not claim_path(cassette).exists()


def test_http_proxy_finalize_releases_even_after_a_first_contact_failure(
    tmp_path: Path,
) -> None:
    from mcp_cassette.transports.http import RecordingProxy

    cassette = tmp_path / "c.mcp.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp",
        cassette_path=str(cassette),
        claim=_claim(cassette),
    )
    proxy._fatal = "cannot reach upstream"  # noqa: SLF001
    proxy.finalize()
    assert not cassette.exists()
    assert not claim_path(cassette).exists()
