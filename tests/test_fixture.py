"""Fixture / CassetteSession tests (ITER_03 §04): modes, commands, finalize."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette.cassette import Cassette
from mcp_cassette.report import write_report
from mcp_cassette.session import CassetteError, CassetteSession

# --- Unit: mode resolution and command building -----------------------------------


def _session(mode: str, cassette: Path, tmp: Path) -> CassetteSession:
    return CassetteSession(
        mode=mode,  # type: ignore[arg-type]
        cassette_path=cassette,
        report_path=tmp / "report.json",
    )


def test_all_mode_builds_record_command(tmp_path: Path) -> None:
    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    cmd = session.server_command(["python", "server.py"])
    assert "record" in cmd
    assert "--cassette" in cmd
    assert cmd[-2:] == ["python", "server.py"]


def test_once_replays_when_cassette_present(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    cassette.write_text("{}", encoding="utf-8")
    session = _session("once", cassette, tmp_path)
    cmd = session.server_command(["python", "server.py"])
    assert "serve" in cmd
    assert "record" not in cmd


def test_none_without_cassette_raises(tmp_path: Path) -> None:
    session = _session("none", tmp_path / "missing.mcp.json", tmp_path)
    with pytest.raises(CassetteError, match="recording is forbidden"):
        session.server_command(["python", "server.py"])


def test_with_faults_under_recording_fails_fast(tmp_path: Path) -> None:
    from mcp_cassette.cassette import Fault

    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    faulted = session.with_faults(Fault.timeout("tools/call"))
    with pytest.raises(CassetteError, match="replay only"):
        faulted.server_command(["python", "server.py"])


def test_finalize_flags_empty_recording(tmp_path: Path) -> None:
    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    session.server_command(["python", "server.py"])  # sets action=record
    write_report(str(session.report_path), {"messages": 0})
    with pytest.raises(CassetteError, match="zero messages"):
        session.finalize()


def test_finalize_flags_replay_misses(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    cassette.write_text("{}", encoding="utf-8")
    session = _session("once", cassette, tmp_path)
    session.server_command(["python", "server.py"])  # sets action=replay
    write_report(str(session.report_path), {"misses": ["tools/call params={}"]})
    with pytest.raises(CassetteError, match="unmatched"):
        session.finalize()


# --- Integration: once records then replays ---------------------------------------


def test_once_records_then_replays(tmp_path: Path) -> None:
    cassette = tmp_path / "cassettes" / "demo.mcp.json"
    messages = [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]

    # first run: no cassette -> record
    rec_session = _session("once", cassette, tmp_path)
    rec_cmd = rec_session.server_command(reference_server_cmd())
    run_session(rec_cmd, messages)
    rec_session.finalize()  # non-empty recording, no misses
    assert cassette.exists()

    # second run: cassette present -> replay offline (no reference server involved)
    play_session = _session("once", cassette, tmp_path)
    play_cmd = play_session.server_command(["definitely-not-a-real-binary"])
    result = run_session(play_cmd, messages)
    play_session.finalize()
    assert result.returncode == 0
    resp = result.response_for(2)
    assert resp is not None
    assert resp["result"]["content"][0]["text"] == "hi"


def test_new_episodes_appends_novel_call(tmp_path: Path) -> None:
    cassette = tmp_path / "cassettes" / "demo.mcp.json"

    # seed a cassette with just echo
    seed = _session("all", cassette, tmp_path)
    run_session(
        seed.server_command(reference_server_cmd()),
        [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})],
    )
    before = len(Cassette.load(cassette).messages)

    # new_episodes: echo replays from cassette; novel add() falls through and appends
    ne = _session("new_episodes", cassette, tmp_path)
    run_session(
        ne.server_command(reference_server_cmd()),
        [
            *initialize_sequence(),
            tool_call(2, "echo", {"text": "hi"}),
            tool_call(3, "add", {"a": 2, "b": 3}),
        ],
    )
    after = Cassette.load(cassette)
    assert len(after.messages) > before
    methods = [m.method for m in after.messages]
    assert methods.count("tools/call") >= 2


# --- pytester: plugin wiring and mode precedence ----------------------------------


def test_env_var_overrides_marker(pytester: pytest.Pytester, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MCP_CASSETTE_MODE", "all")
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.mcp_cassette(mode="none")
        def test_mode(mcp_cassette):
            assert mcp_cassette.mode == "all"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_marker_overrides_ini(pytester: pytest.Pytester, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    pytester.makeini(
        """
        [pytest]
        mcp_cassette_mode = none
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.mcp_cassette(mode="all")
        def test_mode(mcp_cassette):
            assert mcp_cassette.mode == "all"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_parametrized_paths_are_unique(
    pytester: pytest.Pytester, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    pytester.makepyfile(
        """
        import pytest

        _seen = []

        @pytest.mark.parametrize("x", [1, 2])
        def test_p(mcp_cassette, x):
            _seen.append(str(mcp_cassette.cassette_path))
            if len(_seen) == 2:
                assert _seen[0] != _seen[1], "parametrized cases share a cassette path"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=2)
