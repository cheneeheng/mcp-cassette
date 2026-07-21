"""System tests (ITER_03 §04): full fixture flows and plugin wiring.

Unit tests for mode resolution / command building live in tests/unit/test_session.py.
"""

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
from mcp_cassette.session import CassetteSession


def _session(mode: str, cassette: Path, tmp: Path) -> CassetteSession:
    return CassetteSession(
        mode=mode,  # type: ignore[arg-type]
        cassette_path=cassette,
        report_path=tmp / "report.json",
    )


# --- once records then replays ------------------------------------------------------


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


def test_fixture_and_library_agree_on_the_env_mode(
    pytester: pytest.Pytester,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    # The two front doors must never drift on what MCP_CASSETTE_MODE means.
    monkeypatch.setenv("MCP_CASSETTE_MODE", "none")
    pytester.makepyfile(
        """
        from mcp_cassette import resolve_mode

        def test_mode(mcp_cassette):
            assert mcp_cassette.mode == resolve_mode("all") == "none"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_marker_pacing_reaches_the_serve_command(
    pytester: pytest.Pytester,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.mcp_cassette(
            mode="none", pace="recorded", pace_scale=0.2, pace_cap_ms=1000
        )
        def test_pace(mcp_cassette, tmp_path):
            cassette = tmp_path / "c.mcp.json"
            cassette.write_text(
                '{"format_version": 2, "recorded_at": "2026-07-20T00:00:00Z",'
                ' "messages": []}'
            )
            mcp_cassette.cassette_path = cassette
            cmd = mcp_cassette.server_command(["python", "-m", "server"])
            assert "--pace" in cmd
            assert cmd[cmd.index("--pace") + 1] == "recorded"
            assert cmd[cmd.index("--pace-scale") + 1] == "0.2"
            assert cmd[cmd.index("--pace-cap-ms") + 1] == "1000"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_parametrized_paths_are_unique(pytester: pytest.Pytester, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
