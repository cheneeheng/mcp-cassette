"""Replay tests (ITER_02 §04): round-trip, ordering, ignore_params, misses."""

from __future__ import annotations

import sys
from pathlib import Path

from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)


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


def _serve_cmd(cassette: Path, *extra: str) -> list[str]:
    return [sys.executable, "-m", "mcp_cassette", "serve", str(cassette), *extra]


def test_round_trip_identical(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [
        *initialize_sequence(),
        tool_call(2, "echo", {"text": "hello"}),
        tool_call(3, "add", {"a": 2, "b": 3}),
    ]
    recorded = run_session(
        [
            sys.executable,
            "-m",
            "mcp_cassette",
            "record",
            "--cassette",
            str(cassette),
            "--",
            *reference_server_cmd(),
        ],
        messages,
    )
    replayed = run_session(_serve_cmd(cassette), messages)

    assert replayed.returncode == 0
    for msg_id in (2, 3):
        rec = recorded.response_for(msg_id)
        rep = replayed.response_for(msg_id)
        assert rec is not None and rep is not None
        assert rec["result"] == rep["result"]


def test_per_method_queue_consumption(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [
        *initialize_sequence(),
        tool_call(2, "counter", {}),
        tool_call(3, "counter", {}),
    ]
    _record(cassette, messages)
    replayed = run_session(_serve_cmd(cassette), messages)

    first = replayed.response_for(2)
    second = replayed.response_for(3)
    assert first is not None and second is not None
    # distinct recorded responses consumed in order
    assert first["result"]["structuredContent"]["result"] == 1
    assert second["result"]["structuredContent"]["result"] == 2


def test_ignore_params_still_matches(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    recorded_msgs = [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]
    _record(cassette, recorded_msgs)

    # client varies an ignored field; matching must still succeed
    varied = [
        *initialize_sequence(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hi"}, "_nonce": "xyz"},
        },
    ]
    replayed = run_session(
        _serve_cmd(cassette, "--ignore-param", "/params/_nonce"), varied
    )
    resp = replayed.response_for(2)
    assert resp is not None
    assert "error" not in resp
    assert replayed.returncode == 0


def test_unmatched_request_errors_and_exits_3(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    _record(cassette, [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})])

    unmatched = [*initialize_sequence(), tool_call(2, "echo", {"text": "NOT RECORDED"})]
    replayed = run_session(_serve_cmd(cassette), unmatched)

    resp = replayed.response_for(2)
    assert resp is not None
    assert resp["error"]["code"] == -32001
    assert replayed.returncode == 3


def test_notification_anchored_after_response(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [*initialize_sequence(), tool_call(2, "notify", {})]
    _record(cassette, messages)
    replayed = run_session(_serve_cmd(cassette), messages)

    assert replayed.returncode == 0
    # the recorded server notification is replayed
    notes = replayed.notifications()
    assert any(n.get("method") == "notifications/message" for n in notes)
    # it comes after the matched response for id 2 in the output stream
    ids = [m.get("id") for m in replayed.messages]
    methods = [m.get("method") for m in replayed.messages]
    resp_idx = ids.index(2)
    note_idx = next(
        i
        for i, m in enumerate(methods)
        if m == "notifications/message" and i > resp_idx
    )
    assert note_idx > resp_idx


def test_protocol_version_verbatim_vs_rewrite(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    _record(cassette, [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})])

    # client requests a different protocol version
    init = initialize_sequence()
    init[0]["params"]["protocolVersion"] = "2099-01-01"  # type: ignore[index]
    msgs = [*init, tool_call(2, "echo", {"text": "hi"})]

    verbatim = run_session(_serve_cmd(cassette), msgs)
    resp = verbatim.response_for(1)
    assert resp is not None
    assert resp["result"]["protocolVersion"] == "2024-11-05"  # recorded value kept

    rewritten = run_session(_serve_cmd(cassette, "--rewrite-protocol-version"), msgs)
    resp2 = rewritten.response_for(1)
    assert resp2 is not None
    assert resp2["result"]["protocolVersion"] == "2099-01-01"  # rewritten to requested
