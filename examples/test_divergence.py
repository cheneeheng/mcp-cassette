"""The divergence report: the step where a changed agent left the recording.

Replay answers from the cassette. When the agent makes a call the recording has no
answer for, as a new prompt or model often does, replay stops at that step and shows
the recorded call and the new one as a diff. It never falls through to a live server,
because live data would make the comparison with the old agent unfair.

Here a "prompt change" makes the agent echo different text than it did when
``echo_and_add.mcp.json`` was recorded::

    step 2: tools/call: no recorded call matches
    --- recorded
    +++ new
    ...
    -    "text": "hello cassette"
    +    "text": "hello, changed prompt"

Under pytest the ``mcp_cassette`` fixture fails the test with the same block.
``--on-unmatched error`` (marker: ``on_unmatched="error"``) answers the miss with an
error and keeps serving instead, for agents that recover from a failed call.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_client import initialize, tool_call

CASSETTE = Path(__file__).parent / "cassettes" / "echo_and_add.mcp.json"


def test_changed_call_stops_replay_with_a_divergence_report() -> None:
    changed = [
        *initialize(),
        tool_call(2, "echo", {"text": "hello, changed prompt"}),
        tool_call(3, "add", {"a": 40, "b": 2}),
    ]
    result = subprocess.run(
        [sys.executable, "-m", "mcp_cassette", "serve", str(CASSETTE)],
        input="".join(json.dumps(m) + "\n" for m in changed),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 3  # the replay-miss exit code
    assert "step 2: tools/call: no recorded call matches" in result.stderr
    assert '-    "text": "hello cassette"' in result.stderr
    assert '+    "text": "hello, changed prompt"' in result.stderr
    answered = [json.loads(line).get("id") for line in result.stdout.splitlines()]
    assert 3 not in answered  # stopped at step 2: the later call never ran
