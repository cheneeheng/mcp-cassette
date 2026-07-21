"""Library-mode stdio integration (ITER_01_v3 §04): the third front door end to end."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette import CassetteError, use_cassette


def _messages() -> list[dict]:
    return [*initialize_sequence(), tool_call(2, "echo", {"text": "hello"})]


def test_record_then_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "library.mcp.json"

    with use_cassette(cassette, mode="once") as session:
        cmd = session.server_command(reference_server_cmd())
        recorded = run_session(cmd, _messages())
    assert cassette.exists()
    assert recorded.response_for(2) is not None

    with use_cassette(cassette, mode="once") as session:
        cmd = session.server_command(reference_server_cmd())
        replayed = run_session(cmd, _messages())
    assert replayed.returncode == 0
    assert replayed.response_for(2) == recorded.response_for(2)


def test_none_mode_without_a_cassette_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    with pytest.raises(CassetteError, match="recording is forbidden"):
        with use_cassette(tmp_path / "absent.mcp.json", mode="none") as session:
            session.server_command(reference_server_cmd())


def test_replay_miss_names_the_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "library.mcp.json"
    with use_cassette(cassette, mode="once") as session:
        run_session(session.server_command(reference_server_cmd()), _messages())

    with pytest.raises(CassetteError, match="unmatched request") as excinfo:
        with use_cassette(cassette, mode="none") as session:
            cmd = session.server_command(reference_server_cmd())
            run_session(cmd, [*_messages(), tool_call(3, "add", {"a": 1, "b": 2})])
    assert "tools/call" in str(excinfo.value)


def test_env_overrides_the_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CI invariant: MCP_CASSETTE_MODE=none forbids recording through the library
    # door too, whatever the harness hard-codes.
    monkeypatch.setenv("MCP_CASSETTE_MODE", "none")
    with pytest.raises(CassetteError, match="recording is forbidden"):
        with use_cassette(tmp_path / "absent.mcp.json", mode="all") as session:
            session.server_command(reference_server_cmd())
