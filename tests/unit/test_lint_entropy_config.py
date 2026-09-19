"""R005 configuration layering: defaults -> pyproject -> CLI (ITER_02_v4 §04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.cli import main
from mcp_cassette.lint import discover_config

TOKEN = "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"


@pytest.fixture
def cassette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "c.mcp.json"
    Cassette(
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        messages=[
            Message(
                seq=0,
                t_offset_ms=0,
                sender="server",
                kind="notification",
                method="notifications/message",
                payload={"params": {"data": f"key {TOKEN}"}},
            )
        ],
    ).save(path)
    return path


def _pyproject(tmp_path: Path, body: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.mcp_cassette.lint.entropy]\n{body}\n", encoding="utf-8"
    )


def _r005(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if "R005" in line]


def test_pyproject_table_is_discovered(tmp_path: Path, cassette: Path) -> None:
    _pyproject(tmp_path, "min_length = 50\nallowlist = ['x']")
    config = discover_config()
    assert config.entropy is not None
    assert config.entropy.min_length == 50
    assert config.entropy.allowlist == ["x"]


def test_cli_flag_overrides_the_project_config(
    tmp_path: Path, cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pyproject(tmp_path, "min_length = 50")
    assert main(["lint", str(cassette)]) == 0
    assert _r005(capsys) == []
    assert main(["lint", str(cassette), "--entropy-min-length", "20"]) == 0
    assert len(_r005(capsys)) == 1


def test_no_entropy_disables_the_rule(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["lint", str(cassette), "--no-entropy", "--no-config"]) == 0
    assert _r005(capsys) == []


def test_cli_allowlist_suppresses_a_literal(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["lint", str(cassette), "--entropy-allow", TOKEN, "--no-config"]) == 0
    assert _r005(capsys) == []


def test_invalid_threshold_exits_2(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["lint", str(cassette), "--entropy-min-bits", "0", "--no-config"]) == 2
    assert "min_bits" in capsys.readouterr().err


def test_fail_on_warning_gates_while_severity_stays_warning(
    tmp_path: Path, cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcp_cassette.lint]\nfail_on = "warning"\n', encoding="utf-8"
    )
    assert main(["lint", str(cassette), "--format", "json"]) == 4
    (finding,) = json.loads(capsys.readouterr().out)["findings"]
    assert finding["rule"] == "R005"
    assert finding["severity"] == "warning"
