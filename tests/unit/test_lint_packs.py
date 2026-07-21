"""Pattern pack unit tests (ITER_04_v3 §04): loading, validation, composition."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.cli import main
from mcp_cassette.lint import run

PHRASE = "Ships telemetry to acme-internal.example."
BENIGN = "Return the given text unchanged."

PACK = """
version = 1

[[patterns]]
id = "P001"
label = "vendor-hostname"
regex = 'acme-internal\\.example'
flags = ["i"]
severity = "error"
"""


def _pack(path: Path, body: str = PACK) -> str:
    path.write_text(body, encoding="utf-8")
    return str(path)


def _cassette(path: Path, *, description: str, result: str | None = None) -> Path:
    messages: list[Message] = [
        Message(
            seq=0,
            t_offset_ms=0,
            sender="client",
            kind="request",
            method="tools/list",
            msg_id=1,
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ),
        Message(
            seq=1,
            t_offset_ms=1,
            sender="server",
            kind="response",
            msg_id=1,
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": description,
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            },
        ),
    ]
    if result is not None:
        messages += [
            Message(
                seq=2,
                t_offset_ms=2,
                sender="client",
                kind="request",
                method="tools/call",
                msg_id=2,
                payload={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {}},
                },
            ),
            Message(
                seq=3,
                t_offset_ms=3,
                sender="server",
                kind="response",
                msg_id=2,
                payload={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": result}]},
                },
            ),
        ]
    Cassette(recorded_at=datetime(2026, 7, 20, tzinfo=UTC), messages=messages).save(
        path
    )
    return path


def _run(cassette: Path, *packs: str) -> Any:
    return run(cassette, packs=list(packs))


def test_pack_rule_fires_with_its_own_id(tmp_path: Path) -> None:
    cassette = _cassette(tmp_path / "c.mcp.json", description=PHRASE)
    [finding] = _run(cassette, _pack(tmp_path / "pack.toml")).findings
    assert (finding.rule, finding.severity, finding.tool) == ("P001", "error", "echo")
    assert "vendor-hostname" in finding.message


def test_custom_message_replaces_the_default(tmp_path: Path) -> None:
    body = PACK + '\nmessage = "vendor hostname leaked into a tool description"\n'
    cassette = _cassette(tmp_path / "c.mcp.json", description=PHRASE)
    [finding] = _run(cassette, _pack(tmp_path / "pack.toml", body)).findings
    assert finding.message == "vendor hostname leaked into a tool description"


def test_surfaces_restricts_where_a_pattern_applies(tmp_path: Path) -> None:
    body = PACK + '\nsurfaces = ["description"]\n'
    pack = _pack(tmp_path / "pack.toml", body)
    on_description = _cassette(tmp_path / "d.mcp.json", description=PHRASE)
    on_result = _cassette(tmp_path / "r.mcp.json", description=BENIGN, result=PHRASE)
    assert [f.rule for f in _run(on_description, pack).findings] == ["P001"]
    assert _run(on_result, pack).findings == []


def test_result_only_surface(tmp_path: Path) -> None:
    body = PACK.replace('severity = "error"', 'surfaces = ["result"]')
    pack = _pack(tmp_path / "pack.toml", body)
    on_description = _cassette(tmp_path / "d.mcp.json", description=PHRASE)
    on_result = _cassette(tmp_path / "r.mcp.json", description=BENIGN, result=PHRASE)
    assert _run(on_description, pack).findings == []
    assert [f.rule for f in _run(on_result, pack).findings] == ["P001"]


def test_warning_severity_exits_0_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = PACK.replace('severity = "error"', 'severity = "warning"')
    cassette = _cassette(tmp_path / "c.mcp.json", description=PHRASE)
    pack = _pack(tmp_path / "pack.toml", body)
    assert main(["lint", str(cassette), "--pattern-pack", pack, "--no-config"]) == 0
    assert "P001 warning" in capsys.readouterr().out


def test_two_packs_compose(tmp_path: Path) -> None:
    second = PACK.replace("P001", "P002").replace(
        "acme-internal\\.example", "telemetry"
    )
    cassette = _cassette(tmp_path / "c.mcp.json", description=PHRASE)
    report = _run(
        cassette, _pack(tmp_path / "a.toml"), _pack(tmp_path / "b.toml", second)
    )
    assert sorted(f.rule for f in report.findings) == ["P001", "P002"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("not toml at all ===", "pattern pack"),
        ("version = 2\n", "unsupported version 2"),
        ("version = 1\nextra = true\n", "unknown top-level key"),
        (
            'version = 1\n[[patterns]]\nid = "P1"\nlabel = "x"\nregex = "y"\n'
            'severty = "error"\n',
            "severty",
        ),
        ('version = 1\n[[patterns]]\nid = "R005"\nlabel = "x"\nregex = "y"\n', "R005"),
        ('version = 1\n[[patterns]]\nid = "1bad"\nlabel = "x"\nregex = "y"\n', "1bad"),
        (
            'version = 1\n[[patterns]]\nid = "P1"\nlabel = "x"\nregex = "("\n',
            "invalid regex",
        ),
        (
            'version = 1\n[[patterns]]\nid = "P1"\nlabel = "x"\nregex = "y"\n'
            'flags = ["z"]\n',
            "unknown regex flag",
        ),
    ],
)
def test_invalid_packs_exit_2_naming_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, expected: str
) -> None:
    cassette = _cassette(tmp_path / "c.mcp.json", description=BENIGN)
    pack = _pack(tmp_path / "pack.toml", body)
    assert main(["lint", str(cassette), "--pattern-pack", pack, "--no-config"]) == 2
    err = capsys.readouterr().err
    assert expected in err
    assert "pack.toml" in err


def test_duplicate_ids_across_packs_name_both_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cassette = _cassette(tmp_path / "c.mcp.json", description=BENIGN)
    first = _pack(tmp_path / "a.toml")
    second = _pack(tmp_path / "b.toml")
    exit_code = main(
        [
            "lint",
            str(cassette),
            "--pattern-pack",
            first,
            "--pattern-pack",
            second,
            "--no-config",
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "a.toml" in err and "b.toml" in err and "duplicate rule id" in err


def test_pack_cannot_match_redaction_markers(tmp_path: Path) -> None:
    body = PACK.replace("acme-internal\\.example", "REDACTED")
    cassette = _cassette(tmp_path / "c.mcp.json", description="REDACTED")
    assert _run(cassette, _pack(tmp_path / "pack.toml", body)).findings == []


def test_custom_message_applies_to_result_surface(tmp_path: Path) -> None:
    body = (
        PACK.replace('severity = "error"', 'surfaces = ["result"]')
        + '\nmessage = "a result named the internal host"\n'
    )
    cassette = _cassette(tmp_path / "c.mcp.json", description=BENIGN, result=PHRASE)
    [finding] = _run(cassette, _pack(tmp_path / "pack.toml", body)).findings
    assert finding.message == "a result named the internal host"
