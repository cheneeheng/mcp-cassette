"""The Streamable HTTP (v2) example: record a "remote" server once, replay offline.

``mcp_cassette.server_url(real_url)`` is the HTTP analog of ``server_command`` — the
fixture hands back a local URL to plug into the agent's MCP config. First run it is a
recording proxy in front of the real URL; afterwards it is a local mock server rebuilt
from the cassette, and the real URL is never contacted (here it does not even exist).

Needs the ``[http]`` extra (``httpx`` + ``h11``); the repo's dev group includes it.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp_client import initialize, tool_call
from mcp_http_client import run

import mcp_cassette as mcc

pytest.importorskip("h11", reason="the HTTP examples need mcp-cassette[http]")
pytest.importorskip("httpx", reason="the HTTP examples need mcp-cassette[http]")

HERE = Path(__file__).parent
CASSETTE = HERE / "cassettes" / "http_echo_and_add.mcp.json"
HTTP_SERVER = HERE / "echo_http_server.py"


def _wait_for_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"echo_http_server never came up on port {port}")


@pytest.fixture
def real_server_url() -> Iterator[str]:
    """The "remote" MCP endpoint — live only when there is a recording to make.

    With the cassette present, replay is fully offline, so this hands over a dead
    URL to prove the real server is never contacted.
    """
    if CASSETTE.exists():
        yield "http://127.0.0.1:9/mcp"
        return
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    proc = subprocess.Popen([sys.executable, str(HTTP_SERVER), "--port", str(port)])
    _wait_for_port(port)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.mark.mcp_cassette(cassette=CASSETTE)
def test_http_echo_and_add(
    mcp_cassette: mcc.CassetteSession, real_server_url: str
) -> None:
    """Swap the server URL once; the suite stops hitting the remote server."""
    url = mcp_cassette.server_url(real_server_url)
    objects = run(
        url,
        [
            *initialize(),
            tool_call(2, "echo", {"text": "hello remote"}),
            tool_call(3, "add", {"a": 40, "b": 2}),
        ],
    )

    def response_for(msg_id: int) -> dict[str, Any]:
        for obj in objects:
            if obj.get("id") == msg_id and "method" not in obj:
                return obj
        raise AssertionError(f"no response for id {msg_id}")

    echo = response_for(2)
    assert echo["result"]["content"][0]["text"].startswith("hello remote")
    add = response_for(3)
    assert add["result"]["content"][0]["text"] == "42"
