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
import subprocess
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


def test_server_only_rules_replace_and_mask(tmp_path: Path) -> None:
    """The other two strategies, on the rules ``pii-pack.toml`` marks ``server``.

    ``replace`` collapses every match to ``<LABEL>``; ``mask`` keeps the last four
    characters as human-readable evidence. Both rules are ``direction = "server"``,
    so they scrub what the server says and deliberately leave the agent's own request
    alone — a server-only rule cannot cause the request-matching collision that
    ``replace`` risks when it runs in both directions.
    """
    cassette = tmp_path / "strategies.mcp.json"
    text = "deploy to api.internal for ACCT-123456789"

    with mcc.use_cassette(cassette, mode="all", pii_packs=[TEAM_PACK]) as session:
        run(
            session.server_command(ECHO_SERVER),
            [*initialize(), tool_call(2, "echo", {"text": text})],
        )

    messages = mcc.Cassette.load(cassette).messages
    echoed = next(m for m in messages if m.kind == "response" and m.msg_id == 2)
    scrubbed = echoed.payload["result"]["content"][0]["text"]  # type: ignore[index]
    assert "<HOST>" in scrubbed  # replace: the hostname is gone entirely
    assert "•" * 10 + "6789" in scrubbed  # mask: only the last four survive
    assert "api.internal" not in scrubbed and "ACCT-12345" not in scrubbed

    # The request still carries both, because a server rule never reads it.
    sent = next(m for m in messages if m.method == "tools/call")
    assert "api.internal" in json.dumps(sent.payload)


def test_env_salt_makes_pseudonyms_project_specific(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``record --redact-salt-env`` keys the pseudonyms with your own secret.

    The default stable salt is dictionary-attackable for a low-entropy value such as
    an email address: anyone with the package can hash a guess and compare. An env
    salt removes that, and the price is stated here rather than discovered — two
    recordings of the same session under different salts differ byte for byte, so a
    re-record shows up as a diff.
    """
    pseudonyms = []
    for name, salt in (("x.mcp.json", "project-x"), ("y.mcp.json", "project-y")):
        monkeypatch.setenv("MCP_CASSETTE_REDACT_SALT", salt)
        cassette = tmp_path / name
        record = [
            sys.executable, "-m", "mcp_cassette", "record",
            "--cassette", str(cassette), "--redact-salt-env", "--", *ECHO_SERVER,
        ]  # fmt: skip
        run(record, [*initialize(), tool_call(2, "echo", {"text": SECRET_TEXT})])

        loaded = mcc.Cassette.load(cassette)
        assert loaded.redaction is not None
        assert loaded.redaction.salt_mode == "env"
        sent = next(m for m in loaded.messages if m.method == "tools/call")
        pseudonyms.append(json.dumps(sent.payload["params"]))

    assert "alice@example.com" not in "".join(pseudonyms)
    assert pseudonyms[0] != pseudonyms[1]


def test_a_profiled_recording_passes_the_redaction_gate(tmp_path: Path) -> None:
    """``--redact-profile`` is what the commit and CI gates actually check.

    The manifest records *that* scrubbing ran; the profile names *which* scrubbing,
    so a repository can demand its own. ``lint --require-redaction`` is exactly what
    the packaged ``mcp-cassette-redaction-check`` pre-commit hook runs on every staged
    cassette — the positive half of the refusal in ``test_lint_v4.py``.
    """
    cassette = tmp_path / "profiled.mcp.json"
    record = [
        sys.executable, "-m", "mcp_cassette", "record",
        "--cassette", str(cassette), "--pii-pack", str(TEAM_PACK),
        "--redact-profile", "team-baseline", "--", *ECHO_SERVER,
    ]  # fmt: skip
    run(record, [*initialize(), tool_call(2, "echo", {"text": SECRET_TEXT})])

    manifest = mcc.Cassette.load(cassette).redaction
    assert manifest is not None
    assert manifest.profile == "team-baseline"

    gate = subprocess.run(
        [sys.executable, "-m", "mcp_cassette", "lint", str(cassette),
         "--require-redaction", "team-baseline"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )  # fmt: skip
    assert gate.returncode == 0
    assert "alice@example.com" not in cassette.read_text(encoding="utf-8")


def test_hyphenated_key_names_are_redacted(tmp_path: Path) -> None:
    """Structural globs fold ``-`` and ``_``, so ``X-API-Key`` no longer slips through.

    Before 0.4.0 the shipped globs were ``*apikey*`` and ``*api_key*``, neither of
    which matches a hyphen — so a header-style key passed through in the clear while
    the message was still marked ``redacted``. The fold is a widening, so a custom
    glob of ``*api_key*`` now also fires on ``api-key``.
    """
    cassette = tmp_path / "keys.mcp.json"
    arguments = {"text": "hello", "X-API-Key": "live-abcdef123456"}

    with mcc.use_cassette(cassette, mode="all") as session:
        run(
            session.server_command(ECHO_SERVER),
            [*initialize(), tool_call(2, "echo", arguments)],
        )

    sent = next(
        m for m in mcc.Cassette.load(cassette).messages if m.method == "tools/call"
    )
    assert sent.payload["params"]["arguments"]["X-API-Key"] == "REDACTED"
    assert "live-abcdef123456" not in cassette.read_text(encoding="utf-8")


_NOISY_SERVER = '''
import json, sys

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    # A stray diagnostic object: well-formed JSON, but carrying neither "method" nor
    # "id", so it is not a request, a response, or a notification.
    print(json.dumps({"level": "info", "authorization": "Bearer live-key-42"}))
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}))
    sys.stdout.flush()
'''


def test_a_non_jsonrpc_object_still_gets_redacted(tmp_path: Path) -> None:
    """A line that is JSON but not JSON-RPC keeps its keys, so key rules still apply.

    Until 0.4.0 the recorder kept such a line as plain text, and structural rules
    need keys to match — so a credential in a stray diagnostic object landed on disk
    in the clear, on a message flagged ``redacted``. It is now recorded as
    ``kind: "unclassified"`` with the decoded object intact. Replay ignores these
    exactly as it ignored ``raw``; they are evidence, never something to match.
    """
    server = tmp_path / "noisy_server.py"
    server.write_text(_NOISY_SERVER, encoding="utf-8")
    cassette = tmp_path / "noisy.mcp.json"

    with mcc.use_cassette(cassette, mode="all") as session:
        run(session.server_command([sys.executable, str(server)]), initialize())

    stray = next(
        m for m in mcc.Cassette.load(cassette).messages if m.kind == "unclassified"
    )
    assert stray.payload["authorization"] == "REDACTED"  # type: ignore[index] — dict
    assert stray.redacted is True
    assert "live-key-42" not in cassette.read_text(encoding="utf-8")


@pytest.mark.mcp_cassette(
    cassette=Path(__file__).parent / "cassettes" / "pii.mcp.json",
    pii_packs=[TEAM_PACK],
)
def test_the_fixture_door_resolves_packs_too(
    mcp_cassette: mcc.CassetteSession,
) -> None:
    """``pii_packs=`` is on all four doors, because an unresolved pack is fatal.

    Replay resolves a manifest's packs by sha256 and fails loudly when it cannot, so
    every door needs a way to supply them: a stale recorded path is exactly what the
    hash identity exists to survive. Here the committed cassette holds pseudonyms,
    the agent sends the real address, and the two still match.
    """
    replayed = run(
        mcp_cassette.server_command(ECHO_SERVER),
        [*initialize(), tool_call(2, "echo", {"text": SECRET_TEXT})],
    )
    assert "<EMAIL>_" in _echo_text(replayed)
    assert "alice@example.com" not in _echo_text(replayed)
