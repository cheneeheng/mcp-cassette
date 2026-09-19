"""Widened tool-text extraction (ITER_03_v4 §04, decisions 3, 4, 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.lint import run
from mcp_cassette.lint.engine import MAX_SCHEMA_DEPTH, extract_tool_text


def tools_cassette(path: Path, *tool_lists: list[dict[str, Any]]) -> Path:
    """A cassette holding one tools/list request/response pair per list."""
    messages: list[Message] = []
    for n, tools in enumerate(tool_lists):
        messages.append(
            Message(
                seq=len(messages),
                t_offset_ms=0,
                sender="client",
                kind="request",
                method="tools/list",
                msg_id=n + 1,
                payload={"jsonrpc": "2.0", "id": n + 1, "method": "tools/list"},
            )
        )
        messages.append(
            Message(
                seq=len(messages),
                t_offset_ms=0,
                sender="server",
                kind="response",
                msg_id=n + 1,
                payload={"jsonrpc": "2.0", "id": n + 1, "result": {"tools": tools}},
            )
        )
    Cassette(recorded_at=datetime(2026, 9, 1, tzinfo=UTC), messages=messages).save(path)
    return path


SCHEMA = {
    "type": "object",
    "description": "Top level.",
    "properties": {
        "query": {
            "type": "string",
            "description": "The query.",
            "enum": ["fast", "slow", 3, True, None],
        },
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"field": {"description": "Field name."}},
            },
        },
    },
}


def test_nested_schema_yields_exact_locators(tmp_path: Path) -> None:
    path = tools_cassette(
        tmp_path / "c.mcp.json",
        [{"name": "search", "description": "d", "inputSchema": SCHEMA}],
    )
    surfaces, notes = extract_tool_text(Cassette.load(path))
    base = "/messages/1/payload/result/tools/0"
    schema = f"{base}/inputSchema"
    assert [(s.kind, s.locator, s.text) for s in surfaces] == [
        ("name", f"{base}/name", "search"),
        ("schema_description", f"{schema}/description", "Top level."),
        ("schema_description", f"{schema}/properties/query/description", "The query."),
        ("schema_enum", f"{schema}/properties/query/enum/0", "fast"),
        ("schema_enum", f"{schema}/properties/query/enum/1", "slow"),
        (
            "schema_description",
            f"{schema}/properties/filters/items/properties/field/description",
            "Field name.",
        ),
    ]
    assert notes == []


def test_deep_schema_stops_at_the_cap_with_a_note(tmp_path: Path) -> None:
    node: dict[str, Any] = {"description": "too deep to read"}
    for _ in range(15):
        node = {"properties": {"x": node}}
    path = tools_cassette(
        tmp_path / "c.mcp.json", [{"name": "deep", "inputSchema": node}]
    )
    surfaces, notes = extract_tool_text(Cassette.load(path))
    assert "too deep to read" not in [s.text for s in surfaces]
    (note,) = notes
    assert 'tool "deep"' in note
    assert f"depth {MAX_SCHEMA_DEPTH}" in note


def test_ref_cycle_is_never_followed(tmp_path: Path) -> None:
    schema = {"$ref": "#", "definitions": {"a": {"$ref": "#/definitions/a"}}}
    path = tools_cassette(
        tmp_path / "c.mcp.json", [{"name": "loop", "inputSchema": schema}]
    )
    surfaces, notes = extract_tool_text(Cassette.load(path))
    assert [s.kind for s in surfaces] == ["name"]
    assert notes == []


LOOKALIKE = "sеarch"  # Cyrillic е


def _r007_count(path: Path) -> int:
    return sum(1 for f in run(path).findings if f.rule == "R007")


def test_every_occurrence_is_a_surface_regardless_of_order(tmp_path: Path) -> None:
    drifted = [{"name": LOOKALIKE, "inputSchema": {}}]
    reverted = [{"name": "search", "inputSchema": {}}]
    forward = tools_cassette(tmp_path / "forward.mcp.json", drifted, reverted)
    backward = tools_cassette(tmp_path / "backward.mcp.json", reverted, drifted)

    surfaces, _ = extract_tool_text(Cassette.load(forward))
    assert [s.text for s in surfaces if s.kind == "name"] == [LOOKALIKE, "search"]
    (finding,) = [f for f in run(forward).findings if f.rule == "R007"]
    assert finding.locator == "/messages/1/payload/result/tools/0/name"
    assert _r007_count(forward) == _r007_count(backward) == 1
