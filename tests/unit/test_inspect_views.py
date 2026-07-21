"""Inspect view unit tests (ITER_03_v3 §04): timeline, tools, grep, json."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette.cassette import Cassette, Message, ServerInfo
from mcp_cassette.cli import main


def _messages(*, http: bool = False) -> list[Message]:
    common: dict[str, Any] = {"exchange": 0, "channel": None} if http else {}
    return [
        Message(
            seq=0,
            t_offset_ms=0,
            sender="client",
            kind="request",
            method="tools/list",
            msg_id=1,
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            **common,
        ),
        Message(
            seq=1,
            t_offset_ms=37,
            sender="server",
            kind="response",
            msg_id=1,
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {
                            "name": "search",
                            "description": "Search the web.\nSecond line.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"q": {}, "n": {}},
                            },
                        }
                    ]
                },
            },
            **({"exchange": 0, "channel": "post"} if http else {}),
        ),
        Message(
            seq=2,
            t_offset_ms=38,
            sender="client",
            kind="notification",
            method="notifications/initialized",
            payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
            **common,
        ),
    ]


def _cassette(path: Path, *, http: bool = False) -> Path:
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 20, tzinfo=UTC),
        messages=_messages(http=http),
        server_info=ServerInfo(name="reference", version="1.0.0"),
    )
    if http:
        cassette = cassette.model_copy(
            update={"transport": "http", "server_url": "http://127.0.0.1:9001/mcp"}
        )
    cassette.save(path)
    return path


def test_timeline_row_shape_stdio(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _cassette(tmp_path / "c.mcp.json")
    assert main(["inspect", str(path), "--timeline"]) == 0
    lines = capsys.readouterr().out.strip().split("\n")
    assert lines[0].split() == [
        "seq",
        "t_offset_ms",
        "dir",
        "kind",
        "method",
        "id",
        "bytes",
    ]
    assert lines[1].split()[:6] == ["0", "0", "->", "request", "tools/list", "1"]
    assert lines[2].split()[:5] == ["1", "37", "<-", "response", "-"]


def test_timeline_adds_http_columns(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _cassette(tmp_path / "c.mcp.json", http=True)
    assert main(["inspect", str(path), "--timeline"]) == 0
    lines = capsys.readouterr().out.strip().split("\n")
    assert lines[0].split()[-2:] == ["exch", "chan"]
    assert lines[2].split()[-2:] == ["0", "post"]


def test_grep_filters_and_composes_with_method(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _cassette(tmp_path / "c.mcp.json")
    assert main(["inspect", str(path), "--timeline", "--grep", "Search the web"]) == 0
    body = capsys.readouterr().out.strip().split("\n")
    assert len(body) == 2  # header + the one matching response
    composed = ["--grep", "tools", "--method", "tools/list"]
    assert main(["inspect", str(path), "--timeline", *composed]) == 0
    body = capsys.readouterr().out.strip().split("\n")
    assert len(body) == 2


def test_invalid_grep_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _cassette(tmp_path / "c.mcp.json")
    assert main(["inspect", str(path), "--grep", "("]) == 2
    assert "invalid --grep pattern" in capsys.readouterr().err


def test_tools_view_dedupes_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _cassette(tmp_path / "c.mcp.json")
    assert main(["inspect", str(path), "--tools"]) == 0
    [line] = capsys.readouterr().out.strip().split("\n")
    assert line.startswith("search  (2 args)  Search the web.")
    assert "Second line" not in line


def test_json_output_is_byte_stable_and_parses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _cassette(tmp_path / "c.mcp.json")
    assert main(["inspect", str(path), "--format", "json", "--timeline"]) == 0
    first = capsys.readouterr().out
    assert main(["inspect", str(path), "--format", "json", "--timeline"]) == 0
    second = capsys.readouterr().out
    assert first == second
    document = json.loads(first)
    assert document["messages"] == 3
    assert document["timing_span_ms"] == 38
    assert document["tools"][0]["name"] == "search"
    assert document["timeline"][0]["method"] == "tools/list"


def test_raw_payload_and_schemaless_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    messages = _messages()
    payload = messages[1].payload
    assert isinstance(payload, dict)
    payload["result"]["tools"][0]["inputSchema"] = {"type": "object"}
    messages.append(
        Message(
            seq=3,
            t_offset_ms=40,
            sender="server",
            kind="raw",
            payload="not json at all",
        )
    )
    path = tmp_path / "raw.mcp.json"
    Cassette(recorded_at=datetime(2026, 7, 20, tzinfo=UTC), messages=messages).save(
        path
    )

    assert main(["inspect", str(path), "--timeline", "--grep", "not json"]) == 0
    lines = capsys.readouterr().out.strip().split("\n")
    assert len(lines) == 2
    assert lines[1].split()[:4] == ["3", "40", "<-", "raw"]

    assert main(["inspect", str(path), "--tools"]) == 0
    assert capsys.readouterr().out.startswith("search  (0 args)")


def test_json_output_for_http_and_empty_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _cassette(tmp_path / "c.mcp.json", http=True)
    assert main(["inspect", str(path), "--format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["server_host"] == "127.0.0.1:9001"
    assert document["exchanges"] == 1

    assert main(["inspect", str(path), "--format", "json", "--method", "nope"]) == 0
    empty = json.loads(capsys.readouterr().out)
    assert empty["messages"] == 0
    assert empty["timing_span_ms"] == 0
