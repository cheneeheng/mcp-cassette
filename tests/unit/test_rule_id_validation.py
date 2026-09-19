"""Unknown rule ids in --select/--ignore exit 2 (ITER_02_v4 decision 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.cli import main

TOKEN = "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"


@pytest.fixture
def cassette(tmp_path: Path) -> Path:
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


@pytest.mark.parametrize(
    ("flags", "unknown"),
    [
        (["--select", "R0001"], "R0001"),
        (["--ignore", "r005"], "r005"),
        (["--select", "R001", "--select", "R0001"], "R0001"),
    ],
)
def test_unknown_id_exits_2_naming_it(
    cassette: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    unknown: str,
) -> None:
    assert main(["lint", str(cassette), "--no-config", *flags]) == 2
    err = capsys.readouterr().err
    assert repr(unknown) in err
    assert "R005" in err  # the valid set is listed
    assert "'R001'" not in err  # a valid id in a mixed list is not blamed


def test_pack_defined_id_is_accepted_by_both_flags(
    tmp_path: Path, cassette: Path
) -> None:
    pack = tmp_path / "pack.toml"
    pack.write_text(
        'version = 1\n[[patterns]]\nid = "P001"\nlabel = "x"\nregex = "zzz"\n',
        encoding="utf-8",
    )
    base = ["lint", str(cassette), "--no-config", "--pattern-pack", str(pack)]
    assert main([*base, "--select", "P001"]) == 0
    assert main([*base, "--ignore", "P001"]) == 0


def test_select_and_ignore_the_same_id_still_notes_and_selection_wins(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["lint", str(cassette), "--no-config", "--select", "R005", "--ignore", "R005"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "note: rule R005 is both selected and ignored; selection wins" in out
    assert "R005 warning" in out
