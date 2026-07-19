"""In-process unit tests for replay-server edge handling and new_episodes helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import anyio

from mcp_cassette.cassette import Cassette, MatchConfig, Message
from mcp_cassette.matching import Exchange
from mcp_cassette.replay.new_episodes import NewEpisodesProxy, _decode
from mcp_cassette.replay.server import ReplayServer


class _SinkStream:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any] | str] = []

    async def send(self, item: bytes) -> None:
        text = item.decode("utf-8").strip()
        try:
            self.lines.append(json.loads(text))
        except json.JSONDecodeError:
            self.lines.append(text)


def _msg(
    seq: int,
    sender: str,
    kind: str,
    payload: dict[str, Any] | str,
    *,
    method: str | None = None,
    msg_id: int | None = None,
) -> Message:
    return Message(
        seq=seq,
        t_offset_ms=seq,
        sender=sender,  # type: ignore[arg-type] — test helper takes plain str
        kind=kind,  # type: ignore[arg-type] — test helper takes plain str
        method=method,
        msg_id=msg_id,
        payload=payload,
    )


def _init_exchange_messages(result: Any) -> list[Message]:
    return [
        _msg(
            0,
            "client",
            "request",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
            method="initialize",
            msg_id=1,
        ),
        _msg(
            1,
            "server",
            "response",
            {"jsonrpc": "2.0", "id": 1, "result": result},
            msg_id=1,
        ),
    ]


def _cassette(messages: list[Message]) -> Cassette:
    return Cassette(recorded_at=datetime(2026, 7, 5, tzinfo=UTC), messages=messages)


def _drive(server: ReplayServer, lines: list[bytes]) -> _SinkStream:
    sink = _SinkStream()

    async def run() -> None:
        for line in lines:
            await server._handle_line(line, sink)  # type: ignore[arg-type]

    anyio.run(run)
    return sink


INIT_LINE = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    ).encode()
    + b"\n"
)


def test_blank_junk_and_non_object_lines_ignored() -> None:
    server = ReplayServer(_cassette(_init_exchange_messages({})))
    sink = _drive(server, [b"   \n", b"junk not json\n", b"[1, 2]\n"])
    assert sink.lines == []


def test_initialize_without_recorded_exchange_answers_error() -> None:
    # cassette has a non-initialize exchange only
    messages = [
        _msg(
            0,
            "client",
            "request",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
            method="tools/call",
            msg_id=1,
        ),
        _msg(
            1, "server", "response", {"jsonrpc": "2.0", "id": 1, "result": {}}, msg_id=1
        ),
    ]
    server = ReplayServer(_cassette(messages))
    sink = _drive(server, [INIT_LINE])
    (resp,) = sink.lines
    assert isinstance(resp, dict)
    assert resp["id"] == 10
    assert "no recorded initialize response" in resp["error"]["message"]


def test_leading_notifications_emitted_only_once() -> None:
    messages = [
        _msg(
            0,
            "server",
            "notification",
            {"jsonrpc": "2.0", "method": "notifications/ready"},
            method="notifications/ready",
        ),
        *[m.model_copy(update={"seq": m.seq + 1}) for m in _init_exchange_messages({})],
    ]
    server = ReplayServer(_cassette(messages), match=MatchConfig(ordering="none"))
    sink = _drive(server, [INIT_LINE, INIT_LINE])
    ready = [
        line
        for line in sink.lines
        if isinstance(line, dict) and line.get("method") == "notifications/ready"
    ]
    assert len(ready) == 1


def test_initialize_with_non_dict_result_served_verbatim() -> None:
    server = ReplayServer(_cassette(_init_exchange_messages("odd result")))
    sink = _drive(server, [INIT_LINE])
    (resp,) = sink.lines
    assert isinstance(resp, dict)
    assert resp["result"] == "odd result"
    assert resp["id"] == 10


def test_rewrite_without_requested_version_keeps_recorded() -> None:
    server = ReplayServer(
        _cassette(_init_exchange_messages({"protocolVersion": "2024-11-05"})),
        match=MatchConfig(rewrite_protocol_version=True),
    )
    init_no_params = (
        json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialize"}).encode()
        + b"\n"
    )
    sink = _drive(server, [init_no_params])
    (resp,) = sink.lines
    assert isinstance(resp, dict)
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_non_dict_notification_payload_skipped() -> None:
    messages = [
        *_init_exchange_messages({}),
        _msg(
            2,
            "client",
            "request",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {}},
            method="tools/call",
            msg_id=2,
        ),
        _msg(
            3, "server", "response", {"jsonrpc": "2.0", "id": 2, "result": {}}, msg_id=2
        ),
        _msg(4, "server", "notification", "raw non-dict notification"),
    ]
    server = ReplayServer(_cassette(messages))
    call_line = (
        json.dumps(
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {}}
        ).encode()
        + b"\n"
    )
    sink = _drive(server, [INIT_LINE, call_line])
    # both responses came through, the raw notification did not
    assert [line["id"] for line in sink.lines if isinstance(line, dict)] == [10, 20]
    assert len(sink.lines) == 2


# --- new_episodes helpers -----------------------------------------------------------


def test_decode_rejects_junk_and_non_objects() -> None:
    assert _decode(b"junk\n") is None
    assert _decode(b"[1, 2]\n") is None
    assert _decode(b'{"a": 1}\n') == {"a": 1}


def _new_episodes_proxy(tmp_path_str: str, **kw: Any) -> NewEpisodesProxy:
    return NewEpisodesProxy(
        cassette=_cassette([]),
        cassette_path=tmp_path_str,
        server_cmd=["unused"],
        **kw,
    )


def test_new_episodes_custom_redaction_without_defaults(tmp_path: Any) -> None:
    from mcp_cassette.cassette import RedactionRule

    rule = RedactionRule(locator="*planted*")
    proxy = _new_episodes_proxy(
        str(tmp_path / "c.json"), redaction=[rule], include_default_redactions=False
    )
    assert proxy._recorder._rules == [rule]


def test_new_episodes_replay_emits_response_and_dict_notifications(
    tmp_path: Any,
) -> None:
    proxy = _new_episodes_proxy(str(tmp_path / "c.json"))
    proxy._out_lock = anyio.Lock()
    exchange = Exchange(
        request=_msg(
            0,
            "client",
            "request",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
            method="tools/call",
            msg_id=1,
        ),
        response=_msg(
            1,
            "server",
            "response",
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": 1}},
            msg_id=1,
        ),
        notifications=[
            _msg(2, "server", "notification", {"jsonrpc": "2.0", "method": "n/x"}),
            _msg(3, "server", "notification", "raw skipped"),
        ],
    )
    sink = _SinkStream()

    async def run() -> None:
        await proxy._replay({"id": 42}, exchange, sink)  # type: ignore[arg-type]

    anyio.run(run)
    assert sink.lines[0] == {"jsonrpc": "2.0", "id": 42, "result": {"ok": 1}}
    assert sink.lines[1] == {"jsonrpc": "2.0", "method": "n/x"}
    assert len(sink.lines) == 2  # the raw notification was skipped


def test_new_episodes_finalize_without_report(tmp_path: Any) -> None:
    target = tmp_path / "merged.json"
    proxy = _new_episodes_proxy(str(target))
    proxy._recorder.on_line(
        "client", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    )
    proxy._finalize()
    saved = Cassette.load(target)
    assert [m.seq for m in saved.messages] == [0]
    assert not (tmp_path / "merged.json.report.json").exists()


def test_server_request_with_raw_payload_is_never_emitted() -> None:
    # A hand-edited cassette can hold a server "request" whose payload is a raw
    # string; the tracker plans it, but emission must skip it rather than crash.
    messages = [
        _msg(
            0,
            "server",
            "request",
            "hand-edited junk",
            method="sampling/createMessage",
            msg_id=0,
        ),
        *[m.model_copy(update={"seq": m.seq + 1}) for m in _init_exchange_messages({})],
    ]
    server = ReplayServer(_cassette(messages))
    sink = _drive(server, [INIT_LINE])
    assert len(sink.lines) == 1  # only the initialize response; no junk emitted
    assert isinstance(sink.lines[0], dict)
    assert sink.lines[0]["id"] == 10
