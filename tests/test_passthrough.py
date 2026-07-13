"""Passthrough integration test (SKELETON §04): client - proxy - reference server."""

from __future__ import annotations

import sys
from pathlib import Path

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


def test_proxy_forwards_responses_and_writes_cassette(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.json"
    messages = [
        *initialize_sequence(),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call(3, "echo", {"text": "hello"}),
    ]
    result = run_session(_record_cmd(cassette), messages)

    assert result.returncode == 0
    # the client saw a real response for every request it sent
    assert result.response_for(1) is not None  # initialize
    assert result.response_for(2) is not None  # tools/list
    echo = result.response_for(3)
    assert echo is not None
    assert "hello" in echo["result"]["content"][0]["text"]

    # a valid, loadable cassette was written end to end
    loaded = Cassette.load(cassette)
    assert loaded.protocol_version == "2024-11-05"
    assert loaded.server_info is not None
    assert len(loaded.messages) > 0
