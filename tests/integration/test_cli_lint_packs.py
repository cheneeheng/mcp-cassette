"""lint --pattern-pack integration (ITER_04_v3 §04): end to end on a recording."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

PACK = """
version = 1

[[patterns]]
id = "P001"
label = "echo-mention"
regex = 'given text'
flags = ["i"]
"""


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cassette = tmp_path_factory.mktemp("cassettes") / "lint.mcp.json"
    messages: list[dict[str, Any]] = [
        *initialize_sequence(),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call(3, "echo", {"text": "hi"}),
    ]
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
        messages,
    )
    return cassette


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_cassette", "lint", *args, "--no-config"],
        capture_output=True,
        text=True,
    )


def test_recorded_cassette_is_clean(recorded: Path) -> None:
    result = _cli(str(recorded))
    assert result.returncode == 0
    assert "clean: no findings" in result.stdout


def test_pack_fires_naming_its_id_and_pointer(recorded: Path, tmp_path: Path) -> None:
    pack = tmp_path / "team.toml"
    pack.write_text(PACK, encoding="utf-8")
    result = _cli(str(recorded), "--pattern-pack", str(pack))
    assert result.returncode == 4
    assert "P001 error /messages/" in result.stdout
    assert "echo-mention" in result.stdout
