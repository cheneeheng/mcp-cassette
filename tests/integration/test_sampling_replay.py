"""Server-initiated request replay tests (ITER_03_v2 §04), both transports.

Scripted-agent matrix: the agent answers normally (the gated result arrives, with
content from the recording, not the live answer), answers with an error (the gate
still releases), or never answers (other methods stay answerable and the shutdown
summary names the pending request). Emission carries the recorded ``msg_id``; over
HTTP the recorded channel is honored (POST-stream requests emit there, GET-recorded
emit on the GET stream). A v1-era (format 1) sampling cassette loads and replays.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)
from scripted_http_client import (
    ACCEPT_BOTH,
    free_port,
    in_process_server,
    run_http_session,
    start_reference_http_server,
)

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.transports.http.proxy import RecordingProxy
from mcp_cassette.transports.http.server import HttpReplayServer
from mcp_cassette.transports.http.wire import SseParser

RECORDED_SUMMARY = "recorded summary"
RECORDED_ANSWER = "navy"
SUMMARIZE_ARGS = {"text": "the quick brown fox"}
ASK_ARGS = {"question": "favorite color?"}


def _sampling_result(text: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": {"type": "text", "text": text},
        "model": "scripted-model",
        "stopReason": "endTurn",
    }


def _record_responder(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("method") == "sampling/createMessage":
        return {
            "jsonrpc": "2.0",
            "id": obj["id"],
            "result": _sampling_result(RECORDED_SUMMARY),
        }
    if obj.get("method") == "elicitation/create":
        return {
            "jsonrpc": "2.0",
            "id": obj["id"],
            "result": {"action": "accept", "content": {"answer": RECORDED_ANSWER}},
        }
    return None


def _live_responder(obj: dict[str, Any]) -> dict[str, Any] | None:
    # Deliberately different content from the recording: accept-anything means the
    # replayed results still come from the cassette, whatever the agent answers.
    if obj.get("method") == "sampling/createMessage":
        return {
            "jsonrpc": "2.0",
            "id": obj["id"],
            "result": _sampling_result("a different live answer"),
        }
    if obj.get("method") == "elicitation/create":
        return {
            "jsonrpc": "2.0",
            "id": obj["id"],
            "result": {"action": "accept", "content": {"answer": "chartreuse"}},
        }
    return None


def _error_responder(obj: dict[str, Any]) -> dict[str, Any] | None:
    return {
        "jsonrpc": "2.0",
        "id": obj["id"],
        "error": {"code": -32601, "message": "this agent cannot sample"},
    }


def _init_with_capabilities() -> list[dict[str, Any]]:
    seq = initialize_sequence()
    seq[0]["params"]["capabilities"] = {  # type: ignore[index]
        "sampling": {},
        "elicitation": {},
    }
    return seq


def _recorded_server_requests(cassette: Cassette) -> list[Message]:
    return [
        m for m in cassette.messages if m.sender == "server" and m.kind == "request"
    ]


# --- stdio ---------------------------------------------------------------------------


def _serve_cmd(cassette: Path) -> list[str]:
    return [sys.executable, "-m", "mcp_cassette", "serve", str(cassette)]


@pytest.fixture(scope="module")
def stdio_sampling(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Record echo + summarize + ask_user through the stdio recording proxy once."""
    path = tmp_path_factory.mktemp("cassettes") / "stdio-sampling.json"
    cmd = [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(path),
        "--",
        *reference_server_cmd(),
    ]
    messages = [
        *_init_with_capabilities(),
        tool_call(2, "echo", {"text": "hi"}),
        tool_call(3, "summarize", SUMMARIZE_ARGS),
        tool_call(4, "ask_user", ASK_ARGS),
    ]
    result = run_session(cmd, messages, responder=_record_responder, sequential=True)
    resp = result.response_for(3)
    assert resp is not None
    assert resp["result"]["content"][0]["text"] == f"summary: {RECORDED_SUMMARY}"
    return path


def test_stdio_agent_answers_normally(stdio_sampling: Path) -> None:
    server_requests = _recorded_server_requests(Cassette.load(stdio_sampling))
    recorded_id = next(
        m.msg_id for m in server_requests if m.method == "sampling/createMessage"
    )
    result = run_session(
        _serve_cmd(stdio_sampling),
        [
            *initialize_sequence(),
            tool_call(2, "summarize", SUMMARIZE_ARGS),
            tool_call(3, "ask_user", ASK_ARGS),
        ],
        responder=_live_responder,
    )
    assert result.returncode == 0
    resp = result.response_for(2)
    assert resp is not None
    # the gated result arrives, and its content is the recording's, not the live answer
    assert resp["result"]["content"][0]["text"] == f"summary: {RECORDED_SUMMARY}"
    ask = result.response_for(3)
    assert ask is not None
    assert ask["result"]["content"][0]["text"] == f"user said: {RECORDED_ANSWER}"
    emitted = [
        m for m in result.messages if m.get("method") == "sampling/createMessage"
    ]
    assert emitted and emitted[0]["id"] == recorded_id  # recorded msg_id, verbatim
    assert "still awaiting" not in result.stderr


def test_stdio_error_response_still_releases(stdio_sampling: Path) -> None:
    result = run_session(
        _serve_cmd(stdio_sampling),
        [*initialize_sequence(), tool_call(2, "summarize", SUMMARIZE_ARGS)],
        responder=_error_responder,
    )
    assert result.returncode == 0
    resp = result.response_for(2)
    assert resp is not None
    assert resp["result"]["content"][0]["text"] == f"summary: {RECORDED_SUMMARY}"
    assert "still awaiting" not in result.stderr


def test_stdio_unanswered_keeps_others_answerable(stdio_sampling: Path) -> None:
    result = run_session(
        _serve_cmd(stdio_sampling),
        [
            *initialize_sequence(),
            tool_call(2, "summarize", SUMMARIZE_ARGS),
            tool_call(3, "echo", {"text": "hi"}),
        ],
        expected_responses=2,  # summarize's gated response never releases
        responder=lambda obj: None,
    )
    assert result.returncode == 0
    echo = result.response_for(3)
    assert echo is not None
    assert echo["result"]["content"][0]["text"] == "hi"
    assert result.response_for(2) is None
    assert "sampling/createMessage" in result.stderr
    assert "still awaiting" in result.stderr


def test_v1_format_sampling_cassette_loads_and_replays(tmp_path: Path) -> None:
    # v1's ReplayServer refused such cassettes at load; they now replay unchanged.
    path = tmp_path / "v1-sampling.json"
    data = {
        "format_version": 1,
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "transport": "stdio",
        "protocol_version": "2024-11-05",
        "messages": [
            {
                "seq": 0,
                "t_offset_ms": 0,
                "sender": "client",
                "kind": "request",
                "method": "initialize",
                "msg_id": 1,
                "payload": initialize_sequence()[0],
            },
            {
                "seq": 1,
                "t_offset_ms": 1,
                "sender": "server",
                "kind": "response",
                "msg_id": 1,
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "v1-server", "version": "1.0"},
                    },
                },
            },
            {
                "seq": 2,
                "t_offset_ms": 2,
                "sender": "server",
                "kind": "request",
                "method": "sampling/createMessage",
                "msg_id": 99,
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "sampling/createMessage",
                    "params": {"messages": [], "maxTokens": 8},
                },
            },
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    result = run_session(
        _serve_cmd(path),
        [*initialize_sequence()],
        expected_responses=2,  # keep reading past the init response; settle ends it
        settle=2.0,
        responder=_live_responder,
    )
    assert result.returncode == 0
    emitted = [
        m for m in result.messages if m.get("method") == "sampling/createMessage"
    ]
    assert emitted and emitted[0]["id"] == 99  # recorded id, verbatim
    assert "still awaiting" not in result.stderr


# --- Streamable HTTP -----------------------------------------------------------------


@pytest.fixture(scope="module")
def http_sampling(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Record summarize + ask_user + echo over HTTP; the server is then STOPPED."""
    port = free_port()
    proc = start_reference_http_server(port)
    path = tmp_path_factory.mktemp("cassettes") / "http-sampling.json"
    try:
        proxy = RecordingProxy(
            server_url=f"http://127.0.0.1:{port}/mcp", cassette_path=str(path)
        )
        with in_process_server(proxy.serve) as url:
            result = run_http_session(
                url,
                [
                    *_init_with_capabilities(),
                    tool_call(2, "summarize", SUMMARIZE_ARGS),
                    tool_call(3, "ask_user", ASK_ARGS),
                    tool_call(4, "echo", {"text": "hi"}),
                ],
                responder=_record_responder,
            )
        resp = result.response_for(2)
        assert resp is not None
        assert resp["result"]["content"][0]["text"] == f"summary: {RECORDED_SUMMARY}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return path


def test_http_agent_answers_normally_on_post_channel(http_sampling: Path) -> None:
    cassette = Cassette.load(http_sampling)
    server_requests = _recorded_server_requests(cassette)
    assert server_requests and all(m.channel == "post" for m in server_requests)
    recorded_id = next(
        m.msg_id for m in server_requests if m.method == "sampling/createMessage"
    )
    server = HttpReplayServer(cassette)
    with in_process_server(server.serve) as url:
        result = run_http_session(
            url,
            [
                *initialize_sequence(),
                tool_call(2, "summarize", SUMMARIZE_ARGS),
                tool_call(3, "ask_user", ASK_ARGS),
            ],
            open_get=True,
            responder=_live_responder,
        )
    resp = result.response_for(2)
    assert resp is not None
    assert resp["result"]["content"][0]["text"] == f"summary: {RECORDED_SUMMARY}"
    ask = result.response_for(3)
    assert ask is not None
    assert ask["result"]["content"][0]["text"] == f"user said: {RECORDED_ANSWER}"
    emitted = [
        m for m in result.messages if m.get("method") == "sampling/createMessage"
    ]
    assert emitted and emitted[0]["id"] == recorded_id  # recorded msg_id, verbatim
    # channel fidelity: POST-recorded requests emit on the POST stream, never the GET
    assert not result.get_messages
    assert server.misses == []


def test_http_error_response_still_releases(http_sampling: Path) -> None:
    server = HttpReplayServer(Cassette.load(http_sampling))
    with in_process_server(server.serve) as url:
        result = run_http_session(
            url,
            [*initialize_sequence(), tool_call(2, "summarize", SUMMARIZE_ARGS)],
            responder=_error_responder,
        )
    resp = result.response_for(2)
    assert resp is not None
    assert resp["result"]["content"][0]["text"] == f"summary: {RECORDED_SUMMARY}"


def test_http_unanswered_keeps_others_answerable(
    http_sampling: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    server = HttpReplayServer(Cassette.load(http_sampling))
    got_request = threading.Event()

    with httpx.Client(timeout=10) as client:
        with in_process_server(server.serve) as url:
            init = client.post(
                url, json=initialize_sequence()[0], headers={"accept": ACCEPT_BOTH}
            )
            headers = {
                "accept": ACCEPT_BOTH,
                "mcp-session-id": init.headers["mcp-session-id"],
            }
            client.post(url, json=initialize_sequence()[1], headers=headers)

            def hung_summarize() -> None:
                try:
                    with client.stream(
                        "POST",
                        url,
                        content=json.dumps(tool_call(2, "summarize", SUMMARIZE_ARGS)),
                        headers={**headers, "content-type": "application/json"},
                    ) as response:
                        parser = SseParser()
                        for chunk in response.iter_bytes():
                            for event in parser.feed(chunk):
                                obj = json.loads(event.data)
                                if obj.get("method") == "sampling/createMessage":
                                    got_request.set()
                except (httpx.HTTPError, RuntimeError):
                    pass  # torn down when the server shuts down

            thread = threading.Thread(target=hung_summarize, daemon=True)
            thread.start()
            assert got_request.wait(timeout=10)  # emitted, deliberately unanswered

            # the gate is scoped to the summarize exchange: a hung sampling
            # request leaves every other method answerable
            echo = client.post(
                url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
            )
            assert echo.json()["result"]["content"][0]["text"] == "hi"
        thread.join(timeout=10)
    err = capfd.readouterr().err
    assert "sampling/createMessage" in err
    assert "still awaiting" in err


def test_http_get_recorded_request_emits_on_get_stream(tmp_path: Path) -> None:
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 18, tzinfo=UTC),
        transport="http",
        messages=[
            Message(
                seq=0,
                t_offset_ms=0,
                sender="client",
                kind="request",
                method="initialize",
                msg_id=1,
                payload=initialize_sequence()[0],
                exchange=0,
            ),
            Message(
                seq=1,
                t_offset_ms=1,
                sender="server",
                kind="response",
                msg_id=1,
                payload={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "hand-built", "version": "1.0"},
                    },
                },
                exchange=0,
                channel="post",
            ),
            Message(
                seq=2,
                t_offset_ms=2,
                sender="server",
                kind="request",
                method="sampling/createMessage",
                msg_id=77,
                payload={
                    "jsonrpc": "2.0",
                    "id": 77,
                    "method": "sampling/createMessage",
                    "params": {"messages": [], "maxTokens": 8},
                },
                exchange=1,
                channel="get",
            ),
        ],
    )
    server = HttpReplayServer(cassette)
    with in_process_server(server.serve) as url:
        result = run_http_session(
            url,
            [*initialize_sequence()],
            open_get=True,
            expected_get=1,
            responder=_live_responder,
        )
    # channel fidelity: GET-recorded requests emit on the GET listening stream
    assert result.get_messages
    assert result.get_messages[0]["id"] == 77
