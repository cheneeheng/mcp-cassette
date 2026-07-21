"""Project lint config unit tests (ITER_04_v3 §04): discovery and flag layering."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.cli import main
from mcp_cassette.lint import discover_config

PHRASE = "Ships telemetry to acme-internal.example."

PACK = """
version = 1

[[patterns]]
id = "P001"
label = "vendor-hostname"
regex = 'acme-internal\\.example'
"""

SECOND_PACK = """
version = 1

[[patterns]]
id = "P002"
label = "telemetry-word"
regex = 'telemetry'
"""


def _tool(name: str, description: str) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": {"type": "object"}}


def _cassette(path: Path, tools: list[dict[str, Any]]) -> Path:
    messages = [
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
            payload={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}},
        ),
    ]
    Cassette(recorded_at=datetime(2026, 7, 20, tzinfo=UTC), messages=messages).save(
        path
    )
    return path


def _project(tmp_path: Path, table: str, *, pack: str | None = PACK) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.mcp_cassette.lint]\n{table}", encoding="utf-8"
    )
    if pack is not None:
        (tmp_path / "packs").mkdir(exist_ok=True)
        (tmp_path / "packs" / "team.toml").write_text(pack, encoding="utf-8")
    sub = tmp_path / "tests" / "cassettes"
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def test_config_discovered_from_a_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = _project(tmp_path, 'fail_on = "warning"\n')
    monkeypatch.chdir(sub)
    assert discover_config().fail_on == "warning"


def test_pack_paths_resolve_relative_to_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = _project(tmp_path, 'pattern_packs = ["packs/team.toml"]\n')
    monkeypatch.chdir(sub)
    [pack] = discover_config().pattern_packs
    assert pack == tmp_path / "packs" / "team.toml"
    assert pack.is_file()


def test_config_pack_fires_and_no_config_disables_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = _project(tmp_path, 'pattern_packs = ["packs/team.toml"]\n')
    cassette = _cassette(sub / "c.mcp.json", [_tool("echo", PHRASE)])
    monkeypatch.chdir(sub)
    assert main(["lint", str(cassette)]) == 4
    assert main(["lint", str(cassette), "--no-config"]) == 0


def test_flag_pack_adds_while_select_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sub = _project(tmp_path, 'pattern_packs = ["packs/team.toml"]\nselect = ["R001"]\n')
    (tmp_path / "packs" / "extra.toml").write_text(SECOND_PACK, encoding="utf-8")
    cassette = _cassette(sub / "c.mcp.json", [_tool("echo", PHRASE)])
    monkeypatch.chdir(sub)
    # Config select=["R001"] alone silences both pack rules.
    assert main(["lint", str(cassette)]) == 0
    capsys.readouterr()
    # --select replaces it; --pattern-pack is additive to the config's pack.
    exit_code = main(
        [
            "lint",
            str(cassette),
            "--pattern-pack",
            str(tmp_path / "packs" / "extra.toml"),
            "--select",
            "P001",
            "--select",
            "P002",
        ]
    )
    assert exit_code == 4
    out = capsys.readouterr().out
    assert "P001" in out and "P002" in out


def test_select_beats_ignore_and_prints_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sub = _project(tmp_path, 'pattern_packs = ["packs/team.toml"]\n')
    cassette = _cassette(sub / "c.mcp.json", [_tool("echo", PHRASE)])
    monkeypatch.chdir(sub)
    assert main(["lint", str(cassette), "--select", "P001", "--ignore", "P001"]) == 4
    out = capsys.readouterr().out
    assert "note: rule P001 is both selected and ignored; selection wins" in out


def test_fail_on_warning_changes_the_exit_code_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sub = _project(tmp_path, 'fail_on = "warning"\n', pack=None)
    duplicated = [_tool("echo", "Echoes."), _tool("echo", "Echoes again.")]
    cassette = _cassette(sub / "c.mcp.json", duplicated)
    monkeypatch.chdir(sub)
    assert main(["lint", str(cassette), "--format", "json"]) == 4
    report = json.loads(capsys.readouterr().out)
    [finding] = report["findings"]
    assert (finding["rule"], finding["severity"]) == ("R003", "warning")
    assert main(["lint", str(cassette), "--no-config"]) == 0


def test_missing_pyproject_yields_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = discover_config()
    assert config.pattern_packs == []
    assert config.fail_on == "error"


def test_malformed_pyproject_is_skipped_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Somebody else's broken file is not ours to validate; their tooling complains.
    (tmp_path / "pyproject.toml").write_text("this is ][ not toml", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert discover_config().fail_on == "error"


def test_malformed_lint_table_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcp_cassette.lint]\nfail_on = "sometimes"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r"\[tool\.mcp_cassette\.lint\]"):
        discover_config()
