"""mcp-cassette: record/replay and mocking for MCP agent test suites.

Public API. Operates at the transport level — newline-delimited JSON-RPC over stdio,
or Streamable HTTP with the ``[http]`` extra — and does not depend on the official
``mcp`` SDK at runtime.
"""

from __future__ import annotations

from .cassette import (
    Cassette,
    Fault,
    FaultOverlay,
    FaultTarget,
    MatchConfig,
    Message,
    PaceConfig,
    RedactionRule,
    ServerInfo,
    UnsupportedFormatVersion,
)
from .diffing import CassetteDiff, diff_cassettes
from .lint import LintFinding, LintReport, PatternRule, ProjectLintConfig
from .lint import run as lint_cassette
from .record.proxy import StdioRecordingProxy
from .replay.server import ReplayServer
from .session import CassetteError, CassetteSession, Mode, resolve_mode, use_cassette

__version__ = "0.3.3"

__all__ = [
    "Cassette",
    "CassetteDiff",
    "CassetteError",
    "CassetteSession",
    "Fault",
    "FaultOverlay",
    "FaultTarget",
    "LintFinding",
    "LintReport",
    "MatchConfig",
    "Message",
    "Mode",
    "PaceConfig",
    "PatternRule",
    "ProjectLintConfig",
    "RedactionRule",
    "ReplayServer",
    "ServerInfo",
    "StdioRecordingProxy",
    "UnsupportedFormatVersion",
    "__version__",
    "diff_cassettes",
    "lint_cassette",
    "resolve_mode",
    "use_cassette",
]
