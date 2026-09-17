"""The single-writer claim: two sessions recording one cassette cannot both win.

Under ``pytest -n auto`` two tests that share a cassette path used to race, and the last
write silently erased the other recording. A recording session now holds a
``<cassette>.claim`` sidecar for as long as it is open:

* a second **re-recording** session (``all`` or ``new_episodes``) fails fast with a
  ``CassetteError`` naming the holder (the CLI exits ``6``, the one code worth
  retrying);
* a ``once`` session waits for the writer, then replays what it wrote;
* replay never claims, so any number of readers can share a cassette.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from mcp_client import initialize, run, tool_call

import mcp_cassette as mcc

HERE = Path(__file__).parent
ECHO_SERVER = [sys.executable, str(HERE / "echo_server.py")]
GOLDEN = HERE / "cassettes" / "echo_and_add.mcp.json"


def test_second_writer_is_refused_while_the_first_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MCP_CASSETTE_MODE outranks mode=, so CI's "none" would forbid this recording.
    # It records the bundled stdlib server into tmp_path, never a live server.
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "shared.mcp.json"
    claim = cassette.with_name(cassette.name + ".claim")

    with mcc.use_cassette(cassette, mode="all") as first:
        command = first.server_command(ECHO_SERVER)
        assert claim.exists()  # held from server_command until the block closes

        with pytest.raises(mcc.CassetteError, match="held for writing"):
            with mcc.use_cassette(cassette, mode="all") as second:
                second.server_command(ECHO_SERVER)

        run(command, [*initialize(), tool_call(2, "add", {"a": 1, "b": 2})])

    assert not claim.exists()  # released on close
    assert cassette.exists()


def test_a_once_session_waits_and_then_replays_what_the_writer_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``pytest -n auto`` case: one worker records, the rest replay that file.

    Two workers running the same test against one cassette path is the ordinary
    consequence of ``-n auto``, and "one records, the rest replay" is what ``once``
    means. So a ``once`` session that finds a live claim waits for it instead of
    failing, then resolves again — and by then the cassette exists, so it replays
    the recording the other worker just made rather than racing it.
    """
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "shared.mcp.json"
    messages = [*initialize(), tool_call(2, "add", {"a": 1, "b": 2})]

    def waiter() -> list[dict[str, Any]]:
        with mcc.use_cassette(cassette, mode="once") as session:
            command = session.server_command(ECHO_SERVER)
            assert command[3] == "serve"  # it waited, it did not record
            return run(command, messages)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with mcc.use_cassette(cassette, mode="all") as writer:
            command = writer.server_command(ECHO_SERVER)  # takes the claim
            waiting = pool.submit(waiter)  # blocks until the claim is released
            assert not waiting.done()
            run(command, messages)
        replayed = waiting.result(timeout=60)

    assert any(obj.get("id") == 2 for obj in replayed)  # answered from the cassette


def test_readers_share_a_cassette_without_claiming() -> None:
    """Two replay sessions open on one committed cassette at once is always safe."""
    claim = GOLDEN.with_name(GOLDEN.name + ".claim")
    with (
        mcc.use_cassette(GOLDEN, mode="none") as a,
        mcc.use_cassette(GOLDEN, mode="none") as b,
    ):
        assert a.server_command(ECHO_SERVER)[3] == "serve"
        assert b.server_command(ECHO_SERVER)[3] == "serve"
        assert not claim.exists()


def test_the_cli_exits_6_and_force_breaks_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit ``6`` is its own code because it is the one failure worth retrying.

    A usage error (``2``) never succeeds on a second attempt; a claim conflict
    usually does, because the other writer finishes. ``--claim-wait SECONDS`` waits
    for that instead of failing, and ``--force`` takes the claim away from a writer
    that is still running — loudly, since it is someone else's recording.
    """
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    cassette = tmp_path / "contested.mcp.json"

    def record(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "mcp_cassette", "record",
             "--cassette", str(cassette), *extra, "--", *ECHO_SERVER],
            capture_output=True, text=True, encoding="utf-8", input="", timeout=60,
        )  # fmt: skip

    with mcc.use_cassette(cassette, mode="all") as holder:
        holder.server_command(ECHO_SERVER)  # the library session now holds the claim

        refused = record()
        assert refused.returncode == 6
        assert "held for writing" in refused.stderr
        assert "--force to break the claim" in refused.stderr  # the fix is named

        forced = record("--force")
        assert forced.returncode != 6
        assert "--force breaks the live claim" in forced.stderr
