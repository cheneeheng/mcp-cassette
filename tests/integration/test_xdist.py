"""Parallel writers on one cassette path under the claim (ITER_04_v4 §04)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette
from mcp_cassette.session import CassetteSession
from mcp_cassette.session.claim import claim_path

TESTS_DIR = Path(__file__).resolve().parents[1]


def _suite(pytester: pytest.Pytester, cassette: Path, log: Path, mode: str) -> None:
    pytester.makeconftest(f"import sys\nsys.path.insert(0, {str(TESTS_DIR)!r})\n")
    pytester.makepyfile(
        f"""
        import time

        import pytest
        from scripted_client import (
            initialize_sequence,
            reference_server_cmd,
            run_session,
            tool_call,
        )

        @pytest.mark.parametrize("n", range(4))
        @pytest.mark.mcp_cassette(cassette={str(cassette)!r}, mode={mode!r})
        def test_echo(mcp_cassette, n):
            cmd = mcp_cassette.server_command(reference_server_cmd())
            with open({str(log)!r}, "a", encoding="utf-8") as fh:
                fh.write(("record" if "record" in cmd else "replay") + "\\n")
            messages = [*initialize_sequence(), tool_call(2, "echo", {{"text": "hi"}})]
            result = run_session(cmd, messages)
            assert result.response_for(2)["result"]["content"][0]["text"] == "hi"
            time.sleep(3.0)  # hold the claim long enough for the workers to overlap
        """
    )


def test_once_records_exactly_one_and_the_rest_replay(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # pytester decodes as UTF-8
    cassette = pytester.path / "shared.mcp.json"
    log = pytester.path / "actions.log"
    _suite(pytester, cassette, log, "once")

    pytester.runpytest_subprocess("-n", "2").assert_outcomes(passed=4)
    actions = log.read_text(encoding="utf-8").split()
    assert sorted(actions) == ["record", "replay", "replay", "replay"]
    assert Cassette.load(cassette).messages
    assert not claim_path(cassette).exists()

    # A second pass replays throughout: recording is forbidden and nothing fails.
    monkeypatch.setenv("MCP_CASSETTE_MODE", "none")
    pytester.runpytest_subprocess("-n", "2").assert_outcomes(passed=4)


def test_all_mode_fails_the_second_writer_naming_the_holder(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # pytester decodes as UTF-8
    cassette = pytester.path / "shared.mcp.json"
    _suite(pytester, cassette, pytester.path / "actions.log", "all")
    result = pytester.runpytest_subprocess("-n", "2")
    outcomes = result.parseoutcomes()
    assert outcomes.get("failed", 0) >= 1
    result.stdout.fnmatch_lines(["*held for writing by gw*"])
    assert not claim_path(cassette).exists()


def test_waiter_records_when_the_holder_wrote_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The zero-message branch: a holder can release with no file on disk, and the
    # waiter must re-resolve to record rather than hang or fail.
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "c.mcp.json"
    holder = CassetteSession(
        mode="once", cassette_path=cassette, report_path=tmp_path / "h.json"
    )
    assert "record" in holder.server_command(["python", "server.py"])
    waiter = CassetteSession(
        mode="once", cassette_path=cassette, report_path=tmp_path / "w.json"
    )
    timer = threading.Timer(0.5, holder.close)  # releases without recording a thing
    timer.start()
    command = waiter.server_command(["python", "server.py"])
    timer.join()
    assert "record" in command
    assert claim_path(cassette).exists()  # the waiter holds it now
    waiter.close()
    assert not claim_path(cassette).exists()
    assert not cassette.exists()
