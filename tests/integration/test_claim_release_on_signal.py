"""Claim release on the real interrupt and server-death exits (ITER_04_v4 d5, d9)."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripted_client import initialize_sequence, reference_server_cmd

from mcp_cassette.cassette import Cassette
from mcp_cassette.session.claim import claim_path

DYING_SERVER = """\
import json, sys
request = json.loads(sys.stdin.readline())
reply = {"jsonrpc": "2.0", "id": request["id"], "result": {}}
sys.stdout.write(json.dumps(reply) + "\\n")
sys.stdout.flush()
sys.exit(7)
"""


def _record_cmd(cassette: Path, *server: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(cassette),
        "--",
        *server,
    ]


def _start(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,  # type: ignore[call-overload]
    )
    assert proc.stdin is not None
    for message in initialize_sequence():
        proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
    proc.stdin.flush()
    return proc


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_sigint_mid_recording_releases_the_claim(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.mcp.json"
    proc = _start(_record_cmd(cassette, *reference_server_cmd()))
    time.sleep(1.5)
    assert claim_path(cassette).exists()
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=15)
    assert proc.returncode == 130
    assert Cassette.load(cassette).messages
    assert not claim_path(cassette).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK_EVENT is Windows-only")
def test_ctrl_break_mid_recording_releases_the_claim(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.mcp.json"
    proc = _start(
        _record_cmd(cassette, *reference_server_cmd()),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
    )
    try:
        time.sleep(3.0)
        proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            pytest.skip(
                "CTRL_BREAK_EVENT not deliverable in this environment (no console, "
                "e.g. under `uv run`); run `python -m pytest` from a terminal"
            )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert proc.returncode == 130
    assert not claim_path(cassette).exists()


def test_server_death_releases_the_claim_and_keeps_its_exit_code(
    tmp_path: Path,
) -> None:
    server = tmp_path / "dying_server.py"
    server.write_text(DYING_SERVER, encoding="utf-8")
    cassette = tmp_path / "demo.mcp.json"
    proc = _start(_record_cmd(cassette, sys.executable, str(server)))
    try:
        proc.wait(timeout=30)  # stdin stays open: the agent is still attached
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert proc.returncode == 7
    assert Cassette.load(cassette).messages
    assert not claim_path(cassette).exists()
