"""R005 on a real recording, through the CLI (ITER_02_v4 §04)."""

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

API_KEY = "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"


def _lint(cassette: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_cassette", "lint", str(cassette), *extra],
        capture_output=True,
        text=True,
    )


def test_planted_key_in_a_tool_result_is_a_warning(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
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
        [*initialize_sequence(), tool_call(2, "echo", {"text": f"key {API_KEY}"})],
    )
    clean_exit = _lint(cassette, "--no-config")
    assert clean_exit.returncode == 0, clean_exit.stderr
    r005 = [line for line in clean_exit.stdout.splitlines() if line.startswith("R005")]
    assert any(
        line.startswith("R005 warning /messages/")
        and "/payload/result/content/0/text " in line
        for line in r005
    )
    assert API_KEY not in clean_exit.stdout

    gated = _lint(cassette, "--no-config", "--fail-on", "warning")
    assert gated.returncode == 4
