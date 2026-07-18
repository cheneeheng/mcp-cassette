"""CLI surface tests (in-process): error paths and inspect output."""

from __future__ import annotations

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
