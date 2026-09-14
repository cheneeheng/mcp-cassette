"""Runnable async library-mode example: record once, replay thereafter.

The fourth front door, `use_cassette_async`, over Streamable HTTP: the replay server
runs as a task in this script's own event loop, with no background portal thread.
The first run starts the echo HTTP server in this directory to record against; every
later run replays offline and the real URL is never contacted.

Needs the ``[http]`` extra (``httpx`` + ``h11``). Run it twice::

    uv run python examples/library_mode_async.py     # first run records
    uv run python examples/library_mode_async.py     # every run after replays offline
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from mcp_client import initialize, tool_call
from mcp_http_client import run

from mcp_cassette import use_cassette_async

HERE = Path(__file__).parent
CASSETTE = HERE / "library_mode_async.mcp.json"


def start_echo_server() -> tuple[subprocess.Popen[bytes], str]:
    """Launch the echo HTTP server on a free port and wait until it accepts."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = HERE / "echo_http_server.py"
    proc = subprocess.Popen([sys.executable, str(server), "--port", str(port)])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return proc, f"http://127.0.0.1:{port}/mcp"
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise TimeoutError("echo_http_server never came up")


async def main(real_url: str) -> list[dict[str, Any]]:
    """Record or replay one echo session through the async door."""
    async with use_cassette_async(CASSETTE, mode="once") as session:
        url = session.server_url(real_url)
        messages = [*initialize(), tool_call(2, "echo", {"text": "hello from async"})]
        # The scripted client is synchronous; a real async agent would await here.
        return await anyio.to_thread.run_sync(run, url, messages)


if __name__ == "__main__":
    print("cassette:", CASSETTE, "(exists)" if CASSETTE.exists() else "(will record)")
    proc = None
    real_url = "http://127.0.0.1:9/mcp"  # never contacted on replay
    if not CASSETTE.exists():
        proc, real_url = start_echo_server()
    try:
        objects = anyio.run(main, real_url)
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)
    for obj in objects:
        if obj.get("id") == 2 and "method" not in obj:
            print("echo result:", obj["result"]["content"][0]["text"])
