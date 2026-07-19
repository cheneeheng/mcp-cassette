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


def _record_cmd(
    cassette: Path, *server_extra: str, extra: tuple[str, ...] = ()
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(cassette),
        *extra,
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


def test_record_redact_flags_plumbed_through(tmp_path: Path) -> None:
    # end-to-end: custom rules (both `=` and bare-glob forms) reach the recorder,
    # and --no-default-redactions switches the default set off
    cassette = tmp_path / "demo.json"
    cmd = [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(cassette),
        "--redact",
        "text=SCRUBBED",
        "--redact",
        "*planted*",
        "--no-default-redactions",
        "--",
        *reference_server_cmd(),
    ]
    messages = [
        *initialize_sequence(),
        tool_call(2, "echo", {"text": "hush", "planted_field": "x", "api_key": "sk-1"}),
    ]
    result = run_session(cmd, messages)
    assert result.returncode == 0

    text = json.dumps([m.model_dump() for m in Cassette.load(cassette).messages])
    # custom `=` rule applied with its replacement (key-based: only "text" keys)
    assert '"text": "SCRUBBED"' in text
    assert '"planted_field": "REDACTED"' in text  # bare glob form
    assert '"api_key": "sk-1"' in text  # defaults disabled: api_key survives


def test_partial_session_still_valid(tmp_path: Path) -> None:
    # Client sends only the handshake then closes stdin: the graceful shutdown path
    # still finalizes a valid, loadable cassette.
    cassette = tmp_path / "demo.json"
    run_session(_record_cmd(cassette), initialize_sequence())
    loaded = Cassette.load(cassette)
    assert loaded.protocol_version == "2024-11-05"


def _handshake_proc(cmd: list[str]) -> subprocess.Popen[bytes]:
    """Start a recording proxy and push the handshake through it."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdin is not None
    for msg in initialize_sequence():
        proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
    proc.stdin.flush()
    return proc


def test_hard_kill_leaves_a_recoverable_checkpoint(tmp_path: Path) -> None:
    # The whole point of checkpointing: SIGKILL runs no finalize path, so without a
    # sidecar the entire session would be lost with the process.
    cassette = tmp_path / "demo.json"
    partial_file = cassette.with_name(cassette.name + ".partial")
    cmd = _record_cmd(cassette, extra=("--checkpoint-interval", "0.2"))
    proc = _handshake_proc(cmd)
    try:
        # Poll for a checkpoint that has caught the server's reply, not just the
        # client's opening lines — the first one can land before the server answers.
        deadline = time.monotonic() + 20
        captured = None
        while time.monotonic() < deadline:
            if partial_file.exists():
                captured = Cassette.load(partial_file)
                if any(m.sender == "server" for m in captured.messages):
                    break
            time.sleep(0.1)
        assert captured is not None, "no checkpoint was written mid-session"
    finally:
        proc.kill()
        proc.wait(timeout=10)

    # The cassette path stays clean, so mode="once" re-records instead of replaying a
    # truncated session; the traffic itself survives in the sidecar.
    assert not cassette.exists()
    assert captured.protocol_version == "2024-11-05"


def test_clean_shutdown_removes_the_checkpoint(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    run_session(
        _record_cmd(cassette, extra=("--checkpoint-interval", "0.2")),
        initialize_sequence(),
    )
    assert Cassette.load(cassette).protocol_version == "2024-11-05"
    assert not cassette.with_name(cassette.name + ".partial").exists()


def test_checkpoint_interval_zero_writes_no_sidecar(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    partial_file = cassette.with_name(cassette.name + ".partial")
    proc = _handshake_proc(_record_cmd(cassette, extra=("--checkpoint-interval", "0")))
    try:
        time.sleep(2.0)
        assert not partial_file.exists()
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGTERM has no graceful-finalize semantics on Windows; see the "
    "CTRL_BREAK_EVENT test for the win32 equivalent",
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


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="CTRL_BREAK_EVENT is the Windows-only equivalent of SIGTERM",
)
def test_ctrl_break_finalizes_cassette(tmp_path: Path) -> None:
    # Windows analog of test_sigterm_finalizes_cassette: Ctrl+Break must finalize the
    # cassette (via the SIGBREAK handler), not abort the proxy (STATUS_CONTROL_C_EXIT).
    #
    # Delivering CTRL_BREAK_EVENT needs a real Windows console shared with the target's
    # process group. Some launchers (notably `uv run`) run without a console, so the
    # event never reaches the proxy; the test skips in that case rather than hang.
    cassette = tmp_path / "demo.json"
    proc = subprocess.Popen(
        _record_cmd(cassette),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    assert proc.stdin is not None
    try:
        for msg in initialize_sequence():
            proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
        proc.stdin.flush()
        time.sleep(3.0)  # let the proxy start and capture the handshake
        proc.send_signal(signal.CTRL_BREAK_EVENT)
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
    loaded = Cassette.load(cassette)
    assert len(loaded.messages) >= 1
