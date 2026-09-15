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

import sys
from pathlib import Path

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
