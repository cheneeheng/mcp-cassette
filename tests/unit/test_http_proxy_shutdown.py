"""HTTP proxy shutdown unit tests (ITER_01_v2 §04): every exit path finalizes.

The real operator-interrupt path is the same graceful task-group cancel exercised
here (no un-cancellable stdin thread exists on the HTTP side), so mid-session
interrupt coverage is in-process and OS-independent.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import anyio
import pytest

from mcp_cassette.cassette import Cassette
from mcp_cassette.session import CassetteError, CassetteSession
from mcp_cassette.transports.http.proxy import RecordingProxy


def test_cancel_mid_session_writes_valid_cassette(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            url = await tg.start(proxy.serve)
            assert str(url).startswith("http://127.0.0.1:")
            # Simulate captured mid-session traffic, then interrupt.
            proxy._recorder.on_message(
                "client",
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
                exchange=0,
            )
            tg.cancel_scope.cancel()

    anyio.run(partial(main))
    cassette = Cassette.load(cassette_path)
    assert cassette.transport == "http"
    assert cassette.server_url == "http://127.0.0.1:9/mcp"
    assert len(cassette.messages) == 1
    assert cassette.messages[0].exchange == 0


def test_finalize_is_idempotent(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )
    proxy.finalize()
    written = cassette_path.read_text(encoding="utf-8")
    proxy.finalize()
    assert cassette_path.read_text(encoding="utf-8") == written


def test_fatal_first_contact_skips_cassette(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )
    proxy._fatal = "http://127.0.0.1:9/mcp: cannot reach upstream"
    proxy.finalize()
    assert not cassette_path.exists()
    assert proxy.fatal_error is not None


# --- session transport dispatch -----------------------------------------------------


def _http_cassette(path: Path) -> None:
    from datetime import UTC, datetime

    Cassette(recorded_at=datetime(2026, 7, 17, tzinfo=UTC), transport="http").save(path)


def test_server_command_rejects_http_cassette(tmp_path: Path) -> None:
    cassette_path = tmp_path / "h.json"
    _http_cassette(cassette_path)
    session = CassetteSession(mode="once", cassette_path=cassette_path)
    with pytest.raises(CassetteError, match="server_url"):
        session.server_command(["real-server"])


def test_server_url_rejects_stdio_cassette(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    cassette_path = tmp_path / "s.json"
    Cassette(recorded_at=datetime(2026, 7, 17, tzinfo=UTC)).save(cassette_path)
    session = CassetteSession(mode="once", cassette_path=cassette_path)
    with pytest.raises(CassetteError, match="server_command"):
        session.server_url("https://mcp.example.com/mcp")


def test_server_url_none_mode_requires_cassette(tmp_path: Path) -> None:
    session = CassetteSession(mode="none", cassette_path=tmp_path / "missing.json")
    with pytest.raises(CassetteError, match="recording is forbidden"):
        session.server_url("https://mcp.example.com/mcp")


def test_server_url_faults_under_record_mode_fail(tmp_path: Path) -> None:
    from mcp_cassette.cassette import Fault

    session = CassetteSession(
        mode="all", cassette_path=tmp_path / "c.json"
    ).with_faults(Fault.timeout("tools/call"))
    with pytest.raises(CassetteError, match="faults apply to replay only"):
        session.server_url("https://mcp.example.com/mcp")
