"""``mcp-cassette check`` exit codes against the reference server.

The contract CI reads: ``0`` unchanged, ``5`` tools changed, ``7`` the server did not
start, ``2`` usage. Run as a subprocess, the way a CI step runs it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

STUB = str(Path(__file__).parents[1] / "stub_stdio_server.py")


def _check(
    cassette: Path, *server: str, flags: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_cassette",
            "check",
            str(cassette),
            *flags,
            "--",
            *server,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _recording_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # CI sets MCP_CASSETTE_MODE=none, which --record rightly refuses; the
    # subprocesses inherit the environment, so clear it for these tests.
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A full fixture-style session: handshake, tool list, and a tool call."""
    cassette = tmp_path_factory.mktemp("cassettes") / "session.mcp.json"
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
    return cassette


def test_unchanged_server_exits_0_against_a_full_session(session: Path) -> None:
    result = _check(session, *reference_server_cmd())

    assert result.returncode == 0, result.stderr
    assert "unchanged" in result.stdout


def test_changed_server_exits_5_and_names_the_change(session: Path) -> None:
    result = _check(session, *reference_server_cmd("--tools-variant", "v2"))

    assert result.returncode == 5, result.stderr
    assert "echo: description changed" in result.stdout
    assert "echo: inputSchema changed" in result.stdout


def test_record_refuses_a_full_session(session: Path) -> None:
    result = _check(session, *reference_server_cmd(), flags=("--record",))

    assert result.returncode == 2
    assert "MCP_CASSETTE_MODE=all" in result.stderr


def test_record_then_check(tmp_path: Path) -> None:
    cassette = tmp_path / "tools.mcp.json"

    recorded = _check(cassette, *reference_server_cmd(), flags=("--record",))
    assert recorded.returncode == 0, recorded.stderr
    assert "wrote baseline" in recorded.stdout

    assert _check(cassette, *reference_server_cmd()).returncode == 0


def test_server_that_does_not_start_exits_7_with_its_stderr(session: Path) -> None:
    result = _check(session, sys.executable, STUB, "--exit", "3")

    assert result.returncode == 7
    assert "did not start" in result.stderr
    assert "stub: package download failed" in result.stderr


def test_env_flag_reaches_the_server(tmp_path: Path) -> None:
    cassette = tmp_path / "env.mcp.json"
    server = (sys.executable, STUB, "--describe-env", "STUB_KEY")
    env = ("--env", "STUB_KEY=dummy")

    assert _check(cassette, *server, flags=("--record", *env)).returncode == 0
    assert _check(cassette, *server, flags=env).returncode == 0
    assert _check(cassette, *server).returncode == 5


@pytest.mark.parametrize(
    ("flags", "server", "message"),
    [
        ((), (), "pass the server command after --"),
        (("--env", "NOEQUALS"), (sys.executable, STUB), "KEY=VALUE"),
        ((), (sys.executable, STUB), "--record"),
    ],
)
def test_usage_errors_exit_2(
    tmp_path: Path, flags: tuple[str, ...], server: tuple[str, ...], message: str
) -> None:
    result = _check(tmp_path / "absent.mcp.json", *server, flags=flags)

    assert result.returncode == 2
    assert message in result.stderr


def test_unreadable_baseline_exits_2(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mcp.json"
    broken.write_text("{not json", encoding="utf-8")

    result = _check(broken, sys.executable, STUB)

    assert result.returncode == 2
    assert "mcp-cassette check:" in result.stderr
