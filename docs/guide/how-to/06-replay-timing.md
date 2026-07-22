# 6. Replay recorded timing

**When:** your agent's behavior depends on *how long* the server takes — timeout
handling, progress-notification UX, concurrency assumptions, retry/backoff logic.
**Prerequisites:** a recorded cassette. Pacing is replay-only.

Every recorded message carries `t_offset_ms`, milliseconds from proxy start on a
monotonic clock. Replay has always ignored it: responses come back instantly. That is the
right default — fast, deterministic suites — and it stays the default.

Pacing is the opt-in that replays those recorded gaps.

## 6.1 Turn it on

```bash
mcp-cassette serve demo.mcp.json --pace recorded
mcp-cassette serve demo.mcp.json --pace recorded --pace-scale 0.2   # 5x faster
mcp-cassette serve demo.mcp.json --pace recorded --pace-cap-ms 0    # uncapped
```

```python
@pytest.mark.mcp_cassette(pace="recorded", pace_scale=0.2, pace_cap_ms=1000)
def test_agent_handles_slow_tools(mcp_cassette):
    ...
```

```python
from mcp_cassette import PaceConfig, use_cassette

with use_cassette("c.mcp.json", pace=PaceConfig(mode="recorded", scale=0.2)) as session:
    ...
```

**Verify:** the test takes measurably longer, and your agent's timeout branch is reached
(or provably not reached) instead of being skipped by instant responses.

## 6.2 What it does and does not do

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

## 6.3 Why `--pace-cap-ms` defaults to 5000

A cassette recorded interactively can easily contain a 40-second human pause between
calls. Replaying that verbatim by default would turn one opt-in flag into a hung CI job.
Five seconds per gap is long enough to exercise realistic timeout logic and short enough
never to look like a hang. `--pace-cap-ms 0` opts into uncapped replay explicitly.

`--pace-scale` must be greater than zero; `0` would be indistinguishable from
`--pace none` but reads as a mistake, so it is rejected. `--pace-scale` or
`--pace-cap-ms` without `--pace recorded` exits 2 rather than being silently ignored.

## 6.4 Pacing and faults compose

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

## 6.5 The invariant this bends, deliberately

The standing promise is *no network, no subprocess, no wall-clock reads in the response
path*. With pacing off — the default everywhere — that still holds exactly: the pacer
returns without sleeping and without reading a clock. Turning pacing on trades that
determinism for recorded-latency fidelity, by design.

## 6.6 Related

- [5. Inject faults](05-inject-faults.md)
- [14. CLI reference](../operations/14-cli-reference.md)
