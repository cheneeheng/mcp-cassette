"""pytest plugin: the ``mcp_cassette`` fixture, marker, and ini options.

Registered via the ``pytest11`` entry point. Importing this module without pytest
installed is guarded so the core library never hard-depends on pytest.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cassette import MatchConfig, PaceConfig
from .session import CassetteSession, Mode, _validate_mode, resolve_mode

try:
    import pytest
except ImportError:  # pragma: no cover - pytest is a test-only extra
    pytest = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.config.argparsing import Parser
    from _pytest.fixtures import FixtureRequest

_SANITIZE = re.compile(r"[^A-Za-z0-9_.-]+")


def pytest_addoption(parser: Parser) -> None:
    """Register ini options for cassette mode and directory."""
    parser.addini(
        "mcp_cassette_mode",
        help="Default record mode: once|none|all|new_episodes.",
        default="once",
    )
    parser.addini(
        "mcp_cassette_dir",
        help="Base directory for cassettes (default: tests/cassettes).",
        default="",
    )


def pytest_configure(config: Config) -> None:
    """Register the ``mcp_cassette`` marker."""
    config.addinivalue_line(
        "markers",
        "mcp_cassette(mode=, cassette=, ordering=, ignore_params=, "
        "rewrite_protocol_version=, pace=, pace_scale=, pace_cap_ms=): "
        "configure the mcp_cassette fixture.",
    )


def _resolve_mode(marker_kwargs: dict[str, Any], config: Config) -> Mode:
    # Precedence: env var > marker > ini > "once". Env is read here, at fixture setup
    # time, and nothing is cached module-level, so monkeypatch-set env behaves. What
    # counts as a valid mode is delegated to session.resolve_mode so the fixture and
    # the library door cannot drift; only the tier names are local.
    if os.environ.get("MCP_CASSETTE_MODE"):
        return resolve_mode()
    if "mode" in marker_kwargs:
        return _validate_mode(marker_kwargs["mode"], "marker mode=")
    return _validate_mode(
        str(config.getini("mcp_cassette_mode")) or "once", "ini mcp_cassette_mode"
    )


def _cassette_path(request: FixtureRequest, marker_kwargs: dict[str, Any]) -> Path:
    if "cassette" in marker_kwargs:
        return Path(marker_kwargs["cassette"])
    base_ini = str(request.config.getini("mcp_cassette_dir"))
    root = Path(request.config.rootpath)
    base = Path(base_ini) if base_ini else root / "tests" / "cassettes"
    module = Path(str(request.node.fspath)).stem
    node_name = _SANITIZE.sub("_", request.node.name)
    return base / module / f"{node_name}.mcp.json"


def _match_config(marker_kwargs: dict[str, Any]) -> MatchConfig:
    return MatchConfig(
        ignore_params=list(marker_kwargs.get("ignore_params", [])),
        ordering=marker_kwargs.get("ordering", "per_method"),
        rewrite_protocol_version=bool(
            marker_kwargs.get("rewrite_protocol_version", False)
        ),
    )


def _pace_config(marker_kwargs: dict[str, Any]) -> PaceConfig | None:
    if "pace" not in marker_kwargs:
        return None
    return PaceConfig(
        mode=marker_kwargs["pace"],
        scale=float(marker_kwargs.get("pace_scale", 1.0)),
        cap_ms=int(marker_kwargs.get("pace_cap_ms", 5000)),
    )


if pytest is not None:  # pragma: no branch — pytest is always present in the test env

    @pytest.fixture
    def mcp_cassette(request: FixtureRequest, tmp_path: Path) -> Any:
        """Provide a :class:`CassetteSession` for the test, finalized on teardown.

        First run records through the proxy; every run after replays offline. On
        teardown the session report is checked and the test fails on an empty recording
        or any replay miss.
        """
        marker = request.node.get_closest_marker("mcp_cassette")
        marker_kwargs: dict[str, Any] = dict(marker.kwargs) if marker else {}
        mode = _resolve_mode(marker_kwargs, request.config)
        cassette_path = _cassette_path(request, marker_kwargs)
        report_path = tmp_path / "mcp_cassette_report.json"
        session = CassetteSession(
            mode=mode,
            cassette_path=cassette_path,
            match=_match_config(marker_kwargs),
            pace=_pace_config(marker_kwargs),
            report_path=report_path,
        )
        yield session
        session.finalize()
