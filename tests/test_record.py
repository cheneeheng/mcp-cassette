"""Recording tests (ITER_01 §04): classification, timing, redaction, raw, atomicity."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette.cassette import Cassette


def _record_cmd(cassette: Path, *server_extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(cassette),
        "--",
        *reference_server_cmd(*server_extra),
    ]


def test_records_full_exchange_with_ordering(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [
        *initialize_sequence(),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call(3, "echo", {"text": "hi"}),
        tool_call(4, "add", {"a": 2, "b": 3}),
    ]
    result = run_session(_record_cmd(cassette), messages)
    assert result.returncode == 0

    loaded = Cassette.load(cassette)
    # seq is strictly increasing and contiguous from 0
    assert [m.seq for m in loaded.messages] == list(range(len(loaded.messages)))
    # both client requests and server responses were captured
    kinds = {(m.sender, m.kind) for m in loaded.messages}
    assert ("client", "request") in kinds
    assert ("server", "response") in kinds
    # initialize metadata extracted observationally
    assert loaded.protocol_version == "2024-11-05"
    assert loaded.server_info is not None
    assert loaded.server_info.name == "reference-server"


def test_redacts_planted_secret(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [
        *initialize_sequence(),
        tool_call(2, "echo", {"text": "hi", "api_key": "sk-planted-secret"}),
    ]
    run_session(_record_cmd(cassette), messages)

    loaded = Cassette.load(cassette)
    text = json.dumps([m.model_dump() for m in loaded.messages])
    assert "sk-planted-secret" not in text
    # the message carrying the secret is flagged
    def _redacted_api_key(m: object) -> bool:
        payload = getattr(m, "payload", None)
        if not getattr(m, "redacted", False) or not isinstance(payload, dict):
            return False
        args = payload.get("params", {}).get("arguments", {})
        return args.get("api_key") == "REDACTED"

    assert any(_redacted_api_key(m) for m in loaded.messages)


def test_noisy_stdout_recorded_as_raw(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]
    run_session(_record_cmd(cassette, "--noisy-stdout"), messages)

    loaded = Cassette.load(cassette)
    raw = [m for m in loaded.messages if m.kind == "raw"]
    assert raw, "expected the non-JSON stdout line to be captured as kind='raw'"
    assert any("not JSON-RPC" in str(m.payload) for m in raw)


def test_partial_session_still_valid(tmp_path: Path) -> None:
    # Client sends only the handshake then closes stdin: the graceful shutdown path
    # still finalizes a valid, loadable cassette.
    cassette = tmp_path / "demo.json"
    run_session(_record_cmd(cassette), initialize_sequence())
    loaded = Cassette.load(cassette)
    assert loaded.protocol_version == "2024-11-05"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="signal-driven finalize is POSIX-only (Windows out of MVP scope)",
)
def test_sigterm_finalizes_cassette(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    proc = subprocess.Popen(
        _record_cmd(cassette),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    for msg in initialize_sequence():
        proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
    proc.stdin.flush()
    time.sleep(1.0)  # let the handshake be captured
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    assert proc.returncode == 130
    # an interrupted recording is still a valid cassette
    loaded = Cassette.load(cassette)
    assert len(loaded.messages) >= 1
