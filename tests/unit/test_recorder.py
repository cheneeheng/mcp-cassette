"""Unit tests for wire-line classification and initialize metadata watching."""

from __future__ import annotations

import warnings

import pytest

from mcp_cassette.record.recorder import SessionRecorder


def test_blank_line_is_skipped() -> None:
    recorder = SessionRecorder()
    recorder.on_line("client", b"   \r\n")
    assert recorder.message_count == 0


def test_json_without_method_or_id_recorded_as_unclassified() -> None:
    recorder = SessionRecorder()
    recorder.on_line("server", b'{"jsonrpc": "2.0"}\n')
    (msg,) = recorder.build().messages
    assert msg.kind == "unclassified"
    assert msg.payload == {"jsonrpc": "2.0"}


def test_json_non_object_recorded_as_raw() -> None:
    recorder = SessionRecorder()
    recorder.on_line("server", b"[1, 2, 3]\n")
    (msg,) = recorder.build().messages
    assert msg.kind == "raw"


def test_non_json_warns_once_with_a_count_at_finalize() -> None:
    recorder = SessionRecorder()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no per-line warning any more
        recorder.on_line("server", b"plain log line\n")
        recorder.on_line("server", b"another log line\n")
    with pytest.warns(UserWarning, match="2 recorded as kind='raw'"):
        recorder.warn_unrecognized()
    assert recorder.message_count == 2


def test_notification_classified_by_shape() -> None:
    recorder = SessionRecorder()
    recorder.on_line("server", b'{"jsonrpc":"2.0","method":"notifications/x"}\n')
    (msg,) = recorder.build().messages
    assert msg.kind == "notification"
    assert msg.msg_id is None


def test_response_without_initialize_request_leaves_metadata_unset() -> None:
    recorder = SessionRecorder()
    recorder.on_line("server", b'{"jsonrpc":"2.0","id":7,"result":{}}\n')
    cassette = recorder.build()
    assert cassette.protocol_version is None
    assert cassette.server_info is None


def _handshake(recorder: SessionRecorder, result: object) -> None:
    recorder.on_line(
        "client",
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
    )
    import json

    response = {"jsonrpc": "2.0", "id": 1, "result": result}
    recorder.on_line("server", json.dumps(response).encode() + b"\n")


def test_initialize_with_non_dict_result_ignored() -> None:
    recorder = SessionRecorder()
    _handshake(recorder, "not a dict")
    cassette = recorder.build()
    assert cassette.protocol_version is None
    assert cassette.server_info is None


def test_initialize_with_non_string_protocol_version_ignored() -> None:
    recorder = SessionRecorder()
    _handshake(
        recorder,
        {"protocolVersion": 123, "serverInfo": {"name": "s", "version": "1"}},
    )
    cassette = recorder.build()
    assert cassette.protocol_version is None
    assert cassette.server_info is not None
    assert cassette.server_info.name == "s"


def test_initialize_with_malformed_server_info_ignored() -> None:
    recorder = SessionRecorder()
    _handshake(recorder, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "s"}})
    cassette = recorder.build()
    assert cassette.protocol_version == "2024-11-05"
    assert cassette.server_info is None
