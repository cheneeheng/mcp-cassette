# HT-04. Inject faults

**When:** you want to test how your agent behaves when the MCP server is slow, errors,
returns garbage, or dies — without breaking a real server.
**Prerequisites:** a recorded cassette for the test. Faults are **replay-only**.

One recorded cassette drives a whole resilience matrix. The cassette is never mutated:
faults live in a separate `FaultOverlay`, either built in test code or loaded from a
`<cassette>.faults.json` sidecar. Same `Injector`, same hook point, same behavior through
every door and over both transports.

| Door | Section | Read it if |
|---|---|---|
| pytest fixture | [HT-04.1](#ht-041-with-the-pytest-fixture) | your tests are a pytest suite |
| library (`use_cassette`) | [HT-04.2](#ht-042-with-use_cassette) | your harness is a notebook, benchmark, or another test framework |
| CLI | [HT-04.3](#ht-043-with-the-cli) | the agent under test is configured outside Python |

[HT-04.4](#ht-044-behaviour-shared-by-all-three-doors) holds the fault types, the one rule
that trips people up, and what happens if you point a fault at a recording run.

## HT-04.1 With the pytest fixture

1. Derive a faulted session from the fixture and use it exactly as you would the fixture
   itself.

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

2. Run the test. Each parametrized case replays the same cassette under a different fault.

`with_faults()` returns a **new** `CassetteSession`, so parametrized tests never share
state. Pass several faults in one call to combine them. The derived session is registered
on the one the fixture handed you, so a fault test's replay misses fail the test exactly as
an ordinary one's do.

For a remote server, swap the last two lines — the derived session is the same object
either way:

```python
    url = session.server_url("https://mcp.example.com/mcp")
    result = run_my_agent(mcp_servers={"github": {"url": url}})
```

**Verify:** the agent takes its degraded path and the test still passes offline.

## HT-04.2 With `use_cassette`

1. Build the overlay and pass it to the block. There is no `with_faults()` step through
   this door — the session is constructed with the overlay.

   ```python
   from mcp_cassette import Fault, FaultOverlay, use_cassette

   overlay = FaultOverlay(faults=[
       Fault.error("tools/call", code=-32000, message="rate limited"),
   ])

   with use_cassette("cassettes/search.mcp.json", mode="none", faults=overlay) as session:
       cmd = session.server_command(["python", "-m", "my_server"])
       run_my_agent(mcp_servers={"search": {"command": cmd[0], "args": cmd[1:]}})
   ```

2. To run a matrix, open one block per fault. Two blocks on the *same* cassette path
   concurrently is unsupported.

`mode="none"` is worth being explicit about here: faults need a replay action
([HT-04.4](#faults-under-a-recording-mode)), and `none` fails loudly if the cassette is
missing instead of trying to record.

**Verify:** `finalize()` on clean exit raises `CassetteError` on any replay miss, as always.

## HT-04.3 With the CLI

1. Write the overlay to a JSON sidecar:

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

2. Dry-run it before you rely on it:

   ```
   mcp-cassette inspect demo.mcp.json --faults demo.faults.json
   ```

   ```
   fault overlay dry-run:
     seq 4 tools/call -> error
     WARNING: timeout on resources/read matches nothing
   ```

   `WARNING` lines are inert faults — they target something the cassette never recorded.

3. Point the agent's configuration at `serve` with the sidecar:

   ```
   mcp-cassette serve demo.mcp.json --faults demo.faults.json
   ```

`--faults` works for both transports: `serve` infers stdio or HTTP from the cassette and
hands the overlay to whichever replay server it starts. This is the door to use when the
agent under test is configured outside Python — an MCP client config file, a shell script,
another language's test runner.

**Verify:** the dry-run in step 2 lists the exchange you meant to hit, with no `WARNING`.

## HT-04.4 Behaviour shared by all three doors

### Fault types

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

### The one rule that trips people up

**Faults fire after a request matches.** They live on the response side: the replay server
matches the incoming request against the cassette first, and only then consults the
injector. There is deliberately no fault that corrupts or drops a request on its way *in* —
the request is only ever read, never re-emitted, so there is nothing to corrupt. To
exercise "the call never got an answer", target that method with `timeout` (hang) or
`disconnect` (server dies).

Two consequences:

- A fault on `tools/call` does nothing unless the cassette contains a matching `tools/call`
  exchange for that request.
- On an **unmatched** request the injector is never consulted at all, whether or not a
  fault targeted that method. The client gets a `-32001` unmatched error and the replay
  server exits `3` — the standard replay-miss failure, not a fault. The same holds for a
  request that matched a recorded one carrying no recorded response.

A fault targeting a method the cassette never recorded is otherwise silently inert (a
warning at shutdown, not a failure). The dry-run in [HT-04.3](#ht-043-with-the-cli) is how
you catch that up front, whichever door you inject through.

### Faults under a recording mode

Through the programmatic doors — `with_faults()` on the fixture session, or `faults=` on
`use_cassette` — an overlay on a session whose mode resolves to anything but `replay`
raises, on both `server_command` and `server_url`:

```
CassetteError: faults apply to replay only; with_faults cannot run under a recording
mode (resolved action: record)
```

`new_episodes` resolves to a recording action too and raises the same way once the cassette
exists (`resolved action: new_episodes`). Record the cassette first — or stop forcing
`MCP_CASSETTE_MODE=all` for that run — then add the fault.

The CLI enforces the same rule as a usage error. `record` and `serve` are separate commands
and `--faults` only exists on `serve`, so the only conflict left is
`serve --new-episodes --faults`, which exits `2` on both transports:

```
mcp-cassette serve: --faults applies to replay only; --new-episodes records novel
exchanges live
```

The reason is not tidiness: a fault changes the path the agent takes, so injecting under
`new_episodes` would append a session that never happens without the fault. Run the matrix
against the finished cassette, in plain `serve`, once `new_episodes` has appended what it
needed to.

## HT-04.5 Related

- [HT-03. Use it as a library](HT-03-use-as-a-library.md) — the full `use_cassette` surface.
- [HT-05. Replay timing](HT-05-replay-timing.md) — pacing and faults compose; order is
  pace, then fault.
- [OP-04. CLI reference](../operations/OP-04-cli-reference.md) — every flag.
