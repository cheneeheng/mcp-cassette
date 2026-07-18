"""Lint end-to-end (ITER_04_v2 §04): a real recording of the benign reference
server lints clean."""

from __future__ import annotations

import sys
from pathlib import Path

from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette.cli import main


def test_recorded_reference_server_lints_clean(tmp_path: Path) -> None:
    cassette = tmp_path / "ref.json"
    run_session(
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
        [
            *initialize_sequence(),
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            tool_call(3, "echo", {"text": "hi"}),
        ],
    )
    assert cassette.exists()
    assert main(["lint", str(cassette)]) == 0  # benign descriptions: no findings
