"""The unclassified message kind: ITER_06_v3 F2 route 3 (ITER_01_v4 decision 8)."""

from __future__ import annotations

import warnings

import pytest

from mcp_cassette.cassette import default_redaction_rules
from mcp_cassette.record.recorder import SessionRecorder


def test_object_without_method_or_id_keeps_its_decoded_payload() -> None:
    recorder = SessionRecorder(default_redaction_rules())
    recorder.on_line("server", b'{"authorization": "Bearer live", "note": "x"}\n')
    (message,) = recorder.build().messages
    assert message.kind == "unclassified"
    assert message.payload == {"authorization": "REDACTED", "note": "x"}
    assert message.redacted is True


def test_unparseable_line_is_still_raw() -> None:
    recorder = SessionRecorder()
    recorder.on_line("server", b"plain log line\n")
    (message,) = recorder.build().messages
    assert message.kind == "raw"
    assert message.payload == "plain log line"


def test_bare_json_array_is_still_raw() -> None:
    # Route 2 stays deferred: Message.payload does not admit a list.
    recorder = SessionRecorder()
    recorder.on_line("server", b"[1, 2, 3]\n")
    (message,) = recorder.build().messages
    assert message.kind == "raw"


def test_warning_is_a_count_reported_at_finalize() -> None:
    recorder = SessionRecorder()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # nothing may warn per line any more
        recorder.on_line("server", b"log one\n")
        recorder.on_line("server", b"log two\n")
        recorder.on_line("server", b'{"orphan": true}\n')
    with pytest.warns(UserWarning, match="2 recorded as kind='raw', 1 as") as record:
        recorder.warn_unrecognized()
    assert len(record) == 1


def test_clean_session_does_not_warn() -> None:
    recorder = SessionRecorder()
    recorder.on_line("client", b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        recorder.warn_unrecognized()
