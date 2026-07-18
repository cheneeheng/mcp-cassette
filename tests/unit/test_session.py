"""CassetteSession and plugin-helper unit tests: modes, commands, finalize."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio.abc
import pytest

from mcp_cassette.cassette import Fault, MatchConfig
from mcp_cassette.pytest_plugin import _cassette_path, _resolve_mode
from mcp_cassette.report import write_report
from mcp_cassette.session import CassetteError, CassetteSession


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


def test_none_with_cassette_replays(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    cassette.write_text("{}", encoding="utf-8")
    session = _session("none", cassette, tmp_path)
    assert "serve" in session.server_command(["python", "server.py"])


def test_with_faults_under_recording_fails_fast(tmp_path: Path) -> None:
    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    faulted = session.with_faults(Fault.timeout("tools/call"))
    with pytest.raises(CassetteError, match="replay only"):
        faulted.server_command(["python", "server.py"])


def test_with_faults_under_replay_writes_sidecar(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    cassette.write_text("{}", encoding="utf-8")
    session = _session("once", cassette, tmp_path)
    faulted = session.with_faults(Fault.timeout("tools/call"))
    cmd = faulted.server_command(["python", "server.py"])
    assert "--faults" in cmd
    sidecar = Path(cmd[cmd.index("--faults") + 1])
    assert sidecar.exists()
    assert "timeout" in sidecar.read_text(encoding="utf-8")


def test_match_flags_include_ignore_params_and_rewrite(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    cassette.write_text("{}", encoding="utf-8")
    session = CassetteSession(
        mode="once",
        cassette_path=cassette,
        match=MatchConfig(
            ignore_params=["/params/_nonce"], rewrite_protocol_version=True
        ),
        report_path=tmp_path / "report.json",
    )
    cmd = session.server_command(["python", "server.py"])
    assert cmd[cmd.index("--ignore-param") + 1] == "/params/_nonce"
    assert "--rewrite-protocol-version" in cmd


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


def test_finalize_without_report_is_silent(tmp_path: Path) -> None:
    # the engine never ran, so no report was written: finalize must not fail
    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    session.server_command(["python", "server.py"])
    session.finalize()


# --- plugin helpers -----------------------------------------------------------------


def test_invalid_mode_rejected(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    monkeypatch.setenv("MCP_CASSETTE_MODE", "bogus")
    with pytest.raises(ValueError, match="invalid mcp_cassette mode"):
        _resolve_mode({}, request.config)


def test_marker_cassette_kwarg_overrides_path() -> None:
    explicit: Any = None  # request is unused when the marker names the cassette
    assert _cassette_path(explicit, {"cassette": "x/y.mcp.json"}) == Path(
        "x/y.mcp.json"
    )


def test_server_url_without_http_extra_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # None in sys.modules makes the guarded import raise ImportError, exactly as
    # a core-only install (no [http] extra) would.
    monkeypatch.setitem(sys.modules, "mcp_cassette.transports.http", None)
    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    with pytest.raises(CassetteError, match="halted"):
        session.server_url("http://127.0.0.1:9/mcp")


def test_server_url_start_failure_closes_portal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_cassette.transports.http import RecordingProxy

    async def broken_serve(
        self: Any,
        *,
        task_status: anyio.abc.TaskStatus[str] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        raise RuntimeError("bind failed")

    monkeypatch.setattr(RecordingProxy, "serve", broken_serve)
    session = _session("all", tmp_path / "c.mcp.json", tmp_path)
    with pytest.raises(RuntimeError, match="bind failed"):
        session.server_url("http://127.0.0.1:9/mcp")
    assert session._portal_cm is None  # the portal thread was torn down
