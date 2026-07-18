"""Streamable HTTP transport: recording proxy and replay server.

Importing this package requires the ``[http]`` optional extra (httpx + h11); the
core install stays ``anyio`` + ``pydantic`` only.
"""

from __future__ import annotations

try:
    import h11  # noqa: F401 — presence check for the [http] extra
    import httpx  # noqa: F401
except ImportError as exc:  # pragma: no cover — exercised only without the extra
    raise ImportError(
        "the Streamable HTTP transport needs the [http] extra: "
        "pip install 'mcp-cassette[http]'"
    ) from exc

from .proxy import RecordingProxy
from .server import HttpReplayServer

__all__ = ["HttpReplayServer", "RecordingProxy"]
