"""diff CLI integration (ITER_03_v3 §04): exit codes over recorded cassettes."""

from __future__ import annotations

import json
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

from mcp_cassette.cassette import Cassette


def _record(cassette: Path, messages: list[dict[str, Any]]) -> None:
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


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_cassette", *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cassette = tmp_path_factory.mktemp("cassettes") / "old.mcp.json"
    _record(
        cassette,
        [
            *initialize_sequence(),
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            tool_call(3, "echo", {"text": "hi"}),
        ],
    )
    return cassette


def _with_changed_description(source: Path, target: Path) -> Path:
    cassette = Cassette.load(source)
    for message in cassette.messages:
        if not isinstance(message.payload, dict):
            continue
        tools = message.payload.get("result", {}).get("tools")
        if isinstance(tools, list) and tools:
            tools[0]["description"] = "A deliberately rewritten description."
    cassette.save(target)
    return target


def test_identical_files_exit_0(baseline: Path) -> None:
    result = _cli("diff", str(baseline), str(baseline))
    assert result.returncode == 0
    assert "identical" in result.stdout


def test_changed_description_exits_5_and_names_the_tool(
    baseline: Path, tmp_path: Path
) -> None:
    changed = _with_changed_description(baseline, tmp_path / "new.mcp.json")
    result = _cli("diff", str(baseline), str(changed))
    assert result.returncode == 5
    assert "tools:" in result.stdout
    assert "description changed" in result.stdout


def test_missing_file_exits_2_naming_the_path(baseline: Path, tmp_path: Path) -> None:
    missing = tmp_path / "absent.mcp.json"
    result = _cli("diff", str(baseline), str(missing))
    assert result.returncode == 2
    assert "absent.mcp.json" in result.stderr


def test_tools_only_ignores_a_sequence_difference(
    baseline: Path, tmp_path: Path
) -> None:
    cassette = Cassette.load(baseline)
    extended = cassette.model_copy(
        update={"messages": [*cassette.messages, *cassette.messages[-2:]]}
    )
    target = tmp_path / "extended.mcp.json"
    extended.save(target)

    assert _cli("diff", str(baseline), str(target)).returncode == 5
    result = _cli("diff", str(baseline), str(target), "--tools-only")
    assert result.returncode == 0


def test_json_format_round_trips(baseline: Path, tmp_path: Path) -> None:
    changed = _with_changed_description(baseline, tmp_path / "new.mcp.json")
    result = _cli("diff", str(baseline), str(changed), "--format", "json")
    assert result.returncode == 5
    document = json.loads(result.stdout)
    assert document["identical"] is False
    assert document["tools"][0]["change"] == "description"
