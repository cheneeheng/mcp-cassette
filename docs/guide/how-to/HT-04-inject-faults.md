# HT-04. Inject faults

**When:** you want to test how your agent behaves when the MCP server is slow, errors,
returns garbage, or dies — without breaking a real server.
**Prerequisites:** a recorded cassette for the test. Faults are **replay-only**.

One recorded cassette drives a whole resilience matrix. The cassette is never mutated:
faults live in a separate `FaultOverlay`, either built in test code or loaded from a
`<cassette>.faults.json` sidecar.

Every front door injects the same overlay, over either transport:

| Door | How you attach the overlay |
|---|---|
| pytest fixture | `mcp_cassette.with_faults(fault, ...)` → a derived session (HT-04.1) |
| `use_cassette` | `use_cassette(..., faults=FaultOverlay(faults=[...]))` (HT-04.2) |
| CLI | `mcp-cassette serve <cassette> --faults <sidecar>.json` (HT-04.3) |

The fixture is a convenience over the CLI flag for stdio: `with_faults` serializes your
overlay to a temp sidecar and passes `--faults` to the replay subprocess it builds. Over
HTTP the replay server runs in-process and takes the overlay object directly. Same
`Injector`, same hook point, same behavior either way.

## HT-04.1 From the pytest fixture

```python
import mcp_cassette as mcc
import pytest

@pytest.mark.parametrize("fault", [
    mcc.Fault.timeout("tools/call", nth=1),
    mcc.Fault.error("tools/call", code=-32000, message="rate limited"),
    mcc.Fault.disconnect("tools/call"),
])
def test_agent_survives_tool_trouble(mcp_cassette, fault):
    session = mcp_cassette.with_faults(fault)
    cmd = session.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert result.completed_with_degraded_tools
```

`with_faults()` returns a **new** `CassetteSession`, so parametrized tests never share
state. Pass several faults in one call to combine them. The fixture still finalizes on
teardown — the derived session is registered on the one the fixture handed you, so a
fault test's replay misses fail the test exactly as an ordinary one's do.

For a remote server, swap the last two lines for `server_url` — the derived session is
the same object either way:

```python
    url = session.server_url("https://mcp.example.com/mcp")
    result = run_my_agent(mcp_servers={"github": {"url": url}})
```

**Verify:** the agent takes its degraded path and the test still passes offline.

## HT-04.2 From library code

`use_cassette` takes the overlay as an argument — build it in Python rather than writing
a sidecar:

```python
from mcp_cassette import Fault, FaultOverlay, use_cassette

overlay = FaultOverlay(faults=[
    Fault.error("tools/call", code=-32000, message="rate limited"),
])

with use_cassette("cassettes/search.mcp.json", mode="none", faults=overlay) as session:
    cmd = session.server_command(["python", "-m", "my_server"])
    run_my_agent(mcp_servers={"search": {"command": cmd[0], "args": cmd[1:]}})
# finalize() on clean exit: CassetteError on any replay miss, as always
```

`mode="none"` is worth being explicit about here: faults need a replay action (HT-04.5),
and `none` fails loudly if the cassette is missing instead of trying to record.

There is no `with_faults()` step through this door — the session is constructed with the
overlay. To run a matrix, open one `use_cassette` block per fault; two blocks on the
*same* cassette path concurrently is unsupported.

## HT-04.3 From the CLI

Write the overlay to a JSON sidecar and pass it to `serve`:

```json
{
  "faults": [
    {
      "target": { "method": "tools/call", "nth": 1 },
      "type": "error",
      "params": { "code": -32000, "message": "rate limited" }
    }
  ]
}
```

```
mcp-cassette serve demo.json --faults demo.faults.json
```

The flag works for both transports — `serve` infers stdio or HTTP from the cassette and
hands the overlay to whichever replay server it starts. This is the door to use when the
agent under test is configured outside Python (an MCP client config file, a shell script,
another language's test runner): it points at `mcp-cassette serve` like any other server
command or URL.

## HT-04.4 Fault types

| Constructor | Effect |
|---|---|
| `Fault.delay(method, ms, nth=None)` | Sleep `ms` milliseconds, then respond normally. |
| `Fault.timeout(method, nth=None)` | Never respond to the matched request; keep serving others. |
| `Fault.error(method, code=-32603, message="mcp-cassette injected error", nth=None)` | Replace the recorded response with a JSON-RPC error object. |
| `Fault.malformed(method, strategy="truncate", nth=None)` | Emit a corrupted response line. `strategy` is `truncate`, `not_json`, or `wrong_id`. |
| `Fault.disconnect(method, after_response=False, nth=None)` | Close the pipes and exit, simulating server death. |

`method` is the JSON-RPC method the fault targets, e.g. `tools/call`. `nth` restricts the
fault to the nth matching request; omit it to apply to every match.

The constructors are transport-agnostic; what the client observes differs where the wire
differs:

| Fault | Over stdio | Over HTTP |
|---|---|---|
| `timeout` | No response line is written; other requests keep being answered. | Response headers are sent, the body never finishes; other connections keep serving. |
| `malformed` (`truncate`) | A half-serialized line. | Headers, half the body, then the connection is aborted mid-body. |
| `disconnect` | Pipes close and the process exits. | The TCP connection is aborted and the server stops; a live GET stream is closed too. |

`delay` and `error` behave identically on both.

## HT-04.5 The one rule that trips people up

**Faults fire after a request matches.** They live on the response side: the replay
server matches the incoming request against the cassette first, and only then consults
the injector. There is deliberately no fault that corrupts or drops a request on its way
*in* — the request is only ever read, never re-emitted, so there is nothing to corrupt.
To exercise "the call never got an answer", target that method with `timeout` (hang) or
`disconnect` (server dies).

Two consequences:

- A fault on `tools/call` does nothing unless the cassette contains a matching
  `tools/call` exchange for that request.
- On an **unmatched** request the injector is never consulted at all, whether or not a
  fault targeted that method. The client gets a `-32001` unmatched error and the replay
  server exits `3` — the standard replay-miss failure, not a fault. The same holds for a
  request that matched a recorded one carrying no recorded response.

A fault targeting a method the cassette never recorded is otherwise silently inert (a
warning at shutdown, not a failure) — check for it up front with:

```
mcp-cassette inspect demo.json --faults demo.faults.json
```

Expected output:

```
fault overlay dry-run:
  seq 4 tools/call -> error
  WARNING: timeout on resources/read matches nothing
```

The `WARNING` lines are exactly the inert faults.

## HT-04.6 Faults under a recording mode

Through the programmatic doors — `with_faults()` on the fixture session, or `faults=` on
`use_cassette` — an overlay on a session whose mode resolves to anything but `replay`
raises, on both `server_command` and `server_url`:

```
CassetteError: faults apply to replay only; with_faults cannot run under a recording
mode (resolved action: record)
```

`new_episodes` resolves to a recording action too and raises the same way once the
cassette exists (`resolved action: new_episodes`). Record the cassette first (or stop
forcing `MCP_CASSETTE_MODE=all` for that run), then add the fault.

The CLI has no such conflict to raise on: `record` and `serve` are separate commands, and
`--faults` only exists on `serve`. The one edge is `serve --new-episodes --faults`: over
HTTP the overlay still applies to requests that match the cassette (misses fall through
live), while over stdio the overlay is **ignored** — the new-episodes proxy takes no
injector. Don't combine the two flags on a stdio cassette expecting faults.

## HT-04.7 Related

- [HT-03. Use it as a library](HT-03-use-as-a-library.md)
- [HT-05. Replay timing](HT-05-replay-timing.md) — pacing and faults compose
- [OP-04. CLI reference](../operations/OP-04-cli-reference.md)
