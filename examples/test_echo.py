"""End-to-end examples of the ``mcp_cassette`` pytest fixture.

Run them from the repo root::

    uv run pytest examples/                       # replay committed cassettes (offline)
    MCP_CASSETTE_MODE=all uv run pytest examples/ # re-record against echo_server.py

The committed cassettes under ``examples/cassettes/`` let these tests pass with no live
server: the fixture replays them deterministically. Delete one and run in the default
``once`` (or ``all``) mode to record it afresh against ``echo_server.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from mcp_client import initialize, run, tool_call

import mcp_cassette as mcc

HERE = Path(__file__).parent
CASSETTES = HERE / "cassettes"
ECHO_SERVER = [sys.executable, str(HERE / "echo_server.py")]


def _response_for(objects: list[dict[str, Any]], msg_id: int) -> dict[str, Any]:
    for obj in objects:
        if obj.get("id") == msg_id and "method" not in obj:
            return obj
    raise AssertionError(f"no response for id {msg_id}")


@pytest.mark.mcp_cassette(cassette=CASSETTES / "echo_and_add.mcp.json")
def test_echo_and_add(mcp_cassette: mcc.CassetteSession) -> None:
    """Record on first run, replay forever after — same command, no code change."""
    cmd = mcp_cassette.server_command(ECHO_SERVER)
    objects = run(
        cmd,
        [
            *initialize(),
            tool_call(2, "echo", {"text": "hello cassette"}),
            tool_call(3, "add", {"a": 40, "b": 2}),
        ],
    )

    echo = _response_for(objects, 2)
    assert echo["result"]["content"][0]["text"].startswith("hello cassette")
    add = _response_for(objects, 3)
    assert add["result"]["content"][0]["text"] == "42"


@pytest.mark.mcp_cassette(cassette=CASSETTES / "deterministic.mcp.json")
def test_replay_is_deterministic(mcp_cassette: mcc.CassetteSession) -> None:
    """The echo tool mints a random token per call; replay returns the recorded one.

    Two replays of the same cassette yield the identical token — a live server would
    return a different token each time. This is what makes cassette-backed tests stable.
    """
    cmd = mcp_cassette.server_command(ECHO_SERVER)
    first = run(cmd, [*initialize(), tool_call(2, "echo", {"text": "ping"})])
    second = run(cmd, [*initialize(), tool_call(2, "echo", {"text": "ping"})])
    assert (
        _response_for(first, 2)["result"]["content"][0]["text"]
        == _response_for(second, 2)["result"]["content"][0]["text"]
    )


@pytest.mark.mcp_cassette(cassette=CASSETTES / "fault.mcp.json")
def test_survives_injected_error(mcp_cassette: mcc.CassetteSession) -> None:
    """One recorded cassette drives a fault case: make ``tools/call`` return an error.

    ``with_faults`` overlays the fault at replay time on top of the *matched* exchange;
    the recorded cassette itself is never mutated. Faults fire after a request matches,
    so the call must still correspond to a recorded interaction.
    """
    session = mcp_cassette.with_faults(
        mcc.Fault.error("tools/call", code=-32000, message="simulated outage")
    )
    cmd = session.server_command(ECHO_SERVER)
    objects = run(cmd, [*initialize(), tool_call(2, "echo", {"text": "hello"})])

    faulted = _response_for(objects, 2)
    assert faulted["error"]["code"] == -32000
    assert faulted["error"]["message"] == "simulated outage"


def _answer_sampling(request: dict[str, Any]) -> dict[str, Any]:
    """A canned client-side LLM: answer any sampling request with fixed text."""
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "role": "assistant",
            "content": {"type": "text", "text": "A tiny summary."},
            "model": "example-llm",
        },
    }


@pytest.mark.mcp_cassette(cassette=CASSETTES / "sampling.mcp.json")
def test_server_initiated_sampling(mcp_cassette: mcc.CassetteSession) -> None:
    """Sampling replays too (v2): the server asks the *client* mid-call.

    The ``summarize`` tool sends a ``sampling/createMessage`` request and only
    responds once the client answers. On replay, mcp-cassette re-emits the recorded
    sampling request, accepts *whatever* the client answers (the answer comes from an
    LLM and legitimately differs every run — it is never matched), and only then
    releases the recorded tool result.
    """
    cmd = mcp_cassette.server_command(ECHO_SERVER)
    objects = run(
        cmd,
        [*initialize(), tool_call(2, "summarize", {"text": "a very long document"})],
        responder=_answer_sampling,
    )

    sampling = [o for o in objects if o.get("method") == "sampling/createMessage"]
    assert sampling, "server never asked the client to sample"
    # The recorded answer, not the live one, lands in the replayed result — change
    # _answer_sampling's text and replay still returns "A tiny summary.".
    result = _response_for(objects, 2)
    assert result["result"]["content"][0]["text"] == "summary: A tiny summary."
