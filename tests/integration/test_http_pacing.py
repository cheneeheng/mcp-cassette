"""HTTP replay pacing integration (ITER_02_v3 §04): SSE event spacing.

Inter-event spacing is the highest-fidelity thing pacing buys — an agent consuming a
progress stream sees it arrive as it originally did.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from scripted_http_client import ACCEPT_BOTH, in_process_server

from mcp_cassette.cassette import Cassette, Message, PaceConfig
from mcp_cassette.transports.http.server import HttpReplayServer
from mcp_cassette.transports.http.wire import SseParser

GAP_MS = 400

INIT_REQUEST: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
}
CALL_REQUEST: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "echo", "arguments": {"text": "hi"}},
}


def _message(
    seq: int,
    t: int,
    sender: str,
    kind: str,
    payload: dict[str, Any],
    exchange: int,
    channel: str | None,
    msg_id: Any = None,
) -> Message:
    return Message(
        seq=seq,
        t_offset_ms=t,
        sender=sender,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        method=payload.get("method"),
        msg_id=msg_id,
        payload=payload,
        exchange=exchange,
        channel=channel,  # type: ignore[arg-type]
    )


def _cassette(path: Path) -> Cassette:
    messages = [
        _message(0, 0, "client", "request", INIT_REQUEST, 0, None, 1),
        _message(
            1,
            10,
            "server",
            "response",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "paced-http", "version": "1.0"},
                },
            },
            0,
            "post",
            1,
        ),
        _message(2, 20, "client", "request", CALL_REQUEST, 1, None, 2),
        _message(
            3,
            20 + GAP_MS,
            "server",
            "notification",
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"progress": 1},
            },
            1,
            "post",
        ),
        _message(
            4,
            20 + 2 * GAP_MS,
            "server",
            "response",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "hi"}]},
            },
            1,
            "post",
            2,
        ),
    ]
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 20, tzinfo=UTC), messages=messages
    ).model_copy(update={"transport": "http", "server_url": "http://127.0.0.1:9/mcp"})
    cassette.save(path)
    return cassette


def _event_times(url: str) -> list[float]:
    """POST the call and timestamp each SSE event, relative to the request start."""
    with httpx.Client(timeout=httpx.Timeout(30.0, read=30.0)) as client:
        init = client.post(
            url,
            json=INIT_REQUEST,
            headers={"content-type": "application/json", "accept": ACCEPT_BOTH},
        )
        session_id = init.headers["mcp-session-id"]
        headers = {
            "content-type": "application/json",
            "accept": ACCEPT_BOTH,
            "mcp-session-id": session_id,
        }
        parser = SseParser()
        times: list[float] = []
        start = time.monotonic()
        with client.stream("POST", url, json=CALL_REQUEST, headers=headers) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            for chunk in response.iter_raw():
                for _event in parser.feed(chunk):
                    times.append(time.monotonic() - start)
        return times


def test_sse_events_keep_their_recorded_spacing(tmp_path: Path) -> None:
    cassette = _cassette(tmp_path / "http.mcp.json")
    server = HttpReplayServer(
        cassette, pace=PaceConfig(mode="recorded"), report_path=None
    )
    with in_process_server(server.serve) as url:
        times = _event_times(url)
    assert len(times) == 2
    # Tight floor, loose ceiling: the sleeps must happen, but a loaded runner
    # must not flake the suite.
    assert times[0] >= 0.35
    assert times[1] - times[0] >= 0.35
    assert times[1] < 20.0


def test_unpaced_replay_is_instant(tmp_path: Path) -> None:
    cassette = _cassette(tmp_path / "http.mcp.json")
    server = HttpReplayServer(cassette, report_path=None)
    with in_process_server(server.serve) as url:
        times = _event_times(url)
    assert len(times) == 2
    assert times[1] < 0.3
