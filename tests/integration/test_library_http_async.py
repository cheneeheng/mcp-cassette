"""Async library door over Streamable HTTP (ITER_05_v4 §04): no portal thread."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
import pytest
from scripted_client import initialize_sequence, tool_call
from scripted_http_client import ACCEPT_BOTH, free_port, start_reference_http_server

from mcp_cassette import use_cassette_async


def _messages() -> list[dict[str, Any]]:
    return [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]


def _is_closed(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return False
    except OSError:
        return True


def _non_daemon_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if not t.daemon}


async def _run_session(url: str) -> dict[Any, dict[str, Any]]:
    """Speak the scripted session from inside the loop, so the test adds no thread."""
    responses: dict[Any, dict[str, Any]] = {}
    session_id: str | None = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for message in _messages():
            headers = {"content-type": "application/json", "accept": ACCEPT_BOTH}
            if session_id is not None:
                headers["mcp-session-id"] = session_id
            response = await client.post(
                url, content=json.dumps(message), headers=headers
            )
            session_id = response.headers.get("mcp-session-id", session_id)
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                bodies = [
                    line[len("data:") :].strip()
                    for line in response.text.splitlines()
                    if line.startswith("data:")
                ]
            else:
                bodies = [response.text] if response.text.strip() else []
            for body in bodies:
                obj = json.loads(body)
                if "id" in obj and "method" not in obj:
                    responses[obj["id"]] = obj
    return responses


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_record_then_replay_in_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "http.mcp.json"
    port = free_port()
    real_url = f"http://127.0.0.1:{port}/mcp"

    async def block(mode: str) -> tuple[dict[Any, dict[str, Any]], int, set[Any]]:
        before = _non_daemon_threads()
        async with use_cassette_async(cassette, mode=mode) as session:
            url = session.server_url(real_url)
            # A second call returns the bound server rather than starting another.
            assert session.server_url(real_url) == url
            responses = await _run_session(url)
            created = _non_daemon_threads() - before
        return responses, int(urlsplit(url).port or 0), created

    proc = start_reference_http_server(port)
    try:
        recorded, _, record_threads = anyio.run(block, "once", backend=backend)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert recorded[2]["result"]
    assert cassette.exists()

    # The real server is stopped: replay must be fully offline.
    replayed, replay_port, replay_threads = anyio.run(block, "none", backend=backend)
    assert replayed[2] == recorded[2]
    # The observable difference from the sync door, which runs a portal thread.
    assert record_threads == set()
    assert replay_threads == set()
    # The task group really tore the server down.
    assert _is_closed(replay_port)
