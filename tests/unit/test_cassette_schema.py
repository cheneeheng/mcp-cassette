"""Schema, load/save, and redaction unit tests (SKELETON §02/§04, ITER_01 redaction)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import (
    Cassette,
    Fault,
    FaultOverlay,
    MatchConfig,
    Message,
    RedactionRule,
    UnsupportedFormatVersion,
    apply_redactions,
    default_redaction_rules,
)


def _message(seq: int, **kw: object) -> Message:
    base = dict(
        seq=seq,
        t_offset_ms=seq * 10,
        sender="client",
        kind="request",
        method="tools/call",
        msg_id=seq,
        payload={"jsonrpc": "2.0", "id": seq, "method": "tools/call"},
    )
    base.update(kw)
    return Message(**base)  # type: ignore[arg-type]


def _cassette() -> Cassette:
    return Cassette(
        recorded_at=datetime(2026, 7, 5, tzinfo=UTC),
        protocol_version="2024-11-05",
        messages=[_message(0), _message(1, sender="server", kind="response")],
    )


def test_round_trip_save_load(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    original = _cassette()
    original.save(path)
    loaded = Cassette.load(path)
    assert loaded.model_dump() == original.model_dump()


def test_save_is_stable_and_indented(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    _cassette().save(path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("{\n")  # indent=2, human-diffable
    # field order is model order, not alphabetical: format_version comes first
    assert text.index('"format_version"') < text.index('"recorded_at"')


def test_format_version_gate_rejects_newer(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    data = json.loads(_cassette().model_dump_json())
    data["format_version"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedFormatVersion):
        Cassette.load(path)


def test_raw_message_stores_string_payload() -> None:
    msg = Message(
        seq=0,
        t_offset_ms=0,
        sender="server",
        kind="raw",
        payload="this is not json",
    )
    assert msg.payload == "this is not json"


def test_redaction_key_glob_default_rules() -> None:
    payload = {"params": {"api_key": "sk-secret", "nested": {"token": "abc"}}, "id": 1}
    redacted, changed = apply_redactions(payload, default_redaction_rules())
    assert changed is True
    assert redacted["params"]["api_key"] == "REDACTED"
    assert redacted["params"]["nested"]["token"] == "REDACTED"
    # original is untouched (deep copy)
    assert payload["params"]["api_key"] == "sk-secret"


def test_redaction_json_pointer() -> None:
    rule = RedactionRule(locator="/result/content/0/text")
    payload = {"result": {"content": [{"text": "secret body"}]}}
    out = rule.apply(payload)
    assert out["result"]["content"][0]["text"] == "REDACTED"


def test_redaction_custom_replacement() -> None:
    rule = RedactionRule(locator="authorization", replacement="***")
    out = rule.apply({"headers": {"Authorization": "Bearer x"}})
    assert out["headers"]["Authorization"] == "***"


def test_redaction_leaves_raw_string_untouched() -> None:
    out, changed = apply_redactions("not a dict", default_redaction_rules())
    assert out == "not a dict"
    assert changed is False


def test_redaction_rule_apply_on_raw_string_is_noop() -> None:
    rule = RedactionRule(locator="*token*")
    assert rule.apply("raw line, not json") == "raw line, not json"


def test_apply_redactions_with_pointer_rule() -> None:
    rules = [RedactionRule(locator="/result/secret")]
    redacted, changed = apply_redactions({"result": {"secret": "x"}}, rules)
    assert changed is True
    assert redacted["result"]["secret"] == "REDACTED"


def test_pointer_through_missing_intermediate_is_noop() -> None:
    rule = RedactionRule(locator="/result/missing/deep")
    payload = {"result": {}}
    out, changed = apply_redactions(payload, [rule])
    assert changed is False
    assert out == payload


def test_pointer_stepping_through_scalar_is_noop() -> None:
    rule = RedactionRule(locator="/result/text/inner/deep")
    out, changed = apply_redactions({"result": {"text": "plain"}}, [rule])
    assert changed is False
    assert out == {"result": {"text": "plain"}}


def test_key_glob_recurses_into_lists() -> None:
    payload = {"result": {"content": [{"api_key": "sk-1"}, {"plain": "x"}]}}
    out, changed = apply_redactions(payload, default_redaction_rules())
    assert changed is True
    assert out["result"]["content"][0]["api_key"] == "REDACTED"
    assert out["result"]["content"][1]["plain"] == "x"


def test_pointer_to_missing_dict_key_is_noop() -> None:
    rule = RedactionRule(locator="/result/nope")
    out, changed = apply_redactions({"result": {}}, [rule])
    assert changed is False
    assert out == {"result": {}}


def test_pointer_replaces_list_element() -> None:
    rule = RedactionRule(locator="/result/content/1")
    out, changed = apply_redactions({"result": {"content": ["a", "b"]}}, [rule])
    assert changed is True
    assert out["result"]["content"] == ["a", "REDACTED"]


def test_pointer_list_index_out_of_range_is_noop() -> None:
    rule = RedactionRule(locator="/result/content/9")
    out, changed = apply_redactions({"result": {"content": ["a"]}}, [rule])
    assert changed is False
    assert out["result"]["content"] == ["a"]


def test_pointer_non_numeric_list_token_is_noop() -> None:
    rule = RedactionRule(locator="/result/content/abc")
    out, changed = apply_redactions({"result": {"content": ["a"]}}, [rule])
    assert changed is False


def test_empty_pointer_is_noop() -> None:
    # "" is not a valid pointer; _redact_pointer treats no-tokens as no-op
    from mcp_cassette.cassette import _redact_pointer

    assert _redact_pointer({"a": 1}, "", "REDACTED") is False


def test_v2_defaults_keep_v1_cassettes_loading(tmp_path: Path) -> None:
    # A format-1 file with none of the v2 fields loads unchanged.
    path = tmp_path / "v1.json"
    data = json.loads(_cassette().model_dump_json())
    data["format_version"] = 1
    for message in data["messages"]:
        message.pop("exchange", None)
        message.pop("channel", None)
    for key in ("server_url", "session_id"):
        data.pop(key, None)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = Cassette.load(path)
    assert loaded.transport == "stdio"
    assert loaded.server_url is None
    assert loaded.session_id is None
    assert loaded.messages[0].exchange is None
    assert loaded.messages[0].channel is None


def test_save_always_writes_current_format_version(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cassette = _cassette().model_copy(update={"format_version": 1})
    cassette.save(path)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["format_version"] == 2


def test_http_fields_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    cassette = Cassette(
        recorded_at=datetime(2026, 7, 5, tzinfo=UTC),
        transport="http",
        server_url="https://mcp.example.com/mcp",
        session_id="abc123",
        messages=[
            _message(0, exchange=0),
            _message(1, sender="server", kind="response", exchange=0, channel="post"),
        ],
    )
    cassette.save(path)
    loaded = Cassette.load(path)
    assert loaded.transport == "http"
    assert loaded.server_url == "https://mcp.example.com/mcp"
    assert loaded.session_id == "abc123"
    assert loaded.messages[1].exchange == 0
    assert loaded.messages[1].channel == "post"


def test_fault_constructors() -> None:
    assert Fault.timeout("tools/call", nth=2).type == "timeout"
    assert Fault.timeout("tools/call", nth=2).target.nth == 2
    err = Fault.error("tools/call", code=-32000, message="boom")
    assert err.type == "error"
    assert err.params == {"code": -32000, "message": "boom"}
    assert Fault.delay("x", 50).params == {"ms": 50}
    assert Fault.malformed("x", strategy="not_json").params["strategy"] == "not_json"
    assert Fault.disconnect("x", after_response=True).params["after_response"] is True


def test_fault_overlay_round_trip(tmp_path: Path) -> None:
    overlay = FaultOverlay(faults=[Fault.timeout("tools/call")])
    path = tmp_path / "f.json"
    path.write_text(overlay.model_dump_json(), encoding="utf-8")
    loaded = FaultOverlay.load(path)
    assert loaded.faults[0].type == "timeout"


def test_match_config_defaults() -> None:
    cfg = MatchConfig()
    assert cfg.match_on == ["method", "params"]
    assert cfg.ordering == "per_method"
    assert cfg.on_unmatched == "error"
    assert cfg.rewrite_protocol_version is False
