"""Runnable library-mode example: record once, replay thereafter.

No pytest involved — this is the third front door, `use_cassette`, driving the echo
server in this directory through a scripted transport-level client.

Run it twice::

    uv run python examples/library_mode.py     # first run records
    uv run python examples/library_mode.py     # every run after replays offline
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp_cassette import use_cassette

HERE = Path(__file__).parent
CASSETTE = HERE / "library_mode.mcp.json"

REQUESTS: list[dict[str, Any]] = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"text": "hello from the library"}},
    },
]


def drive(command: list[str]) -> list[dict[str, Any]]:
    """Run the MCP server command and speak newline-delimited JSON-RPC to it."""
    payload = "".join(json.dumps(r) + "\n" for r in REQUESTS)
    completed = subprocess.run(
        command, input=payload.encode("utf-8"), capture_output=True, timeout=60
    )
    return [
        json.loads(line)
        for line in completed.stdout.decode("utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    """Record or replay one echo session and print the tool result."""
    print("cassette:", CASSETTE, "(exists)" if CASSETTE.exists() else "(will record)")
    with use_cassette(CASSETTE, mode="once") as session:
        command = session.server_command([sys.executable, str(HERE / "echo_server.py")])
        messages = drive(command)
    for message in messages:
        if message.get("id") == 2:
            print("echo result:", message["result"]["content"][0]["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
