"""Free-text redaction on the record path, end to end (ITER_01_v4 §04)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette.cassette import Cassette

TEAM_PACK = """version = 1
id = "team"

[[rules]]
id = "ticket"
label = "TICKET"
regex = 'TICKET-\\d{6}'
"""


def _record_cmd(cassette: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mcp_cassette",
        "record",
        "--cassette",
        str(cassette),
        *extra,
        "--",
        *reference_server_cmd(),
    ]


def test_planted_pii_never_reaches_disk(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.mcp.json"
    pack = tmp_path / "team.toml"
    pack.write_text(TEAM_PACK, encoding="utf-8")
    text = "mail alice@example.com about TICKET-123456"
    result = run_session(
        _record_cmd(cassette, "--pii-pack", str(pack), "--redact-profile", "team"),
        [*initialize_sequence(), tool_call(2, "echo", {"text": text})],
    )
    assert result.returncode == 0
    # Bytes in flight are never altered: the live agent saw the real echo.
    response = result.response_for(2)
    assert response is not None
    assert response["result"]["content"][0]["text"] == text

    on_disk = cassette.read_text(encoding="utf-8")
    assert "alice@example.com" not in on_disk  # request param and result alike
    assert "TICKET-123456" not in on_disk

    loaded = Cassette.load(cassette)
    assert loaded.format_version == 3
    manifest = loaded.redaction
    assert manifest is not None
    assert manifest.profile == "team"
    assert manifest.backends == ["structural", "regex-pii"]
    assert [ref.id for ref in manifest.packs] == ["common", "team"]
    assert manifest.packs[1].sha256 == hashlib.sha256(pack.read_bytes()).hexdigest()
    assert "team/ticket" in manifest.rule_ids
    # Evidence, never the salt: the manifest records the mode, not the key.
    assert manifest.salt_mode == "stable"
    assert "mcp-cassette/redaction/stable" not in on_disk


def test_no_default_redactions_suppresses_the_bundled_pack(tmp_path: Path) -> None:
    cassette = tmp_path / "plain.mcp.json"
    run_session(
        _record_cmd(cassette, "--no-default-redactions"),
        [*initialize_sequence(), tool_call(2, "echo", {"text": "alice@example.com"})],
    )
    assert "alice@example.com" in cassette.read_text(encoding="utf-8")
    manifest = Cassette.load(cassette).redaction
    assert manifest is not None
    assert manifest.packs == []
    assert manifest.backends == ["structural"]
