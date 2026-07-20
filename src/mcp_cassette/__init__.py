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
    RedactionRule,
    ServerInfo,
    UnsupportedFormatVersion,
)
from .lint import LintFinding, LintReport
from .record.proxy import StdioRecordingProxy
from .replay.server import ReplayServer
from .session import CassetteSession

__version__ = "0.2.2"

__all__ = [
    "Cassette",
    "CassetteSession",
    "Fault",
    "FaultOverlay",
    "FaultTarget",
    "LintFinding",
    "LintReport",
    "MatchConfig",
    "Message",
    "RedactionRule",
    "ReplayServer",
    "ServerInfo",
    "StdioRecordingProxy",
    "UnsupportedFormatVersion",
    "__version__",
]
