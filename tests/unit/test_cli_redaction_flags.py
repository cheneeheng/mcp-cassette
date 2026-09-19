"""CLI redaction flags (ITER_01_v4 §05): record, serve, and lint's manifest gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette, PackRef, RedactionManifest
from mcp_cassette.cli import main
from mcp_cassette.redaction import SALT_ENV

RECORDED = datetime(2026, 9, 1, tzinfo=UTC)


def _cassette(
    path: Path, *, profile: str | None = None, manifest: bool = True, packs=()
) -> Path:
    redaction = (
        RedactionManifest(profile=profile, packs=list(packs), applied_at=RECORDED)
        if manifest
        else None
    )
    Cassette(recorded_at=RECORDED, redaction=redaction).save(path)
    return path


def test_record_with_unreadable_pack_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.toml"
    code = main(
        [
            "record",
            "--cassette",
            str(tmp_path / "c.mcp.json"),
            "--pii-pack",
            str(absent),
            "--",
            "python",
            "-c",
            "pass",
        ]
    )
    assert code == 2
    assert "absent.toml" in capsys.readouterr().err


def test_record_salt_env_without_variable_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SALT_ENV, raising=False)
    code = main(
        [
            "record",
            "--cassette",
            str(tmp_path / "c.mcp.json"),
            "--redact-salt-env",
            "--",
            "python",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert SALT_ENV in err
    assert "--redact-salt-env" in err


def test_serve_pii_pack_on_cassette_without_manifest_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _cassette(tmp_path / "c.mcp.json", manifest=False)
    pack = tmp_path / "p.toml"
    pack.write_text('version = 1\nid = "p"\n', encoding="utf-8")
    assert main(["serve", str(path), "--pii-pack", str(pack)]) == 2
    assert "carries no redaction manifest" in capsys.readouterr().err


def test_serve_unresolvable_pack_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ghost = PackRef(id="ghost", path=None, sha256="0" * 64)
    path = _cassette(tmp_path / "c.mcp.json", packs=[ghost])
    assert main(["serve", str(path)]) == 2
    err = capsys.readouterr().err
    assert "'ghost'" in err
    assert "re-record" in err


def test_require_redaction_passes_on_matching_profile(tmp_path: Path) -> None:
    path = _cassette(tmp_path / "c.mcp.json", profile="team")
    assert main(["lint", str(path), "--require-redaction", "team", "--no-config"]) == 0


@pytest.mark.parametrize(
    ("profile", "manifest", "needle"),
    [
        (None, False, "carries no redaction manifest"),
        ("other", True, "expected 'team'"),
    ],
)
def test_require_redaction_failure_exits_4_with_the_fix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    profile: str | None,
    manifest: bool,
    needle: str,
) -> None:
    path = _cassette(tmp_path / "c.mcp.json", profile=profile, manifest=manifest)
    assert main(["lint", str(path), "--require-redaction", "team", "--no-config"]) == 4
    err = capsys.readouterr().err
    assert needle in err
    assert "--redact-profile team" in err


def test_require_redaction_from_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mcp_cassette.lint]\nrequire_redaction = "team"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    path = _cassette(tmp_path / "c.mcp.json", manifest=False)
    assert main(["lint", str(path)]) == 4


def test_lint_multiple_paths_emits_one_json_document_each(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _cassette(tmp_path / "a.mcp.json")
    second = _cassette(tmp_path / "b.mcp.json")
    assert (
        main(["lint", str(first), str(second), "--format", "json", "--no-config"]) == 0
    )
    documents = json.loads(capsys.readouterr().out)
    assert [Path(d["cassette"]).name for d in documents] == ["a.mcp.json", "b.mcp.json"]


def test_lint_multiple_paths_text_output_names_each(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _cassette(tmp_path / "a.mcp.json")
    second = _cassette(tmp_path / "b.mcp.json")
    assert main(["lint", str(first), str(second), "--no-config"]) == 0
    out = capsys.readouterr().out
    assert f"{first}:" in out
    assert f"{second}:" in out


def test_lint_baseline_with_multiple_paths_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _cassette(tmp_path / "a.mcp.json")
    second = _cassette(tmp_path / "b.mcp.json")
    code = main(["lint", str(first), str(second), "--baseline", str(first)])
    assert code == 2
    assert "--baseline compares one cassette" in capsys.readouterr().err
