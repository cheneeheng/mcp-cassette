"""HTTP replay integration tests (ITER_02_v2 §04): deterministic offline mock."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripted_client import initialize_sequence, tool_call
from scripted_http_client import (
    ACCEPT_BOTH,
    free_port,
    in_process_server,
    run_http_session,
    start_reference_http_server,
)

from mcp_cassette.cassette import Cassette, Fault, FaultOverlay
from mcp_cassette.transports.http.proxy import RecordingProxy
from mcp_cassette.transports.http.server import HttpReplayServer


def _standard_messages() -> list[dict[str, Any]]:
    return [
        *initialize_sequence(),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call(3, "echo", {"text": "hi"}),
        tool_call(4, "notify", {}),
        tool_call(5, "broadcast", {}),
        tool_call(6, "counter", {}),
        tool_call(7, "counter", {}),
    ]


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Record the canonical session once; the reference server is then STOPPED."""
    port = free_port()
    proc = start_reference_http_server(port)
    cassette_path = tmp_path_factory.mktemp("cassettes") / "http.json"
    try:
        proxy = RecordingProxy(
            server_url=f"http://127.0.0.1:{port}/mcp",
            cassette_path=str(cassette_path),
        )
        with in_process_server(proxy.serve) as url:
            result = run_http_session(
                url, _standard_messages(), open_get=True, expected_get=1
            )
        assert result.response_for(7) is not None
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return cassette_path


def _replay_server(recorded: Path, **kw: Any) -> HttpReplayServer:
    return HttpReplayServer(Cassette.load(recorded), **kw)


def test_replay_round_trip_semantically_identical(recorded: Path) -> None:
    server = _replay_server(recorded)
    with in_process_server(server.serve) as url:
        result = run_http_session(
            url, _standard_messages(), open_get=True, expected_get=1
        )
    assert result.response_for(3)["result"]["content"][0]["text"] == "hi"
    # the SSE exchange replays with its notification before the response
    notify_index = next(
        i
        for i, m in enumerate(result.messages)
        if m.get("method") == "notifications/message"
    )
    notify_resp_index = next(
        i for i, m in enumerate(result.messages) if m.get("id") == 4
    )
    assert notify_index < notify_resp_index
    # counter queue positions replay in order
    assert result.response_for(6)["result"]["content"][0]["text"] == "1"
    assert result.response_for(7)["result"]["content"][0]["text"] == "2"
    # GET-stream delivery: the broadcast notification arrived on the GET stream
    assert any(
        m.get("method") == "notifications/tools/list_changed"
        for m in result.get_messages
    )
    assert server.misses == []


def test_session_id_issuance_and_404(recorded: Path) -> None:
    server = _replay_server(recorded)
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            init = client.post(
                url,
                json=initialize_sequence()[0],
                headers={"accept": ACCEPT_BOTH},
            )
            sid = init.headers.get("mcp-session-id")
            assert sid is not None
            assert sid.startswith("mcc-")  # fresh deterministic id, never recorded
            assert sid != Cassette.load(recorded).session_id

            # mismatching id -> 404 per the Streamable HTTP spec
            bad = client.post(
                url,
                json=tool_call(3, "echo", {"text": "hi"}),
                headers={"accept": ACCEPT_BOTH, "mcp-session-id": "wrong"},
            )
            assert bad.status_code == 404
            missing = client.post(
                url,
                json=tool_call(3, "echo", {"text": "hi"}),
                headers={"accept": ACCEPT_BOTH},
            )
            assert missing.status_code == 404


def test_session_id_is_deterministic(recorded: Path) -> None:
    first = _replay_server(recorded).session_id
    second = _replay_server(recorded).session_id
    assert first == second


def test_unmatched_request_gets_200_jsonrpc_error(recorded: Path) -> None:
    server = _replay_server(recorded)
    with in_process_server(server.serve) as url:
        result = run_http_session(
            url,
            [*initialize_sequence(), tool_call(9, "no_such_tool", {})],
        )
    resp = result.response_for(9)
    assert resp is not None  # delivered as a 200 body, not a transport error
    assert resp["error"]["code"] == -32001
    assert "no recorded interaction matches" in resp["error"]["message"]
    assert server.misses  # surfaced to the fixture as a failure signal


def test_concurrent_identical_calls_consume_queue_deterministically(
    recorded: Path,
) -> None:
    server = _replay_server(recorded)
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            init = client.post(
                url, json=initialize_sequence()[0], headers={"accept": ACCEPT_BOTH}
            )
            sid = init.headers["mcp-session-id"]
            headers = {"accept": ACCEPT_BOTH, "mcp-session-id": sid}
            client.post(url, json=initialize_sequence()[1], headers=headers)
            results: list[str] = []
            lock = threading.Lock()

            def call(msg_id: int) -> None:
                response = client.post(
                    url, json=tool_call(msg_id, "counter", {}), headers=headers
                )
                body = response.json()
                with lock:
                    results.append(body["result"]["content"][0]["text"])

            threads = [threading.Thread(target=call, args=(i,)) for i in (20, 21)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
    assert sorted(results) == ["1", "2"]  # both queue positions consumed exactly once


# --- fault matrix over HTTP ---------------------------------------------------------


def _faulted(recorded: Path, fault: Fault) -> HttpReplayServer:
    return _replay_server(recorded, faults=FaultOverlay(faults=[fault]))


def _open_session(client: httpx.Client, url: str) -> dict[str, str]:
    init = client.post(
        url, json=initialize_sequence()[0], headers={"accept": ACCEPT_BOTH}
    )
    headers = {
        "accept": ACCEPT_BOTH,
        "mcp-session-id": init.headers["mcp-session-id"],
    }
    client.post(url, json=initialize_sequence()[1], headers=headers)
    return headers


def test_fault_delay_then_normal(recorded: Path) -> None:
    server = _faulted(recorded, Fault.delay("tools/call", 300))
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _open_session(client, url)
            start = time.monotonic()
            response = client.post(
                url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
            )
            elapsed = time.monotonic() - start
    assert response.json()["result"]["content"][0]["text"] == "hi"
    assert elapsed >= 0.25


def test_fault_timeout_hangs_one_call_but_serves_others(recorded: Path) -> None:
    server = _faulted(recorded, Fault.timeout("tools/call", nth=1))
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=httpx.Timeout(10, read=1.0)) as client:
            headers = _open_session(client, url)
            with pytest.raises(httpx.ReadTimeout):
                client.post(
                    url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
                )
            # a second connection keeps serving: a hung tool, not a dead server
            ok = client.post(url, json=tool_call(6, "counter", {}), headers=headers)
            assert ok.json()["result"]["content"][0]["text"] == "1"


def test_fault_error_replaces_response(recorded: Path) -> None:
    server = _faulted(recorded, Fault.error("tools/call", code=-32000, message="boom"))
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _open_session(client, url)
            response = client.post(
                url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
            )
    assert response.status_code == 200
    body = response.json()
    assert body["error"] == {"code": -32000, "message": "boom"}
    assert body["id"] == 3  # same live id


def test_fault_malformed_not_json(recorded: Path) -> None:
    server = _faulted(recorded, Fault.malformed("tools/call", strategy="not_json"))
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _open_session(client, url)
            response = client.post(
                url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
            )
    assert response.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        response.json()


def test_fault_malformed_wrong_id(recorded: Path) -> None:
    server = _faulted(recorded, Fault.malformed("tools/call", strategy="wrong_id"))
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _open_session(client, url)
            response = client.post(
                url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
            )
    assert response.json()["id"] == "mcp-cassette-unknown-id"


def test_fault_malformed_truncate_closes_mid_body(recorded: Path) -> None:
    server = _faulted(recorded, Fault.malformed("tools/call", strategy="truncate"))
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _open_session(client, url)
            with pytest.raises(httpx.HTTPError):
                client.post(
                    url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
                )


def test_fault_disconnect_kills_server_and_get_stream(recorded: Path) -> None:
    server = _faulted(recorded, Fault.disconnect("tools/call"))
    get_closed = threading.Event()
    with in_process_server(server.serve) as url:
        with httpx.Client(timeout=10) as client:
            headers = _open_session(client, url)

            def get_loop() -> None:
                try:
                    with client.stream(
                        "GET",
                        url,
                        headers={
                            "accept": "text/event-stream",
                            "mcp-session-id": headers["mcp-session-id"],
                        },
                    ) as response:
                        for _ in response.iter_bytes():
                            pass
                except httpx.HTTPError:
                    pass
                get_closed.set()

            thread = threading.Thread(target=get_loop, daemon=True)
            thread.start()
            time.sleep(0.2)
            with pytest.raises(httpx.HTTPError):
                client.post(
                    url, json=tool_call(3, "echo", {"text": "hi"}), headers=headers
                )
            assert get_closed.wait(timeout=5)  # server death kills everything


# --- new_episodes over HTTP ---------------------------------------------------------


def test_new_episodes_appends_exactly_the_novel_exchange(tmp_path: Path) -> None:
    port = free_port()
    proc = start_reference_http_server(port)
    real_url = f"http://127.0.0.1:{port}/mcp"
    cassette_path = tmp_path / "ne.json"
    try:
        proxy = RecordingProxy(server_url=real_url, cassette_path=str(cassette_path))
        with in_process_server(proxy.serve) as url:
            run_http_session(
                url, [*initialize_sequence(), tool_call(2, "echo", {"text": "hi"})]
            )
        before = Cassette.load(cassette_path)

        server = HttpReplayServer(
            before,
            fallthrough_url=real_url,
            cassette_path=str(cassette_path),
        )
        with in_process_server(server.serve) as url:
            result = run_http_session(
                url,
                [
                    *initialize_sequence(),
                    tool_call(2, "echo", {"text": "hi"}),  # replays from cassette
                    tool_call(3, "add", {"a": 2, "b": 3}),  # novel -> falls through
                ],
            )
        assert result.response_for(2)["result"]["content"][0]["text"] == "hi"
        assert result.response_for(3)["result"]["content"][0]["text"] == "5"

        after = Cassette.load(cassette_path)
        appended = after.messages[len(before.messages) :]
        # exactly the novel exchange: the add request and its response
        assert len(appended) == 2
        assert appended[0].method == "tools/call"
        assert appended[1].kind == "response"
        assert len({m.exchange for m in appended}) == 1
        assert appended[0].exchange not in {
            m.exchange for m in before.messages if m.exchange is not None
        }
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_transport_mismatch_fails_fast(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    stdio_path = tmp_path / "stdio.json"
    Cassette(recorded_at=datetime(2026, 7, 17, tzinfo=UTC)).save(stdio_path)
    with pytest.raises(ValueError, match="not 'http'"):
        HttpReplayServer(Cassette.load(stdio_path))


# --- CLI exit codes (signal-driven; POSIX only, matching v1 precedent) --------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGINT delivery to a console-less subprocess is not testable on win32",
)
def test_cli_serve_exits_3_on_misses(recorded: Path, tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_cassette", "serve", str(recorded)],
        stderr=subprocess.PIPE,
    )
    assert proc.stderr is not None
    banner = proc.stderr.readline().decode("utf-8", errors="replace")
    url = banner.split("replaying at ", 1)[1].strip()
    run_http_session(url, [*initialize_sequence(), tool_call(9, "nope", {})])
    proc.send_signal(signal.SIGINT)
    rc = proc.wait(timeout=30)
    assert rc == 3
