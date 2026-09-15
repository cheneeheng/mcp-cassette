"""Free-text PII redaction, end to end: record, inspect the cassette, replay.

Key-name rules (``*token*``, ``authorization``) cannot see an email address inside a
``tools/call`` argument. Redaction packs can. This example records a session whose
request carries an email and an employee id, then shows three things:

1. the cassette on disk holds stable pseudonyms (``<EMAIL>_3f9a…``), never the raw
   values;
2. the cassette carries a redaction manifest naming the packs that ran, by sha256;
3. replay still matches the agent's *raw* request, because the same rules are
   re-applied to live requests before matching.

The bundled ``common`` pack catches the email; ``examples/pii-pack.toml`` adds the
team-specific employee id. Each test records into its own temporary directory, so
nothing here touches the committed cassettes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp_client import initialize, run, tool_call

import mcp_cassette as mcc

HERE = Path(__file__).parent
ECHO_SERVER = [sys.executable, str(HERE / "echo_server.py")]
TEAM_PACK = HERE / "pii-pack.toml"
SECRET_TEXT = "ping alice@example.com about EMP-123456"


@pytest.fixture(autouse=True)
def _allow_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    # MCP_CASSETTE_MODE outranks mode=, so CI's "none" would forbid these recordings.
    # They record the bundled stdlib server into tmp_path, never a live server.
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)


def _echo_text(objects: list[dict[str, object]]) -> str:
    for obj in objects:
        if obj.get("id") == 2 and "method" not in obj:
            return obj["result"]["content"][0]["text"]  # type: ignore[index] — JSON
    raise AssertionError("no echo response")


def test_record_scrubs_pii_and_replay_still_matches(tmp_path: Path) -> None:
    cassette = tmp_path / "pii.mcp.json"
    messages = [*initialize(), tool_call(2, "echo", {"text": SECRET_TEXT})]

    # Record: the agent sees the live server's answer, unaltered in flight.
    with mcc.use_cassette(cassette, mode="all", pii_packs=[TEAM_PACK]) as session:
        live = run(session.server_command(ECHO_SERVER), messages)
    assert "alice@example.com" in _echo_text(live)

    # On disk: pseudonyms only, in both the request and the echoed response.
    raw = cassette.read_text(encoding="utf-8")
    assert "alice@example.com" not in raw
    assert "EMP-123456" not in raw
    assert "<EMAIL>_" in raw and "<EMPLOYEE>_" in raw

    manifest = mcc.Cassette.load(cassette).redaction
    assert manifest is not None
    assert {pack.id for pack in manifest.packs} == {"common", "team"}
    assert all(len(pack.sha256) == 64 for pack in manifest.packs)

    # Replay: the agent sends the same raw email, and the request still matches.
    with mcc.use_cassette(cassette, mode="none", pii_packs=[TEAM_PACK]) as session:
        replayed = run(session.server_command(ECHO_SERVER), messages)
    assert "<EMAIL>_" in _echo_text(replayed)


def test_hash_pseudonyms_are_stable_across_recordings(tmp_path: Path) -> None:
    """The default salt is stable, so re-recording yields identical pseudonyms.

    That is what keeps a re-recorded cassette diffable. ``record --redact-salt-env``
    trades this away for pseudonyms that cannot be dictionary-attacked across projects.
    """
    pseudonyms = []
    for name in ("a.mcp.json", "b.mcp.json"):
        cassette = tmp_path / name
        with mcc.use_cassette(cassette, mode="all") as session:
            run(
                session.server_command(ECHO_SERVER),
                [*initialize(), tool_call(2, "echo", {"text": SECRET_TEXT})],
            )
        request = next(
            m for m in mcc.Cassette.load(cassette).messages if m.method == "tools/call"
        )
        pseudonyms.append(json.dumps(request.payload["params"]))
    assert pseudonyms[0] == pseudonyms[1]
