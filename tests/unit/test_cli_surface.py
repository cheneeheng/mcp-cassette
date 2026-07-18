"""CLI surface tests (in-process): error paths and inspect output."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import (
    Cassette,
    Fault,
    FaultOverlay,
    Message,
    ServerInfo,
)
from mcp_cassette.cli import main


def _full_cassette() -> Cassette:
    return Cassette(
        recorded_at=datetime(2026, 7, 5, tzinfo=UTC),
        protocol_version="2024-11-05",
        server_info=ServerInfo(name="ref", version="1.0"),
        messages=[
            Message(
                seq=0,
                t_offset_ms=0,
                sender="client",
                kind="request",
                method="tools/call",
                msg_id=1,
                payload={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "echo"},
                },
            ),
            Message(
                seq=1,
                t_offset_ms=5,
                sender="server",
                kind="response",
                msg_id=1,
                payload={"jsonrpc": "2.0", "id": 1, "result": {}},
            ),
        ],
    )


def test_record_without_server_cmd_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["record", "--cassette", str(tmp_path / "c.json")])
    assert rc == 2
    assert "pass a remote --url URL or a server command" in capsys.readouterr().err


def test_serve_missing_cassette_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["serve", str(tmp_path / "nope.json")])
    assert rc == 2
    assert "mcp-cassette serve:" in capsys.readouterr().err


def test_serve_new_episodes_without_server_cmd_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.json"
    _full_cassette().save(path)
    rc = main(["serve", str(path), "--new-episodes"])
    assert rc == 2
    assert "missing server command" in capsys.readouterr().err


def test_inspect_missing_cassette_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["inspect", str(tmp_path / "nope.json")])
    assert rc == 2
    assert "mcp-cassette inspect:" in capsys.readouterr().err


def test_inspect_summarizes_cassette(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.json"
    _full_cassette().save(path)
    rc = main(["inspect", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "protocol_version: 2024-11-05" in out
    assert "server: ref 1.0" in out
    assert "messages: 2" in out
    assert "tools/call: 1" in out
    assert "timing span: 5 ms" in out


def test_inspect_method_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.json"
    _full_cassette().save(path)
    rc = main(["inspect", str(path), "--method", "tools/call"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "messages: 1" in out  # the response (no method) is filtered out


def test_inspect_empty_cassette(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.json"
    Cassette(recorded_at=datetime(2026, 7, 5, tzinfo=UTC)).save(path)
    rc = main(["inspect", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "messages: 0" in out
    assert "protocol_version" not in out
    assert "timing span" not in out


def test_inspect_faults_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.json"
    cassette = _full_cassette()
    cassette.messages.append(
        Message(
            seq=2,
            t_offset_ms=10,
            sender="client",
            kind="request",
            method="tools/list",
            msg_id=2,
            payload={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
    )
    cassette.save(path)
    faults = tmp_path / "c.faults.json"
    overlay = FaultOverlay(
        faults=[Fault.error("tools/call"), Fault.timeout("tools/none")]
    )
    faults.write_text(overlay.model_dump_json(), encoding="utf-8")

    rc = main(["inspect", str(path), "--faults", str(faults)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "seq 0 tools/call -> error" in out
    assert "WARNING: timeout on tools/none matches nothing" in out


# --- v2 surfaces: http branches without the extra, transport mismatches --------------


def _save_http_cassette(path: Path, *, server_url: str | None = None) -> None:
    Cassette(
        recorded_at=datetime(2026, 7, 18, tzinfo=UTC),
        transport="http",
        server_url=server_url,
    ).save(path)


def test_record_url_without_http_extra_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # None in sys.modules makes the guarded import raise ImportError, exactly as
    # a core-only install (no [http] extra) would.
    monkeypatch.setitem(sys.modules, "mcp_cassette.transports.http", None)
    rc = main(
        [
            "record",
            "--cassette",
            str(tmp_path / "c.json"),
            "--url",
            "http://127.0.0.1:9/mcp",
        ]
    )
    assert rc == 2
    assert "mcp-cassette record:" in capsys.readouterr().err


def test_serve_http_cassette_without_http_extra_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "h.json"
    _save_http_cassette(path)
    monkeypatch.setitem(sys.modules, "mcp_cassette.transports.http", None)
    rc = main(["serve", str(path)])
    assert rc == 2
    assert "mcp-cassette serve:" in capsys.readouterr().err


def test_serve_stdio_cassette_with_url_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "c.json"
    _full_cassette().save(path)
    rc = main(["serve", str(path), "--url", "http://127.0.0.1:9/mcp"])
    assert rc == 2
    assert "--url applies to http cassettes" in capsys.readouterr().err


def test_serve_http_new_episodes_without_url_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.json"
    _save_http_cassette(path, server_url=None)
    rc = main(["serve", str(path), "--new-episodes"])
    assert rc == 2
    assert "records no server_url" in capsys.readouterr().err


def test_serve_http_new_episodes_with_recorded_url_starts_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_cassette.transports.http import HttpReplayServer

    path = tmp_path / "h.json"
    _save_http_cassette(path, server_url="http://127.0.0.1:9/mcp")
    seen: dict[str, str | None] = {}

    def fake_run(self: HttpReplayServer) -> int:
        seen["fallthrough"] = self._fallthrough_url
        seen["cassette_path"] = self._cassette_path
        return 0

    monkeypatch.setattr(HttpReplayServer, "run", fake_run)
    rc = main(["serve", str(path), "--new-episodes"])
    assert rc == 0
    assert seen["fallthrough"] == "http://127.0.0.1:9/mcp"  # from the recording
    assert seen["cassette_path"] == str(path)  # novel episodes append in place


def test_inspect_http_cassette_without_server_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.json"
    _save_http_cassette(path, server_url=None)
    rc = main(["inspect", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "transport: http" in out
    assert "exchanges: 0" in out
    assert "server host" not in out
