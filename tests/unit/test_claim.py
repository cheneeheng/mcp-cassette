"""Advisory single-writer claim (ITER_04_v4 §04, decisions 1 and 4)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette.session.claim import (
    ClaimConflict,
    ClaimFile,
    ClaimRecord,
    claim_path,
    read_claim,
)

OLD = datetime.now(UTC) - timedelta(hours=5)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _plant(cassette: Path, **fields: Any) -> None:
    record: dict[str, Any] = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "node_id": "tests/test_a.py::test_x",
        "worker": "gw1",
        "mode": "all",
        "created_at": datetime.now(UTC),
        **fields,
    }
    claim_path(cassette).write_text(
        ClaimRecord.model_validate(record).model_dump_json(), encoding="utf-8"
    )


def test_acquire_and_release_round_trip(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    with ClaimFile(cassette, "all") as claim:
        assert claim.held
        record = read_claim(cassette)
        assert record is not None
        assert record.pid == os.getpid()
        assert record.mode == "all"
    assert not claim_path(cassette).exists()


def test_second_writer_fails_fast_naming_the_holder(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    holder = ClaimFile(cassette, "all", node_id="tests/test_a.py::test_x", worker="gw0")
    holder.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(ClaimConflict) as excinfo:
            ClaimFile(cassette, "all", wait=0).acquire()
        assert time.monotonic() - started < 1.0
        message = str(excinfo.value)
        for part in (
            "held for writing by gw0",
            "tests/test_a.py::test_x",
            f"pid {os.getpid()}",
            socket.gethostname(),
            "--force",
        ):
            assert part in message
    finally:
        holder.release()


def test_dead_holder_is_reclaimed_with_a_warning(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    dead = _dead_pid()
    _plant(cassette, pid=dead)
    claim = ClaimFile(cassette, "all", wait=0)
    with pytest.warns(UserWarning, match=f"pid {dead}.*not alive"):
        claim.acquire()
    assert claim.held
    claim.release()


def test_claim_past_the_ttl_is_reclaimed(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    _plant(cassette, created_at=OLD)  # our own live pid: only age can break it
    claim = ClaimFile(cassette, "all", wait=0)
    with pytest.warns(UserWarning, match="TTL"):
        claim.acquire()
    claim.release()


def test_another_hosts_claim_is_aged_out_but_never_pid_checked(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    _plant(cassette, pid=_dead_pid(), host="another-host")
    with pytest.raises(ClaimConflict):
        ClaimFile(cassette, "all", wait=0).acquire()
    _plant(cassette, pid=_dead_pid(), host="another-host", created_at=OLD)
    claim = ClaimFile(cassette, "all", wait=0)
    with pytest.warns(UserWarning, match="TTL"):
        claim.acquire()
    claim.release()


def test_release_is_idempotent_and_safe_after_removal(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    claim = ClaimFile(cassette, "all")
    claim.acquire()
    claim_path(cassette).unlink()
    claim.release()
    claim.release()
    assert not claim.held


def test_force_breaks_a_live_claim_and_the_old_holder_leaves_it_alone(
    tmp_path: Path,
) -> None:
    cassette = tmp_path / "c.mcp.json"
    holder = ClaimFile(cassette, "all")
    holder.acquire()
    breaker = ClaimFile(cassette, "all", force=True)
    with pytest.warns(UserWarning, match="--force breaks"):
        breaker.acquire()
    holder.release()  # no longer its claim: must not delete the breaker's
    assert claim_path(cassette).exists()
    breaker.release()
    assert not claim_path(cassette).exists()


def test_waiter_acquires_once_the_holder_releases(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    holder = ClaimFile(cassette, "once")
    holder.acquire()
    timer = threading.Timer(0.3, holder.release)
    timer.start()
    waiter = ClaimFile(cassette, "once", wait=10)
    waiter.acquire()
    timer.join()
    assert waiter.held
    waiter.release()


def test_unreadable_claim_blocks_briefly_then_is_reclaimed(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    claim_path(cassette).write_text("{", encoding="utf-8")
    with pytest.raises(ClaimConflict, match="unknown host"):
        ClaimFile(cassette, "all", wait=0).acquire()
    stale = time.time() - 60
    os.utime(claim_path(cassette), (stale, stale))
    claim = ClaimFile(cassette, "all", wait=0)
    with pytest.warns(UserWarning, match="unreadable"):
        claim.acquire()
    claim.release()
