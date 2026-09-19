"""CLI record/serve under the write claim (ITER_04_v4 §04, decision 10)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette
from mcp_cassette.cli import main
from mcp_cassette.session.claim import ClaimFile, claim_path


@pytest.fixture
def cassette(tmp_path: Path) -> Path:
    path = tmp_path / "c.mcp.json"
    Cassette(recorded_at=datetime(2026, 9, 1, tzinfo=UTC)).save(path)
    return path


def _record(cassette: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_cassette",
            "record",
            "--cassette",
            str(cassette),
            *extra,
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def test_live_claim_exits_6_without_the_overwrite_warning(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    holder = ClaimFile(cassette, "all", worker="gw1")
    holder.acquire()
    try:
        code = main(["record", "--cassette", str(cassette), "--", "python", "-c", ""])
    finally:
        holder.release()
    assert code == 6
    err = capsys.readouterr().err
    assert "held for writing by gw1" in err
    assert "already exists" not in err


def test_without_a_claim_the_overwrite_warning_still_prints(cassette: Path) -> None:
    result = _record(cassette)
    assert result.returncode == 0, result.stderr
    assert "already exists and will be replaced" in result.stderr
    assert not claim_path(cassette).exists()


def test_claim_held_by_the_parent_is_neither_retaken_nor_released(
    cassette: Path,
) -> None:
    holder = ClaimFile(cassette, "all")
    holder.acquire()
    try:
        result = _record(cassette, "--claim-held-by", str(os.getpid()))
        assert result.returncode == 0, result.stderr
        assert claim_path(cassette).exists()
    finally:
        holder.release()


def test_force_warns_about_the_break_before_the_overwrite(cassette: Path) -> None:
    holder = ClaimFile(cassette, "all")
    holder.acquire()
    try:
        result = _record(cassette, "--force")
    finally:
        holder.release()
    assert result.returncode == 0, result.stderr
    err = result.stderr
    assert err.index("--force breaks") < err.index("already exists")


def test_serve_new_episodes_under_a_live_claim_exits_6(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    holder = ClaimFile(cassette, "all")
    holder.acquire()
    try:
        code = main(
            ["serve", str(cassette), "--new-episodes", "--", "python", "-c", ""]
        )
    finally:
        holder.release()
    assert code == 6
    assert "held for writing" in capsys.readouterr().err
