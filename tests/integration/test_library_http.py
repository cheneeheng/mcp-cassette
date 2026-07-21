"""Library-mode HTTP integration (ITER_01_v3 §04): server_url and teardown."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from scripted_client import initialize_sequence, tool_call
from scripted_http_client import (
    free_port,
    run_http_session,
    start_reference_http_server,
)

from mcp_cassette import use_cassette


def _messages() -> list[dict[str, Any]]:
    return [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]


def _port_of(url: str) -> int:
    return int(urlsplit(url).netloc.rsplit(":", 1)[1])


def _is_closed(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return False
    except OSError:
        return True


def test_record_then_replay_and_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "http.mcp.json"
    port = free_port()
    proc = start_reference_http_server(port)
    real_url = f"http://127.0.0.1:{port}/mcp"
    try:
        with use_cassette(cassette, mode="once") as session:
            url = session.server_url(real_url)
            recorded = run_http_session(url, _messages())
        assert recorded.response_for(2) is not None
        assert cassette.exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # The real server is now stopped: replay must be fully offline.
    with use_cassette(cassette, mode="none") as session:
        url = session.server_url(real_url)
        replay_port = _port_of(url)
        replayed = run_http_session(url, _messages())
    assert replayed.response_for(2) == recorded.response_for(2)
    # close() really tore the portal down — the bound port no longer accepts.
    assert _is_closed(replay_port)
