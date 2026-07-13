"""Schema, load/save, and redaction unit tests (SKELETON §02/§04, ITER_01 redaction)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
        recorded_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
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
