"""The upgrade tripwire: fail CI the day a server you don't own changes its tools.

``mcp-cassette check`` re-launches the real server, reads its tool list, and compares it
with the committed cassette. In CI it is one step per server::

    mcp-cassette check tests/cassettes/fetch.mcp.json -- uvx mcp-server-fetch

Exit ``0`` means unchanged, ``5`` means the tools changed (the diff is printed), and
``7`` means the server did not start at all. Write the first baseline with ``--record``,
review it, and commit it.

Here ``echo_server.py`` plays the third-party server. ``tools.mcp.json`` is its
committed baseline, and ``tools-v2.mcp.json`` is a baseline from before a release that
changed the ``echo`` tool, so checking against it shows what a red build looks like.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CASSETTES = HERE / "cassettes"
ECHO_SERVER = [sys.executable, str(HERE / "echo_server.py")]


def _check(cassette: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_cassette", "check", str(cassette), "--"]
        + ECHO_SERVER,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_tripwire_passes_while_the_server_is_unchanged() -> None:
    result = _check(CASSETTES / "tools.mcp.json")

    assert result.returncode == 0, result.stderr


def test_tripwire_fails_when_the_tools_moved() -> None:
    result = _check(CASSETTES / "tools-v2.mcp.json")

    assert result.returncode == 5
    assert "echo: description changed" in result.stdout
