"""Matcher unit tests: ordering disciplines, exchange building, ignore_params."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp_cassette.cassette import Cassette, MatchConfig, Message
from mcp_cassette.matching import Matcher


def _msg(
    seq: int,
    sender: str,
    kind: str,
    *,
    method: str | None = None,
    msg_id: int | None = None,
    payload: dict[str, Any] | str | None = None,
) -> Message:
    if payload is None:
        payload = {"jsonrpc": "2.0"}
        if method is not None:
            payload["method"] = method
        if msg_id is not None:
            payload["id"] = msg_id
    return Message(
        seq=seq,
        t_offset_ms=seq,
        sender=sender,  # type: ignore[arg-type] — test helper takes plain str
        kind=kind,  # type: ignore[arg-type] — test helper takes plain str
        method=method,
        msg_id=msg_id,
        payload=payload,
    )


def _request(seq: int, msg_id: int, method: str, params: dict[str, Any]) -> Message:
    return _msg(
        seq,
        "client",
        "request",
        method=method,
        msg_id=msg_id,
        payload={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
    )


def _response(seq: int, msg_id: int, result: Any) -> Message:
    return _msg(
        seq,
        "server",
        "response",
        msg_id=msg_id,
        payload={"jsonrpc": "2.0", "id": msg_id, "result": result},
    )


def _notification(seq: int, method: str) -> Message:
    return _msg(seq, "server", "notification", method=method)


def _cassette(messages: list[Message]) -> Cassette:
    return Cassette(recorded_at=datetime(2026, 7, 5, tzinfo=UTC), messages=messages)


def _req_obj(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 999, "method": method, "params": params}


TWO_CALLS = [
    _request(0, 1, "tools/call", {"name": "counter"}),
    _response(1, 1, {"n": 1}),
    _request(2, 2, "tools/call", {"name": "counter"}),
    _response(3, 2, {"n": 2}),
]


def test_ordering_none_always_returns_first_match_without_consuming() -> None:
    matcher = Matcher(_cassette(TWO_CALLS), MatchConfig(ordering="none"))
    first = matcher.find(_req_obj("tools/call", {"name": "counter"}))
    again = matcher.find(_req_obj("tools/call", {"name": "counter"}))
    assert first is not None and again is not None
    assert first is again  # not consumed, same exchange every time
    assert first.response is not None
    assert first.response.payload["result"] == {"n": 1}  # type: ignore[index]


def test_ordering_none_miss_returns_none() -> None:
    matcher = Matcher(_cassette(TWO_CALLS), MatchConfig(ordering="none"))
    assert matcher.find(_req_obj("tools/other", {})) is None
    assert matcher.misses == ["tools/other params={}"]


def test_ordering_strict_consumes_in_recorded_order() -> None:
    matcher = Matcher(_cassette(TWO_CALLS), MatchConfig(ordering="strict"))
    first = matcher.find(_req_obj("tools/call", {"name": "counter"}))
    second = matcher.find(_req_obj("tools/call", {"name": "counter"}))
    assert first is not None and second is not None
    assert first is not second
    assert second.response is not None
    assert second.response.payload["result"] == {"n": 2}  # type: ignore[index]


def test_ordering_strict_next_in_line_mismatch_is_unmatched() -> None:
    matcher = Matcher(_cassette(TWO_CALLS), MatchConfig(ordering="strict"))
    # next-in-line is the counter call; asking for anything else is a miss
    assert matcher.find(_req_obj("tools/call", {"name": "echo"})) is None
    # the queue head is NOT consumed by the miss
    assert matcher.find(_req_obj("tools/call", {"name": "counter"})) is not None


def test_ordering_strict_exhausted_queue_is_unmatched() -> None:
    matcher = Matcher(_cassette(TWO_CALLS), MatchConfig(ordering="strict"))
    matcher.find(_req_obj("tools/call", {"name": "counter"}))
    matcher.find(_req_obj("tools/call", {"name": "counter"}))
    assert matcher.find(_req_obj("tools/call", {"name": "counter"})) is None


def test_record_miss_appends_summary() -> None:
    matcher = Matcher(_cassette([]))
    matcher.record_miss("external miss")
    assert matcher.misses == ["external miss"]


def test_params_digest_truncated_in_miss_summary() -> None:
    matcher = Matcher(_cassette([]))
    matcher.find(_req_obj("tools/call", {"blob": "x" * 500}))
    (summary,) = matcher.misses
    assert summary.endswith("...")
    assert len(summary) < 200


def test_leading_notifications_collected_before_first_request() -> None:
    matcher = Matcher(
        _cassette(
            [
                _notification(0, "notifications/ready"),
                _request(1, 1, "tools/call", {"name": "echo"}),
                _response(2, 1, {}),
            ]
        )
    )
    assert [n.method for n in matcher.leading_notifications] == ["notifications/ready"]


def test_mid_stream_notifications_anchor_to_previous_exchange() -> None:
    matcher = Matcher(
        _cassette(
            [
                _request(0, 1, "tools/call", {"name": "a"}),
                _response(1, 1, {}),
                _notification(2, "notifications/progress"),
                _request(3, 2, "tools/call", {"name": "b"}),
                _response(4, 2, {}),
            ]
        )
    )
    first = matcher.find(_req_obj("tools/call", {"name": "a"}))
    second = matcher.find(_req_obj("tools/call", {"name": "b"}))
    assert first is not None and second is not None
    assert [n.method for n in first.notifications] == ["notifications/progress"]
    assert second.notifications == []


def test_request_with_raw_string_payload_gets_empty_key() -> None:
    raw_request = _msg(0, "client", "request", msg_id=1, payload="not json")
    matcher = Matcher(_cassette([raw_request]), MatchConfig(ordering="none"))
    # a request whose canonical key is that of an empty object matches it
    assert matcher.find({}) is not None


class TestIgnoreParams:
    def _matcher(self, params: dict[str, Any], *ignore: str) -> Matcher:
        cassette = _cassette(
            [_request(0, 1, "tools/call", params), _response(1, 1, {})]
        )
        return Matcher(cassette, MatchConfig(ignore_params=list(ignore)))

    def test_bare_token_ignores_top_level_key(self) -> None:
        matcher = self._matcher({"name": "echo"}, "params")
        # with "params" ignored entirely, any params payload matches
        assert matcher.find(_req_obj("tools/call", {"name": "DIFFERENT"})) is not None

    def test_pointer_through_list_element(self) -> None:
        matcher = self._matcher(
            {"items": [{"nonce": "aaa", "keep": 1}]}, "/params/items/0/nonce"
        )
        varied = _req_obj("tools/call", {"items": [{"nonce": "bbb", "keep": 1}]})
        assert matcher.find(varied) is not None

    def test_pointer_deleting_list_index(self) -> None:
        matcher = self._matcher({"items": ["a", "volatile"]}, "/params/items/1")
        varied = _req_obj("tools/call", {"items": ["a", "OTHER"]})
        assert matcher.find(varied) is not None

    def test_pointer_to_missing_path_is_noop(self) -> None:
        matcher = self._matcher({"name": "echo"}, "/params/nope/deep")
        assert matcher.find(_req_obj("tools/call", {"name": "echo"})) is not None

    def test_pointer_with_non_numeric_list_token_is_noop(self) -> None:
        matcher = self._matcher({"items": ["a"]}, "/params/items/abc")
        assert matcher.find(_req_obj("tools/call", {"items": ["a"]})) is not None

    def test_pointer_with_out_of_range_index_is_noop(self) -> None:
        matcher = self._matcher({"items": ["a"]}, "/params/items/99")
        assert matcher.find(_req_obj("tools/call", {"items": ["a"]})) is not None
