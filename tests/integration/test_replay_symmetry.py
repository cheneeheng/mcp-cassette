"""Replay re-applies client-direction redaction (ITER_01_v4 decisions 3, 4, 9)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scripted_client import (
    initialize_sequence,
    reference_server_cmd,
    run_session,
    tool_call,
)

from mcp_cassette import CassetteError, use_cassette

CUSTOMER_PACK = """version = 1
id = "team"

[[rules]]
id = "customer"
label = "CUSTOMER"
regex = 'CUST-\\d{4}'
"""

COLLAPSING_PACK = CUSTOMER_PACK + 'strategy = "replace"\n'


def _record(cassette: Path, messages: list[dict[str, object]], *extra: str) -> str:
    cmd = [
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
    return run_session(cmd, messages).stderr


def _serve(cassette: Path, *extra: str) -> list[str]:
    return [sys.executable, "-m", "mcp_cassette", "serve", str(cassette), *extra]


def _call(text: str) -> list[dict[str, object]]:
    return [*initialize_sequence(), tool_call(2, "echo", {"text": text})]


def test_real_request_matches_its_scrubbed_recording(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    _record(cassette, _call("write to alice@example.com"))
    assert "alice@example.com" not in cassette.read_text(encoding="utf-8")

    # The agent sends the *real* email; its pseudonym is what was recorded.
    replay = run_session(_serve(cassette), _call("write to alice@example.com"))
    assert replay.returncode == 0, replay.stderr
    response = replay.response_for(2)
    assert response is not None
    assert "error" not in response


def test_missing_pack_exits_2_naming_it_and_is_fixable(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    pack = tmp_path / "team.toml"
    pack.write_text(CUSTOMER_PACK, encoding="utf-8")
    _record(cassette, _call("order for CUST-1234"), "--pii-pack", str(pack))

    moved = tmp_path / "elsewhere" / "renamed.toml"
    moved.parent.mkdir()
    pack.rename(moved)
    missing = subprocess.run(
        _serve(cassette), capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert missing.returncode == 2
    assert "'team'" in missing.stderr
    assert "--pii-pack" in missing.stderr

    # Identity is the content hash, so a moved and renamed pack still resolves.
    replay = run_session(
        _serve(cassette, "--pii-pack", str(moved)), _call("order for CUST-1234")
    )
    assert replay.returncode == 0, replay.stderr


def test_unused_supplied_pack_is_accepted_with_a_note(tmp_path: Path) -> None:
    cassette = tmp_path / "c.mcp.json"
    _record(cassette, _call("hello"))
    extra = tmp_path / "unrelated.toml"
    extra.write_text(CUSTOMER_PACK.replace('"team"', '"other"'), encoding="utf-8")
    replay = run_session(_serve(cassette, "--pii-pack", str(extra)), _call("hello"))
    assert replay.returncode == 0
    assert "not named by this cassette's redaction manifest" in replay.stderr


def test_replace_with_both_directions_warns_at_record_time(tmp_path: Path) -> None:
    pack = tmp_path / "collapse.toml"
    pack.write_text(COLLAPSING_PACK, encoding="utf-8")
    stderr = _record(
        tmp_path / "c.mcp.json", _call("CUST-0001"), "--pii-pack", str(pack)
    )
    assert "team/customer" in stderr
    assert "collapse" in stderr


def test_library_door_reaches_and_fixes_the_missing_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "c.mcp.json"
    pack = tmp_path / "team.toml"
    pack.write_text(CUSTOMER_PACK, encoding="utf-8")
    _record(cassette, _call("CUST-9876"), "--pii-pack", str(pack))
    moved = tmp_path / "moved.toml"
    pack.rename(moved)

    with pytest.raises(CassetteError, match="'team'"):
        with use_cassette(cassette, mode="none") as session:
            session.server_command(["python", "server.py"])

    with use_cassette(cassette, mode="none", pii_packs=[moved]) as session:
        cmd = session.server_command(["python", "server.py"])
    assert cmd[cmd.index("--pii-pack") + 1] == str(moved)
