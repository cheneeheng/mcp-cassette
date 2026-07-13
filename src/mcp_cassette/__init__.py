"""mcp-cassette: record/replay and mocking for MCP agent test suites.

Public API. Operates at the stdio transport level (newline-delimited JSON-RPC) and does
not depend on the official ``mcp`` SDK at runtime.
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
    UnsupportedCassetteFeature,
    UnsupportedFormatVersion,
)
from .record.proxy import StdioRecordingProxy
from .replay.server import ReplayServer
from .session import CassetteSession

__version__ = "0.1.0"

__all__ = [
    "Cassette",
    "CassetteSession",
    "Fault",
    "FaultOverlay",
    "FaultTarget",
    "MatchConfig",
    "Message",
    "RedactionRule",
    "ReplayServer",
    "ServerInfo",
    "StdioRecordingProxy",
    "UnsupportedCassetteFeature",
    "UnsupportedFormatVersion",
    "__version__",
]
