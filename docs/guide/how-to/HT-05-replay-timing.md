# HT-05. Replay recorded timing

**When:** your agent's behavior depends on *how long* the server takes — timeout handling,
progress-notification UX, concurrency assumptions, retry/backoff logic.
**Prerequisites:** a recorded cassette. Pacing is replay-only.

Every recorded message carries `t_offset_ms`, milliseconds from proxy start on a monotonic
clock. Replay ignores it by default: responses come back instantly. That is the right
default — fast, deterministic suites — and it stays the default. Pacing is the opt-in that
replays those recorded gaps.

| Door | Section | Read it if |
|---|---|---|
| pytest fixture | [HT-05.1](#ht-051-with-the-pytest-fixture) | your tests are a pytest suite |
| library (`use_cassette`) | [HT-05.2](#ht-052-with-use_cassette) | your harness is a notebook, benchmark, or another test framework |
| CLI | [HT-05.3](#ht-053-with-the-cli) | you drive replay by hand or from a shell script |

[HT-05.4](#ht-054-behaviour-shared-by-all-three-doors) holds what pacing does and does not
replay, why the cap defaults to 5 seconds, and how pacing composes with faults.

## HT-05.1 With the pytest fixture

1. Add the pacing keywords to the marker.

   ```python
   @pytest.mark.mcp_cassette(pace="recorded", pace_scale=0.2, pace_cap_ms=1000)
   def test_agent_handles_slow_tools(mcp_cassette):
       cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
       assert run_my_agent(mcp_servers={"github": cmd}).completed
   ```

   `pace_scale=0.2` replays every gap five times faster; `pace_cap_ms=1000` clamps any
   single gap to one second.

2. Run the test.

**Verify:** the test takes measurably longer than the unpaced run, and your agent's timeout
branch is reached — or provably not reached — instead of being skipped by instant
responses.

## HT-05.2 With `use_cassette`

1. Pass a `PaceConfig`.

   ```python
   from mcp_cassette import PaceConfig, use_cassette

   with use_cassette(
       "cassettes/github.mcp.json",
       pace=PaceConfig(mode="recorded", scale=0.2, cap_ms=1000),
   ) as session:
       cmd = session.server_command(["python", "tools/github_server.py"])
       run_my_agent(mcp_servers={"github": {"command": cmd[0], "args": cmd[1:]}})
   ```

2. Run your harness.

**Verify:** same as the fixture — wall-clock time rises to roughly the recorded span times
your scale, bounded by the cap.

## HT-05.3 With the CLI

```
mcp-cassette serve examples/cassettes/echo_and_add.mcp.json --pace recorded
mcp-cassette serve examples/cassettes/echo_and_add.mcp.json --pace recorded --pace-scale 0.2   # 5x faster
mcp-cassette serve examples/cassettes/echo_and_add.mcp.json --pace recorded --pace-cap-ms 0    # uncapped
```

`--pace-scale` must be greater than zero; `0` would be indistinguishable from `--pace none`
but reads as a mistake, so it is rejected. `--pace-scale` or `--pace-cap-ms` without
`--pace recorded` exits `2` rather than being silently ignored.

**Verify:** time the run against the same command without `--pace recorded`.

## HT-05.4 Behaviour shared by all three doors

### What pacing replays

| | |
|---|---|
| Replays | The gap between two adjacent recorded messages, applied as the later one is emitted. |
| Never replays | The absolute recorded timeline. That would need the client to send requests at recorded times — it will not. |
| On stdio | Response, anchored notifications, and server-initiated requests. |
| On HTTP | The same, plus **SSE inter-event spacing** — the highest-fidelity thing pacing buys. |
| Under `new_episodes` | Replayed hits only. Fall-through misses go to the real server and are inherently live-timed. |

Negative or missing gaps become zero, silently. Concurrent HTTP exchanges can interleave
such that a response's recorded offset precedes its request's; zero means "as fast as
possible", which is exactly the pre-pacing behavior for that pair.

### Why the cap defaults to 5000 ms

A cassette recorded interactively can easily contain a 40-second human pause between calls.
Replaying that verbatim by default would turn one opt-in flag into a hung CI job. Five
seconds per gap is long enough to exercise realistic timeout logic and short enough never
to look like a hang. Opt into uncapped replay explicitly with `pace_cap_ms=0` (or
`--pace-cap-ms 0`).

### Pacing and faults compose

Order at each emission point is **pace, then fault**.

| Fault | With pacing on |
|---|---|
| `delay` | Additive — recorded 800 ms + injected 2000 ms = 2800 ms. "The server was already slow, then got slower." |
| `timeout` | No pacing sleep is spent; the silence starts immediately. |
| `disconnect` | Paces first, then drops — the realistic shape. |
| `error`, `malformed` | Paced normally. |

Pacing covers **realistic** latency (what the real server did); faults cover
**pathological** latency (what you want to prove your agent survives). They are different
tools; use both.

### The invariant this bends, deliberately

The standing promise is *no network, no subprocess, no wall-clock reads in the response
path*. With pacing off — the default everywhere — that still holds exactly: the pacer
returns without sleeping and without reading a clock. Turning pacing on trades that
determinism for recorded-latency fidelity, by design.

## HT-05.5 Related

- [HT-04. Inject faults](HT-04-inject-faults.md) — pathological latency, and the
  composition rules above.
- [OP-04. CLI reference](../operations/OP-04-cli-reference.md) — every flag.
