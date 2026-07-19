"""HTTP recording integration tests (ITER_01_v2 §04) against the reference server."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripted_client import initialize_sequence, tool_call
from scripted_http_client import (
    free_port,
    in_process_server,
    run_http_session,
    start_reference_http_server,
    wait_for_port,
)

from mcp_cassette.cassette import Cassette
from mcp_cassette.cli import main as cli_main
from mcp_cassette.transports.http.proxy import RecordingProxy


@pytest.fixture(scope="module")
def ref_server() -> Iterator[str]:
    port = free_port()
    proc = start_reference_http_server(port)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _record(
    ref_url: str,
    cassette_path: Path,
    messages: list[dict[str, Any]],
    **session_kw: Any,
) -> Any:
    proxy = RecordingProxy(server_url=ref_url, cassette_path=str(cassette_path))
    with in_process_server(proxy.serve) as url:
        result = run_http_session(url, messages, **session_kw)
    return result


def _standard_messages() -> list[dict[str, Any]]:
    return [
        *initialize_sequence(),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call(3, "echo", {"text": "hi"}),
        tool_call(4, "notify", {}),  # answered as SSE with an interleaved notification
        tool_call(5, "broadcast", {}),  # emits a GET-stream notification
    ]


def test_full_session_capture(ref_server: str, tmp_path: Path) -> None:
    cassette_path = tmp_path / "http.json"
    result = _record(
        ref_server,
        cassette_path,
        _standard_messages(),
        open_get=True,
        expected_get=1,
        extra_headers={"authorization": "Bearer planted-credential"},
    )
    assert result.response_for(3)["result"]["content"][0]["text"] == "hi"

    cassette = Cassette.load(cassette_path)
    assert cassette.format_version == 2
    assert cassette.transport == "http"
    assert cassette.server_url == ref_server
    assert cassette.session_id is not None  # captured from Mcp-Session-Id

    # seq strictly increasing
    seqs = [m.seq for m in cassette.messages]
    assert seqs == sorted(set(seqs))

    # client messages carry no channel; server POST answers are channel="post"
    for m in cassette.messages:
        if m.sender == "client":
            assert m.channel is None
            assert m.exchange is not None
        else:
            assert m.channel in ("post", "get")

    # exchange grouping: a request and its response share the exchange number
    req = next(m for m in cassette.messages if m.msg_id == 3 and m.kind == "request")
    resp = next(m for m in cassette.messages if m.msg_id == 3 and m.kind == "response")
    assert req.exchange == resp.exchange

    # the notify call was an SSE exchange: notification + response share it, ordered
    notify_req = next(
        m
        for m in cassette.messages
        if m.kind == "request"
        and isinstance(m.payload, dict)
        and m.payload.get("params", {}).get("name") == "notify"
    )
    same_exchange = [
        m
        for m in cassette.messages
        if m.exchange == notify_req.exchange and m.sender == "server"
    ]
    assert len(same_exchange) >= 2
    kinds = [m.kind for m in same_exchange]
    assert kinds.index("notification") < kinds.index("response")

    # GET stream capture: the broadcast notification arrived with channel="get"
    get_messages = [m for m in cassette.messages if m.channel == "get"]
    assert any(m.method == "notifications/tools/list_changed" for m in get_messages)

    # header never-persist: the planted Authorization value appears nowhere
    raw = cassette_path.read_text(encoding="utf-8")
    assert "planted-credential" not in raw
    assert "authorization" not in raw.lower()


def test_json_response_mode_round_trips(tmp_path: Path) -> None:
    port = free_port()
    proc = start_reference_http_server(port, json_response=True)
    try:
        cassette_path = tmp_path / "json-mode.json"
        result = _record(
            f"http://127.0.0.1:{port}/mcp",
            cassette_path,
            [*initialize_sequence(), tool_call(2, "add", {"a": 2, "b": 3})],
        )
        assert result.response_for(2)["result"]["content"][0]["text"] == "5"
        cassette = Cassette.load(cassette_path)
        assert any(m.kind == "response" and m.msg_id == 2 for m in cassette.messages)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_concurrent_posts_keep_seq_strictly_increasing(
    ref_server: str, tmp_path: Path
) -> None:
    cassette_path = tmp_path / "concurrent.json"
    proxy = RecordingProxy(server_url=ref_server, cassette_path=str(cassette_path))
    with in_process_server(proxy.serve) as url:
        run_http_session(url, initialize_sequence())
        import httpx

        session_id = None
        with httpx.Client(timeout=30) as probe:
            # Re-initialize on a raw client to get a session id for parallel posts.
            r = probe.post(
                url,
                json=initialize_sequence()[0],
                headers={"accept": "application/json, text/event-stream"},
            )
            session_id = r.headers.get("mcp-session-id")
            probe.post(
                url,
                json=initialize_sequence()[1],
                headers={
                    "accept": "application/json, text/event-stream",
                    "mcp-session-id": session_id or "",
                },
            )

            def call(msg_id: int) -> None:
                probe.post(
                    url,
                    json=tool_call(msg_id, "counter", {}),
                    headers={
                        "accept": "application/json, text/event-stream",
                        "mcp-session-id": session_id or "",
                    },
                )

            threads = [threading.Thread(target=call, args=(i,)) for i in (10, 11, 12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
    cassette = Cassette.load(cassette_path)
    seqs = [m.seq for m in cassette.messages]
    assert seqs == sorted(set(seqs))
    counter_requests = [
        m
        for m in cassette.messages
        if m.kind == "request"
        and isinstance(m.payload, dict)
        and m.payload.get("params", {}).get("name") == "counter"
    ]
    assert len({m.exchange for m in counter_requests}) == 3  # exchanges interleave


def test_first_contact_failure_creates_no_cassette(tmp_path: Path) -> None:
    dead_url = f"http://127.0.0.1:{free_port()}/mcp"
    cassette_path = tmp_path / "never.json"
    proxy = RecordingProxy(server_url=dead_url, cassette_path=str(cassette_path))
    with in_process_server(proxy.serve) as url:
        result = run_http_session(url, initialize_sequence()[:1])
    assert 502 in result.statuses
    assert not cassette_path.exists()
    assert proxy.fatal_error is not None
    assert dead_url in proxy.fatal_error


def test_inspect_reports_http_cassette(
    ref_server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cassette_path = tmp_path / "inspect.json"
    _record(
        ref_server,
        cassette_path,
        [*initialize_sequence(), tool_call(2, "echo", {"text": "x"})],
    )
    rc = cli_main(["inspect", str(cassette_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "transport: http" in out
    assert "server host: 127.0.0.1" in out
    assert "exchanges: " in out


def test_cli_record_url_with_max_idle(ref_server: str, tmp_path: Path) -> None:
    cassette_path = tmp_path / "cli.json"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mcp_cassette",
            "record",
            "--cassette",
            str(cassette_path),
            "--url",
            ref_server,
            "--max-idle",
            "2.0",
        ],
        stderr=subprocess.PIPE,
    )
    assert proc.stderr is not None
    banner = proc.stderr.readline().decode("utf-8", errors="replace")
    assert "recording at http://127.0.0.1:" in banner
    url = banner.split("recording at ", 1)[1].split(" ")[0].strip()
    wait_for_port(int(url.split(":")[2].split("/")[0]))
    result = run_http_session(
        url, [*initialize_sequence(), tool_call(2, "echo", {"text": "cli"})]
    )
    assert result.response_for(2) is not None
    rc = proc.wait(timeout=30)
    assert rc == 0  # --max-idle shutdown is a clean exit
    cassette = Cassette.load(cassette_path)
    assert cassette.transport == "http"
    assert any(m.msg_id == 2 and m.kind == "response" for m in cassette.messages)


def test_cli_record_url_and_cmd_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_main(
        [
            "record",
            "--cassette",
            str(tmp_path / "c.json"),
            "--url",
            "http://127.0.0.1:9/mcp",
            "--",
            "server-cmd",
        ]
    )
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_raw_sse_payload_recorded_as_raw(tmp_path: Path) -> None:
    # A non-JSON SSE data payload must be captured as kind="raw", not dropped.
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(tmp_path / "r.json")
    )
    proxy._recorder.on_message("server", "not json", exchange=0, channel="post")
    proxy.finalize()
    cassette = Cassette.load(tmp_path / "r.json")
    assert cassette.messages[0].kind == "raw"
    assert cassette.messages[0].channel == "post"
    assert json.loads(cassette.model_dump_json())  # still a valid cassette
