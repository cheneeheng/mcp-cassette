"""Transport guards run in every mode (ITER_04_v4 decision 7, ITER_06_v3 F4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette, Transport
from mcp_cassette.session import CassetteError, CassetteSession
from mcp_cassette.session.claim import claim_path

MODES = ["once", "none", "all", "new_episodes"]


def _session(tmp_path: Path, mode: str, transport: Transport | None) -> CassetteSession:
    cassette = tmp_path / "c.mcp.json"
    if transport is not None:
        Cassette(
            recorded_at=datetime(2026, 9, 1, tzinfo=UTC), transport=transport
        ).save(cassette)
    return CassetteSession(
        mode=mode,  # type: ignore[arg-type]  # parametrized literal
        cassette_path=cassette,
        report_path=tmp_path / "report.json",
    )


@pytest.mark.parametrize("mode", MODES)
def test_server_command_refuses_an_http_cassette(tmp_path: Path, mode: str) -> None:
    session = _session(tmp_path, mode, "http")
    with pytest.raises(CassetteError, match="Streamable HTTP"):
        session.server_command(["python", "server.py"])
    # Refused before any claim is taken, so nothing is left to release.
    assert not claim_path(session.cassette_path).exists()


@pytest.mark.parametrize("mode", MODES)
def test_server_url_refuses_a_stdio_cassette(tmp_path: Path, mode: str) -> None:
    session = _session(tmp_path, mode, "stdio")
    with pytest.raises(CassetteError, match="recorded over stdio"):
        session.server_url("http://127.0.0.1:9/mcp")
    assert not claim_path(session.cassette_path).exists()


def test_missing_cassette_does_not_trip_the_guard(tmp_path: Path) -> None:
    session = _session(tmp_path, "all", None)
    assert "record" in session.server_command(["python", "server.py"])
    session.close()
    assert not claim_path(session.cassette_path).exists()
