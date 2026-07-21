"""Diffing unit tests (ITER_03_v3 §04): structural deltas between two cassettes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette import diff_cassettes
from mcp_cassette.cassette import Cassette, Message, ServerInfo
from mcp_cassette.cli import main

BENIGN = "Return the given text unchanged."


def _tool(
    name: str, description: str, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema or {"type": "object"},
    }


def _exchange(
    seq: int, msg_id: int, method: str, result: dict[str, Any], t: int
) -> list[Message]:
    return [
        Message(
            seq=seq,
            t_offset_ms=t,
            sender="client",
            kind="request",
            method=method,
            msg_id=msg_id,
            payload={"jsonrpc": "2.0", "id": msg_id, "method": method},
        ),
        Message(
            seq=seq + 1,
            t_offset_ms=t + 1,
            sender="server",
            kind="response",
            msg_id=msg_id,
            payload={"jsonrpc": "2.0", "id": msg_id, "result": result},
        ),
    ]


def _cassette(
    path: Path,
    *,
    tools: list[dict[str, Any]] | None = None,
    methods: list[str] | None = None,
    version: str = "1.4.0",
    transport: str = "stdio",
    format_version: int = 2,
    id_base: int = 1,
    t_base: int = 0,
) -> Path:
    messages: list[Message] = []
    if tools is not None:
        messages += _exchange(0, id_base, "tools/list", {"tools": tools}, t_base)
    for i, method in enumerate(methods or []):
        messages += _exchange(
            len(messages), id_base + 10 + i, method, {"ok": True}, t_base + 10 + i
        )
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 20, tzinfo=UTC),
        messages=messages,
        server_info=ServerInfo(name="reference", version=version),
        protocol_version="2024-11-05",
    )
    cassette = cassette.model_copy(
        update={
            "transport": transport,
            "format_version": format_version,
            "server_url": (
                "http://127.0.0.1:9001/mcp?token=abc" if transport == "http" else None
            ),
        }
    )
    path.write_text(cassette.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_identical_cassettes(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("search", BENIGN)])
    new = _cassette(tmp_path / "new.json", tools=[_tool("search", BENIGN)])
    result = diff_cassettes(old, new)
    assert result.identical
    assert result.metadata == []
    assert result.methods == []
    assert result.tools == []
    assert result.sequence == []


def test_server_version_change(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", tools=[], version="1.4.0")
    new = _cassette(tmp_path / "new.json", tools=[], version="1.5.0")
    result = diff_cassettes(old, new)
    [change] = result.metadata
    assert change.field == "server_info.version"
    assert (change.old, change.new) == ("1.4.0", "1.5.0")
    assert not result.identical


def test_added_tool_has_no_diff_lines(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("search", BENIGN)])
    new = _cassette(
        tmp_path / "new.json", tools=[_tool("search", BENIGN), _tool("add", "Adds.")]
    )
    [change] = diff_cassettes(old, new).tools
    assert (change.tool, change.change) == ("add", "added")
    assert change.diff == []


def test_removed_tool_locates_in_the_old_cassette(tmp_path: Path) -> None:
    old = _cassette(
        tmp_path / "old.json", tools=[_tool("search", BENIGN), _tool("add", "Adds.")]
    )
    new = _cassette(tmp_path / "new.json", tools=[_tool("search", BENIGN)])
    [change] = diff_cassettes(old, new).tools
    assert (change.tool, change.change) == ("add", "removed")
    assert change.locator == "/messages/1/payload/result/tools/1/name"


def test_description_change_carries_unified_diff(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("search", "Search the web.")])
    new = _cassette(tmp_path / "new.json", tools=[_tool("search", "Search anything.")])
    [change] = diff_cassettes(old, new).tools
    assert change.change == "description"
    assert "-Search the web." in change.diff
    assert "+Search anything." in change.diff


def test_input_schema_change_is_its_own_entry(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("search", BENIGN)])
    new = _cassette(
        tmp_path / "new.json",
        tools=[_tool("search", BENIGN, {"type": "object", "properties": {"q": {}}})],
    )
    [change] = diff_cassettes(old, new).tools
    assert change.change == "input_schema"
    assert change.locator.endswith("/inputSchema")


def test_reordered_sequence_with_unchanged_counts(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", methods=["tools/call", "resources/list"])
    new = _cassette(tmp_path / "new.json", methods=["resources/list", "tools/call"])
    result = diff_cassettes(old, new)
    assert result.methods == []
    assert result.sequence
    assert not result.identical


def test_ids_offsets_and_seq_alone_are_not_differences(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("search", BENIGN)])
    new = _cassette(
        tmp_path / "new.json",
        tools=[_tool("search", BENIGN)],
        id_base=900,
        t_base=50_000,
    )
    assert diff_cassettes(old, new).identical


def test_cross_version_and_transport_comparison(tmp_path: Path) -> None:
    old = _cassette(
        tmp_path / "v1.json", tools=[_tool("search", BENIGN)], format_version=1
    )
    new = _cassette(
        tmp_path / "v2.json", tools=[_tool("search", BENIGN)], transport="http"
    )
    result = diff_cassettes(old, new)
    assert result.tools == []
    fields = {c.field for c in result.metadata}
    assert fields == {"transport", "server_host"}
    # Host only — a diff must never surface the recorded URL's query string.
    assert all("token" not in (c.new or "") for c in result.metadata)


def test_method_count_delta(tmp_path: Path) -> None:
    old = _cassette(tmp_path / "old.json", methods=["tools/call"])
    new = _cassette(tmp_path / "new.json", methods=["tools/call", "tools/call"])
    result = diff_cassettes(old, new)
    deltas = {d.method: (d.old_count, d.new_count) for d in result.methods}
    # Responses carry no method and are counted under their kind, as inspect does.
    assert deltas == {"tools/call": (1, 2), "<response>": (1, 2)}


def test_text_output_prints_every_populated_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _cassette(
        tmp_path / "old.json",
        tools=[_tool("search", "Search the web.")],
        methods=["tools/call"],
    )
    new = _cassette(
        tmp_path / "new.json",
        tools=[
            _tool("search", "Search anything.", {"type": "object", "properties": {}})
        ],
        methods=["resources/list", "tools/call"],
        version="1.5.0",
    )
    assert main(["diff", str(old), str(new)]) == 5
    out = capsys.readouterr().out
    assert "metadata:" in out and "server_info.version: 1.4.0 -> 1.5.0" in out
    assert "methods:" in out and "resources/list: 0 -> 1" in out
    assert "tools:" in out and "description changed (+1 -1 lines)" in out
    assert "inputSchema changed" in out
    assert "sequence:" in out


def test_tools_only_text_output_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("search", BENIGN)])
    new = _cassette(
        tmp_path / "new.json", tools=[_tool("search", BENIGN)], version="9.9.9"
    )
    assert main(["diff", str(old), str(new), "--tools-only"]) == 0
    assert "identical" in capsys.readouterr().out
    assert main(["diff", str(old), str(new), "--tools-only", "--format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["metadata"] == []
    assert document["identical"] is True


def test_added_and_removed_tools_render_their_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _cassette(tmp_path / "old.json", tools=[_tool("gone", BENIGN)])
    new = _cassette(tmp_path / "new.json", tools=[_tool("fresh", BENIGN)])
    assert main(["diff", str(old), str(new)]) == 5
    out = capsys.readouterr().out
    assert "fresh: added" in out
    assert "gone: removed" in out
