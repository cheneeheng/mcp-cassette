"""System tests (ITER_02_v2 §04/§05): server_url fixture flows and mode matrix."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from scripted_client import initialize_sequence, tool_call
from scripted_http_client import (
    free_port,
    run_http_session,
    start_reference_http_server,
)

from mcp_cassette.cassette import Cassette
from mcp_cassette.session import CassetteError, CassetteSession


@pytest.fixture(scope="module")
def ref_server() -> Iterator[str]:
    port = free_port()
    proc = start_reference_http_server(port)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _session(mode: str, cassette: Path, tmp: Path) -> CassetteSession:
    return CassetteSession(
        mode=mode,  # type: ignore[arg-type]
        cassette_path=cassette,
        report_path=tmp / "report.json",
    )


def _messages() -> list[dict[str, object]]:
    return [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]


def test_once_records_then_replays(ref_server: str, tmp_path: Path) -> None:
    cassette = tmp_path / "cassettes" / "demo.mcp.json"

    # first run: no cassette -> record through the proxy
    rec = _session("once", cassette, tmp_path)
    url = rec.server_url(ref_server)
    assert url.startswith("http://127.0.0.1:")
    run_http_session(url, _messages())
    rec.finalize()
    assert cassette.exists()
    assert Cassette.load(cassette).transport == "http"

    # second run: cassette present -> replay offline (real URL may be dead)
    play = _session("once", cassette, tmp_path)
    url = play.server_url("http://127.0.0.1:9/mcp")
    result = run_http_session(url, _messages())
    play.finalize()
    assert result.response_for(2)["result"]["content"][0]["text"] == "hi"


def test_none_mode_fails_without_cassette(tmp_path: Path) -> None:
    session = _session("none", tmp_path / "missing.json", tmp_path)
    with pytest.raises(CassetteError, match="recording is forbidden"):
        session.server_url("http://127.0.0.1:9/mcp")


def test_all_mode_rerecords(ref_server: str, tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    first = _session("all", cassette, tmp_path)
    run_http_session(first.server_url(ref_server), _messages())
    first.finalize()
    stamp = Cassette.load(cassette).recorded_at

    second = _session("all", cassette, tmp_path)
    run_http_session(second.server_url(ref_server), _messages())
    second.finalize()
    assert Cassette.load(cassette).recorded_at != stamp  # re-recorded, not replayed


def test_new_episodes_appends_novel_call(ref_server: str, tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    seed = _session("all", cassette, tmp_path)
    run_http_session(seed.server_url(ref_server), _messages())
    seed.finalize()
    before = len(Cassette.load(cassette).messages)

    ne = _session("new_episodes", cassette, tmp_path)
    run_http_session(
        ne.server_url(ref_server),
        [*_messages(), tool_call(3, "add", {"a": 2, "b": 3})],
    )
    ne.finalize()
    after = Cassette.load(cassette)
    assert len(after.messages) == before + 2  # exactly the novel exchange


def test_replay_miss_fails_the_test(ref_server: str, tmp_path: Path) -> None:
    cassette = tmp_path / "c.json"
    seed = _session("all", cassette, tmp_path)
    run_http_session(seed.server_url(ref_server), _messages())
    seed.finalize()

    play = _session("none", cassette, tmp_path)
    run_http_session(
        play.server_url("http://127.0.0.1:9/mcp"),
        [*initialize_sequence(), tool_call(9, "never_recorded", {})],
    )
    with pytest.raises(CassetteError, match="unmatched request"):
        play.finalize()


def test_record_against_dead_upstream_fails_loudly(tmp_path: Path) -> None:
    session = _session("once", tmp_path / "c.json", tmp_path)
    url = session.server_url(f"http://127.0.0.1:{free_port()}/mcp")
    run_http_session(url, initialize_sequence()[:1])
    with pytest.raises(CassetteError, match="recording failed"):
        session.finalize()


def test_pytester_once_flow(
    pytester: pytest.Pytester,
    ref_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical README flow through the real plugin, twice: record then replay."""
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    monkeypatch.setenv("MCP_REF_URL", ref_server)
    pytester.makepyfile(
        """
        import os
        from scripted_http_client import run_http_session
        from scripted_client import initialize_sequence, tool_call

        def test_agent_reads_remote_tracker(mcp_cassette):
            url = mcp_cassette.server_url(os.environ["MCP_REF_URL"])
            result = run_http_session(
                url,
                [*initialize_sequence(), tool_call(2, "echo", {"text": "triaged"})],
            )
            assert (
                result.response_for(2)["result"]["content"][0]["text"] == "triaged"
            )
        """
    )
    pytester.syspathinsert(str(Path(__file__).parent.parent))
    pytester.runpytest_inprocess().assert_outcomes(passed=1)  # records
    pytester.runpytest_inprocess().assert_outcomes(passed=1)  # replays offline
