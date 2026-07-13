"""Fault injection tests (ITER_04 §04): per-behavior semantics + overlay + inspect."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette.cassette import Fault, FaultOverlay


def _record(cassette: Path, messages: list[dict], *server_extra: str) -> None:
    cmd = [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(cassette),
        "--",
        *reference_server_cmd(*server_extra),
    ]
    run_session(cmd, messages)


def _write_overlay(path: Path, *faults: Fault) -> Path:
    overlay = FaultOverlay(faults=list(faults))
    path.write_text(overlay.model_dump_json(), encoding="utf-8")
    return path


def _serve(cassette: Path, overlay: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mcp_cassette",
        "serve",
        str(cassette),
        "--faults",
        str(overlay),
        *extra,
    ]


ECHO_ADD = [
    *initialize_sequence(),
    tool_call(2, "echo", {"text": "hi"}),
    tool_call(3, "add", {"a": 2, "b": 3}),
]


def test_error_fault_replaces_response(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(
        tmp_path / "f.json",
        Fault.error("tools/call", code=-32000, message="rate limited", nth=1),
    )
    result = run_session(_serve(cassette, overlay), ECHO_ADD)
    resp = result.response_for(2)
    assert resp is not None
    assert resp["error"]["code"] == -32000
    assert resp["error"]["message"] == "rate limited"
    # the second call is untouched
    assert result.response_for(3) is not None


def test_timeout_fault_suppresses_one_response(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(tmp_path / "f.json", Fault.timeout("tools/call", nth=1))
    # init + add answer; echo (nth=1) never responds
    result = run_session(_serve(cassette, overlay), ECHO_ADD, expected_responses=2)
    assert result.response_for(2) is None  # timed out
    assert result.response_for(3) is not None  # still served


def test_disconnect_before_response(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(tmp_path / "f.json", Fault.disconnect("tools/call", nth=1))
    result = run_session(_serve(cassette, overlay), ECHO_ADD, expected_responses=1)
    assert result.returncode == 0  # clean server death
    assert result.response_for(2) is None  # died before responding


def test_disconnect_after_response(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(
        tmp_path / "f.json", Fault.disconnect("tools/call", after_response=True, nth=1)
    )
    result = run_session(_serve(cassette, overlay), ECHO_ADD, expected_responses=2)
    assert result.returncode == 0
    assert result.response_for(2) is not None  # responded, then died
    assert result.response_for(3) is None


def test_malformed_wrong_id(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(
        tmp_path / "f.json",
        Fault.malformed("tools/call", strategy="wrong_id", nth=1),
    )
    result = run_session(_serve(cassette, overlay), ECHO_ADD, expected_responses=2)
    # no valid response under the requested id
    assert result.response_for(2) is None
    # but a stamped-with-unknown-id message came through
    assert any(m.get("id") == "mcp-cassette-unknown-id" for m in result.messages)


def test_delay_fault_still_responds_correctly(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(
        tmp_path / "f.json", Fault.delay("tools/call", ms=100, nth=1)
    )
    result = run_session(_serve(cassette, overlay), ECHO_ADD)
    resp = result.response_for(2)
    assert resp is not None
    assert resp["result"]["content"][0]["text"] == "hi"


def test_fired_timeout_consumes_queue_position(tmp_path: Path) -> None:
    # Two identical counter calls record responses 1 then 2. A timeout on nth=1 spends
    # the first queue position, so the second call must return the SECOND response (2).
    cassette = tmp_path / "c.json"
    counters = [
        *initialize_sequence(),
        tool_call(2, "counter", {}),
        tool_call(3, "counter", {}),
    ]
    _record(cassette, counters)
    overlay = _write_overlay(tmp_path / "f.json", Fault.timeout("tools/call", nth=1))
    result = run_session(_serve(cassette, overlay), counters, expected_responses=2)
    assert result.response_for(2) is None
    second = result.response_for(3)
    assert second is not None
    assert second["result"]["structuredContent"]["result"] == 2


def test_one_fault_per_request_first_wins(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(
        tmp_path / "f.json",
        Fault.error("tools/call", code=-32000, message="first", nth=1),
        Fault.timeout("tools/call", nth=1),
    )
    result = run_session(_serve(cassette, overlay), ECHO_ADD, expected_responses=2)
    resp = result.response_for(2)
    assert resp is not None
    assert resp["error"]["code"] == -32000  # the first overlay entry fired


def test_inspect_faults_dry_run(tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    _record(cassette, ECHO_ADD)
    overlay = _write_overlay(tmp_path / "f.json", Fault.timeout("tools/call", nth=1))
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_cassette",
            "inspect",
            str(cassette),
            "--faults",
            str(overlay),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0
    assert "dry-run" in out.stdout
    assert "timeout" in out.stdout
