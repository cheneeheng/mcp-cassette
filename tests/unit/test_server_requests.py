"""Server-request tracker unit tests (ITER_03_v2 §04): plan, gating, accept-anything."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Any

import anyio

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.replay.server_requests import ServerRequestTracker


def _msg(
    seq: int,
    sender: str,
    kind: str,
    *,
    method: str | None = None,
    msg_id: str | int | None = None,
    exchange: int | None = None,
    channel: str | None = None,
) -> Message:
    payload: dict[str, Any] = {"jsonrpc": "2.0"}
    if method is not None:
        payload["method"] = method
    if msg_id is not None:
        payload["id"] = msg_id
    return Message(
        seq=seq,
        t_offset_ms=seq,
        sender=sender,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        method=method,
        msg_id=msg_id,
        payload=payload,
        exchange=exchange,
        channel=channel,  # type: ignore[arg-type]
    )


def _sampling_cassette() -> Cassette:
    """The canonical stdio sampling shape: SR inside the tools/call exchange."""
    return Cassette(
        recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
        messages=[
            _msg(0, "client", "request", method="initialize", msg_id=1),
            _msg(1, "server", "response", msg_id=1),
            _msg(2, "client", "notification", method="notifications/initialized"),
            _msg(3, "client", "request", method="tools/call", msg_id=2),
            _msg(4, "server", "request", method="sampling/createMessage", msg_id=0),
            _msg(5, "client", "response", msg_id=0),
            _msg(6, "server", "response", msg_id=2),
        ],
    )


def test_plan_trigger_during_exchange() -> None:
    tracker = ServerRequestTracker(_sampling_cassette())
    assert tracker.has_server_requests
    states = tracker.triggered_by(3, "during")
    assert len(states) == 1
    assert states[0].message.method == "sampling/createMessage"
    assert states[0].response_seq == 5


def test_plan_trigger_after_exchange() -> None:
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
        messages=[
            _msg(0, "client", "request", method="initialize", msg_id=1),
            _msg(1, "server", "response", msg_id=1),
            _msg(2, "server", "request", method="elicitation/create", msg_id=0),
        ],
    )
    tracker = ServerRequestTracker(cassette)
    states = tracker.triggered_by(0, "after")
    assert len(states) == 1
    assert states[0].response_seq is None  # never answered in the recording


def test_plan_free_floating_before_any_request() -> None:
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
        messages=[
            _msg(0, "server", "request", method="sampling/createMessage", msg_id=0)
        ],
    )
    tracker = ServerRequestTracker(cassette)
    assert len(tracker.triggered_by(None, "initialize")) == 1


def test_gate_blocks_only_after_emission() -> None:
    tracker = ServerRequestTracker(_sampling_cassette())
    assert not tracker.would_block(6, None)  # not emitted yet
    state = tracker.triggered_by(3, "during")[0]
    tracker.mark_emitted(state)
    assert tracker.would_block(6, None)
    assert not tracker.would_block(5, None)  # at/before the recorded response


def test_accept_anything_opens_gate_even_on_error_response() -> None:
    tracker = ServerRequestTracker(_sampling_cassette())
    state = tracker.triggered_by(3, "during")[0]
    tracker.mark_emitted(state)
    consumed = tracker.on_client_message(
        {"jsonrpc": "2.0", "id": 0, "error": {"code": -1, "message": "no sampling"}}
    )
    assert consumed is True
    assert not tracker.would_block(6, None)
    assert tracker.pending_summaries() == []


def test_non_matching_client_messages_are_not_consumed() -> None:
    tracker = ServerRequestTracker(_sampling_cassette())
    state = tracker.triggered_by(3, "during")[0]
    tracker.mark_emitted(state)
    assert not tracker.on_client_message({"jsonrpc": "2.0", "id": 99, "result": {}})
    assert not tracker.on_client_message(
        {"jsonrpc": "2.0", "id": 0, "method": "x"}  # a request, not a response
    )
    assert tracker.pending_summaries()


def test_pending_summary_names_method_and_id() -> None:
    tracker = ServerRequestTracker(_sampling_cassette())
    state = tracker.triggered_by(3, "during")[0]
    tracker.mark_emitted(state)
    [summary] = tracker.pending_summaries()
    assert "sampling/createMessage" in summary
    assert "id=0" in summary


def test_http_gate_is_scoped_to_the_exchange() -> None:
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
        transport="http",
        messages=[
            _msg(0, "client", "request", method="initialize", msg_id=1, exchange=0),
            _msg(1, "server", "response", msg_id=1, exchange=0, channel="post"),
            _msg(2, "client", "request", method="tools/call", msg_id=2, exchange=1),
            _msg(
                3,
                "server",
                "request",
                method="sampling/createMessage",
                msg_id=0,
                exchange=1,
                channel="post",
            ),
            _msg(4, "client", "response", msg_id=0, exchange=2),
            _msg(5, "server", "response", msg_id=2, exchange=1, channel="post"),
        ],
    )
    tracker = ServerRequestTracker(cassette)
    state = tracker.triggered_by(2, "during")[0]
    tracker.mark_emitted(state)
    assert tracker.would_block(5, 1)  # same exchange, after the recorded response
    assert not tracker.would_block(5, 7)  # another exchange is not gated


def test_wait_ready_releases_when_answered() -> None:
    tracker = ServerRequestTracker(_sampling_cassette())
    state = tracker.triggered_by(3, "during")[0]
    tracker.mark_emitted(state)
    order: list[str] = []

    async def main() -> None:
        async with anyio.create_task_group() as tg:

            async def waiter() -> None:
                await tracker.wait_ready(6, None)
                order.append("released")

            tg.start_soon(waiter)
            await anyio.sleep(0.05)
            order.append("answering")
            tracker.on_client_message({"jsonrpc": "2.0", "id": 0, "result": {}})

    anyio.run(partial(main))
    assert order == ["answering", "released"]
