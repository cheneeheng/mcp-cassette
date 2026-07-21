"""Library-mode unit tests (ITER_01_v3 §04): resolve_mode and use_cassette."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_cassette import CassetteError, resolve_mode, use_cassette
from mcp_cassette.session import CassetteSession


def test_env_beats_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CASSETTE_MODE", "none")
    assert resolve_mode("all") == "none"


def test_argument_beats_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    assert resolve_mode("all") == "all"
    assert resolve_mode() == "once"


def test_invalid_env_names_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CASSETTE_MODE", "sometimes")
    with pytest.raises(ValueError, match="env MCP_CASSETTE_MODE"):
        resolve_mode()


def test_invalid_argument_names_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    with pytest.raises(ValueError, match="mode= argument"):
        resolve_mode("sometimes")


def test_yields_session_with_resolved_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    with use_cassette(tmp_path / "c.mcp.json", mode="all") as session:
        assert isinstance(session, CassetteSession)
        assert session.mode == "all"


def test_clean_exit_finalizes(tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatched = CassetteSession.finalize

    def spy(self: CassetteSession) -> None:
        calls.append("finalize")
        monkeypatched(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(CassetteSession, "finalize", spy)
        with use_cassette(tmp_path / "c.mcp.json", mode="all"):
            pass
    assert calls == ["finalize"]


def test_raising_body_propagates_without_cassette_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with use_cassette(tmp_path / "c.mcp.json", mode="all") as session:
            # A miss would normally raise CassetteError from finalize(); the real
            # failure must not be buried under it.
            session._last_action = "replay"  # noqa: SLF001 — same package
            raise RuntimeError("boom")


def test_temp_report_dir_is_removed(tmp_path: Path) -> None:
    with use_cassette(tmp_path / "c.mcp.json", mode="all") as session:
        report_dir = session.report_path.parent
        assert report_dir.is_dir()
    assert not report_dir.exists()


def test_explicit_report_path_survives(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    with use_cassette(tmp_path / "c.mcp.json", mode="all", report_path=report):
        pass
    assert report.exists()


def test_close_is_idempotent(tmp_path: Path) -> None:
    session = CassetteSession(mode="all", cassette_path=tmp_path / "c.mcp.json")
    session.close()
    session.close()


def test_missing_cassette_under_none_raises(tmp_path: Path) -> None:
    with pytest.raises(CassetteError, match="recording is forbidden"):
        with use_cassette(tmp_path / "absent.mcp.json", mode="none") as session:
            session.server_command(["python", "-m", "server"])
