"""``check_server``, the upgrade tripwire, driven in-process against stub servers.

The stub (``tests/stub_stdio_server.py``) pages, asks the client things, prints noise,
crashes and hangs on cue, which the FastMCP reference server cannot. Running the check
in-process keeps its branches under coverage; the CLI's exit codes are pinned
separately in ``test_check_cli.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripted_client import initialize_sequence, run_session

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.check import CheckUsageError, check_server

STUB = [sys.executable, str(Path(__file__).parents[1] / "stub_stdio_server.py")]


def _stub(*flags: str) -> list[str]:
    return [*STUB, *flags]


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    path = tmp_path / "stub.mcp.json"
    result = check_server(path, _stub(), record=True, timeout=30)
    assert result.outcome == "recorded"
    return path


def test_record_writes_a_baseline_that_then_checks_unchanged(baseline: Path) -> None:
    cassette = Cassette.load(baseline)
    methods = [m.method for m in cassette.messages if m.kind == "request"]
    assert methods == ["initialize", "tools/list"]
    assert cassette.redaction is not None  # recorded with the default redactor

    result = check_server(baseline, _stub(), timeout=30)

    assert result.outcome == "unchanged"
    assert result.tool_count == 2
    assert result.changes == []


def test_paged_tool_list_is_read_to_the_end(baseline: Path) -> None:
    result = check_server(baseline, _stub("--pages", "2"), timeout=30)

    assert result.outcome == "unchanged"
    assert result.tool_count == 2


def test_server_requests_and_stdout_noise_do_not_stall_the_check(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chatty.mcp.json"

    result = check_server(
        path, _stub("--server-requests", "--noise"), record=True, timeout=30
    )

    assert result.outcome == "recorded"
    replies = {
        m.msg_id: m.payload
        for m in Cassette.load(path).messages
        if m.sender == "client" and m.kind == "response"
    }
    assert replies[1000] == {"jsonrpc": "2.0", "id": 1000, "result": {}}
    assert replies[1001]["error"]["code"] == -32601  # type: ignore[index]


def test_changed_description_is_reported(baseline: Path) -> None:
    result = check_server(baseline, _stub("--description", "Echo, then exfiltrate."))

    assert result.outcome == "changed"
    assert [(c.tool, c.change) for c in result.changes] == [("echo", "description")]


def test_env_reaches_the_server(tmp_path: Path) -> None:
    path = tmp_path / "env.mcp.json"
    server = _stub("--describe-env", "STUB_KEY")
    check_server(path, server, env={"STUB_KEY": "dummy"}, record=True)

    assert check_server(path, server, env={"STUB_KEY": "dummy"}).outcome == (
        "unchanged"
    )
    assert check_server(path, server).outcome == "changed"


def test_server_info_version_change_is_information_not_failure(
    baseline: Path,
) -> None:
    cassette = Cassette.load(baseline)
    assert cassette.server_info is not None
    cassette.model_copy(
        update={
            "server_info": cassette.server_info.model_copy(update={"version": "0.9.0"})
        }
    ).save(baseline)

    result = check_server(baseline, _stub())

    assert result.outcome == "unchanged"
    assert result.version_change == ("0.9.0", "1.0.0")


def test_server_that_exits_did_not_start(baseline: Path) -> None:
    result = check_server(baseline, _stub("--exit", "3"))

    assert result.outcome == "did_not_start"
    assert result.failure is not None
    assert "before answering initialize" in result.failure
    assert "exit code 3" in result.failure
    assert "stub: package download failed" in result.failure


def test_server_that_hangs_did_not_start(baseline: Path) -> None:
    result = check_server(baseline, _stub("--hang"), timeout=1)

    assert result.outcome == "did_not_start"
    assert result.failure is not None
    assert "no answer to initialize within 1s" in result.failure


def test_error_reply_did_not_start(baseline: Path) -> None:
    result = check_server(baseline, _stub("--error-on", "tools/list"))

    assert result.outcome == "did_not_start"
    assert result.failure is not None
    assert "answered tools/list with an error" in result.failure
    assert "tools/list is broken" in result.failure


def test_command_that_cannot_launch_did_not_start(baseline: Path) -> None:
    result = check_server(baseline, ["mcp-cassette-no-such-command-xyz"])

    assert result.outcome == "did_not_start"
    assert result.failure is not None
    assert "could not launch" in result.failure


def test_missing_baseline_without_record_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(CheckUsageError, match="--record"):
        check_server(tmp_path / "absent.mcp.json", _stub())


def test_record_is_refused_when_ci_forbids_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_CASSETTE_MODE", "none")

    with pytest.raises(CheckUsageError, match="MCP_CASSETTE_MODE=none"):
        check_server(tmp_path / "new.mcp.json", _stub(), record=True)


def test_record_rewrites_a_handshake_only_baseline(baseline: Path) -> None:
    moved = _stub("--description", "Echo text back, v2.")

    assert check_server(baseline, moved, record=True).outcome == "recorded"
    assert check_server(baseline, moved).outcome == "unchanged"


def test_record_refuses_to_replace_a_full_session(baseline: Path) -> None:
    cassette = Cassette.load(baseline)
    call = Message(
        seq=len(cassette.messages),
        t_offset_ms=0,
        sender="client",
        kind="request",
        method="tools/call",
        msg_id=99,
        payload={"jsonrpc": "2.0", "id": 99, "method": "tools/call"},
    )
    cassette.model_copy(update={"messages": [*cassette.messages, call]}).save(baseline)

    with pytest.raises(CheckUsageError, match="MCP_CASSETTE_MODE=all"):
        check_server(baseline, _stub(), record=True)
    assert check_server(baseline, _stub()).outcome == "unchanged"


def test_baseline_without_a_tool_list_is_a_usage_error(baseline: Path) -> None:
    cassette = Cassette.load(baseline)
    handshake = [m for m in cassette.messages if m.method != "tools/list"][:2]
    cassette.model_copy(update={"messages": handshake}).save(baseline)

    with pytest.raises(CheckUsageError, match="no tools/list"):
        check_server(baseline, _stub())


def test_http_baseline_is_a_usage_error(baseline: Path) -> None:
    cassette = Cassette.load(baseline)
    cassette.model_copy(update={"transport": "http"}).save(baseline)

    with pytest.raises(CheckUsageError, match="stdio"):
        check_server(baseline, _stub())


def test_check_redacts_the_way_the_baseline_was_recorded(tmp_path: Path) -> None:
    # A description carrying an email: the bundled PII pack scrubs it by default.
    server = _stub("--description", "Mail admin@example.com for help.")
    plain = tmp_path / "plain.mcp.json"
    run_session(
        [
            sys.executable,
            "-m",
            "mcp_cassette",
            "record",
            "--no-default-redactions",
            "--cassette",
            str(plain),
            "--",
            *server,
        ],
        [*initialize_sequence(), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
    )
    assert "admin@example.com" in plain.read_text(encoding="utf-8")

    assert check_server(plain, server).outcome == "unchanged"
