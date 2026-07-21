"""Replay pacing integration (ITER_02_v3 §04): recorded gaps become real sleeps.

Bounds are deliberately asymmetric — a tight floor (the sleeps must actually happen)
and a loose ceiling (a loaded CI runner must not flake the suite).
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripted_client import initialize_sequence, run_session, tool_call

from mcp_cassette.cassette import Cassette, Fault, FaultOverlay, Message

GAP_MS = 500


def _client_messages() -> list[dict[str, Any]]:
    return [
        *initialize_sequence(),
        tool_call(2, "echo", {"text": "hi"}),
        tool_call(3, "add", {"a": 1, "b": 2}),
    ]


def _cassette(path: Path, gap_ms: int = GAP_MS) -> Path:
    init_request, initialized = initialize_sequence()
    messages: list[Message] = []
    t = 0

    def add(
        sender: str, kind: str, payload: dict[str, Any], msg_id: Any = None
    ) -> None:
        messages.append(
            Message(
                seq=len(messages),
                t_offset_ms=t,
                sender=sender,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                method=payload.get("method"),
                msg_id=msg_id,
                payload=payload,
            )
        )

    add("client", "request", init_request, 1)
    t += gap_ms
    add(
        "server",
        "response",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": init_request["params"]["protocolVersion"],
                "capabilities": {},
                "serverInfo": {"name": "paced", "version": "1.0"},
            },
        },
        1,
    )
    add("client", "notification", initialized)
    for msg_id in (2, 3):
        add("client", "request", tool_call(msg_id, "echo", {"text": "hi"}), msg_id)
        t += gap_ms
        add(
            "server",
            "response",
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": "hi"}]},
            },
            msg_id,
        )
    Cassette(recorded_at=datetime(2026, 7, 20, tzinfo=UTC), messages=messages).save(
        path
    )
    return path


def _serve(cassette: Path, *extra: str) -> list[str]:
    return [sys.executable, "-m", "mcp_cassette", "serve", str(cassette), *extra]


def _elapsed(cmd: list[str], messages: list[dict[str, Any]]) -> float:
    start = time.monotonic()
    result = run_session(cmd, messages)
    assert result.response_for(3) is not None
    return time.monotonic() - start


def test_recorded_gaps_are_replayed(tmp_path: Path) -> None:
    cassette = _cassette(tmp_path / "paced.mcp.json")
    messages = _client_messages()
    # Two tools/call requests carry the same params, so the client sends the same
    # payload twice; per_method ordering hands out the two recorded exchanges.
    messages[3] = tool_call(3, "echo", {"text": "hi"})

    instant = _elapsed(_serve(cassette), messages)
    paced = _elapsed(_serve(cassette, "--pace", "recorded"), messages)
    scaled = _elapsed(
        _serve(cassette, "--pace", "recorded", "--pace-scale", "0.1"), messages
    )

    # Three recorded gaps of 500 ms each must actually be spent.
    assert paced - instant >= 1.2
    assert paced - scaled >= 0.7
    assert paced < 30.0


def test_cap_bounds_a_pathological_gap(tmp_path: Path) -> None:
    cassette = _cassette(tmp_path / "slow.mcp.json", gap_ms=5000)
    messages = _client_messages()
    messages[3] = tool_call(3, "echo", {"text": "hi"})
    capped = _elapsed(
        _serve(cassette, "--pace", "recorded", "--pace-cap-ms", "50"), messages
    )
    # Uncapped this cassette would spend 15 s in sleeps.
    assert capped < 10.0


def _faults_file(tmp_path: Path, fault: Fault) -> str:
    path = tmp_path / "faults.json"
    path.write_text(
        FaultOverlay(faults=[fault]).model_dump_json(indent=2), encoding="utf-8"
    )
    return str(path)


def test_delay_fault_is_additive_on_top_of_pacing(tmp_path: Path) -> None:
    cassette = _cassette(tmp_path / "paced.mcp.json")
    messages = _client_messages()
    messages[3] = tool_call(3, "echo", {"text": "hi"})
    faults = _faults_file(tmp_path, Fault.delay("tools/call", ms=1000, nth=1))

    paced = _elapsed(_serve(cassette, "--pace", "recorded"), messages)
    both = _elapsed(
        _serve(cassette, "--pace", "recorded", "--faults", faults), messages
    )
    assert both - paced >= 0.7


def test_timeout_fault_spends_no_pacing_sleep(tmp_path: Path) -> None:
    # The faulted call never responds, so its recorded gap is never slept — the run
    # is strictly shorter than the same cassette paced without the fault.
    cassette = _cassette(tmp_path / "paced.mcp.json")
    messages = _client_messages()
    messages[3] = tool_call(3, "echo", {"text": "hi"})
    faults = _faults_file(tmp_path, Fault.timeout("tools/call", nth=1))

    start = time.monotonic()
    result = run_session(
        _serve(cassette, "--pace", "recorded", "--faults", faults),
        messages,
        expected_responses=2,
        settle=2.0,
    )
    elapsed = time.monotonic() - start
    assert result.response_for(2) is None
    paced = _elapsed(_serve(cassette, "--pace", "recorded"), messages)
    assert elapsed < paced


def test_pace_flags_without_pace_recorded_exit_2(tmp_path: Path) -> None:
    from mcp_cassette.cli import main

    cassette = _cassette(tmp_path / "paced.mcp.json")
    assert main(["serve", str(cassette), "--pace-scale", "2"]) == 2


def test_invalid_pace_scale_exits_2(tmp_path: Path) -> None:
    from mcp_cassette.cli import main

    cassette = _cassette(tmp_path / "paced.mcp.json")
    assert (
        main(["serve", str(cassette), "--pace", "recorded", "--pace-scale", "0"]) == 2
    )
