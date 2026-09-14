# HT-03. Use it as a library

**When:** your agent harness is not a pytest suite — a notebook, a benchmark runner, a
CLI of your own, or a different test framework.
**Prerequisites:** `mcp-cassette` installed (plus the `[http]` extra for HTTP servers).

The pytest fixture and the CLI are two front doors onto the same machinery. `use_cassette`
is the third: a context manager that hands you a `CassetteSession` with the same modes, the
same fault matrix, and the same failure semantics. `use_cassette_async` is the fourth, its
twin for async code ([HT-03.9](#ht-039-async-code-use_cassette_async)).

This chapter is the **reference for that door**. Each task chapter already shows it
alongside the other two — come here for the details they do not repeat.

| If you want to | Start at | This chapter adds |
|---|---|---|
| record/replay a local command | [HT-01.2](HT-01-record-and-replay.md#ht-012-with-use_cassette) | modes, raising behaviour, the report sidecar |
| record/replay a remote URL | [HT-02.2](HT-02-remote-http.md#ht-022-with-use_cassette) | why HTTP hands you a server, not a command |
| inject faults | [HT-04.2](HT-04-inject-faults.md#ht-042-with-use_cassette) | how `faults=` composes with `match` and `pace` |
| replay recorded timing | [HT-05.2](HT-05-replay-timing.md#ht-052-with-use_cassette) | — |

## HT-03.1 stdio: command substitution

```python
from mcp_cassette import use_cassette

with use_cassette("cassettes/search.mcp.json", mode="once") as session:
    cmd = session.server_command(["python", "-m", "my_server"])
    run_my_agent(mcp_servers={"search": {"command": cmd[0], "args": cmd[1:]}})
```

On the first run the returned command is a recording proxy wrapping your real server; on
every run after it is `mcp-cassette serve` replaying the cassette. Nothing about your
agent changes — only which command it launches.

**Verify:** the cassette file exists after the first run, and the second run works with
the real server stopped.

## HT-03.2 Streamable HTTP: URL substitution

```python
with use_cassette("cassettes/remote.mcp.json", mode="once") as session:
    url = session.server_url("https://mcp.example.com/mcp")
    run_my_agent(mcp_servers={"remote": {"url": url}})
```

`server_url` starts a server in *this* process on a background thread bound to
`127.0.0.1` on an ephemeral port. It is stopped when the block exits.

**Verify:** `mcp-cassette inspect cassettes/remote.mcp.json` reports `transport: http`
after the first run, and the second run completes with the real endpoint unreachable —
point at `https://dead.invalid/mcp` to prove it.

## HT-03.3 The one asymmetry, stated up front

For stdio you get a **command list**, not an in-process server. An MCP stdio server *is*
a program the client launches; the only seam is which command it launches. HTTP is the
opposite: an HTTP config carries no command at all, so something must already be
listening before the agent connects — running it ourselves is the minimum, not a
preference.

## HT-03.4 Modes and precedence

Precedence, highest first: `MCP_CASSETTE_MODE` (env) → `mode=` argument → default
`once`. The environment stays the top tier so the CI invariant holds through this door
too: with `MCP_CASSETTE_MODE=none`, a harness that hard-codes `mode="all"` still cannot
record.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail — recording is forbidden | replay |
| `all` | record | re-record |
| `new_episodes` | record | replay; misses fall through to the real server and are appended |

Through this door, "fail" means `finalize()` raises `CassetteError` for the missing
cassette.

`resolve_mode()` is exported if you want the resolved value without opening a session.
An unknown mode raises `ValueError` naming the bad value, its source (`env
MCP_CASSETTE_MODE` or `mode=` argument), and the four valid modes.

## HT-03.5 What the block raises, and when

A clean exit calls `finalize()`, which raises `CassetteError` if:

- a recording captured zero messages (the agent never spoke to the proxied server), or
- replay hit any unmatched request (the message lists every miss).

If the `with` body raises, the session is closed — no thread or socket leaks — and **your**
exception propagates untouched. Report checks are skipped deliberately: a replay miss is
usually a consequence of the real failure, and chaining it on top buries the cause.

## HT-03.5.1 What the block writes, and how to undo it

| When | Writes | Undo |
|---|---|---|
| the run that records (`once` with no cassette yet, or `all`) | the cassette path you passed to `use_cassette(...)`, plus a `<cassette>.partial` sidecar while the session runs, removed on a clean finish | delete the cassette file |
| every run that replays | nothing — replay only reads the cassette | nothing to undo |

Nothing else on disk changes: the session report goes to a temp directory that is removed
on exit (HT-03.6), and faults never touch the recording (HT-03.7).

To re-record, delete the cassette and run again — `once` records whenever the file is
absent, so deleting it is the whole reset. Copy it first if you might want the old one
back; nothing keeps a previous version for you.

## HT-03.6 The report sidecar goes to a temp directory

Unlike the fixture (which passes pytest's `tmp_path`), `use_cassette` creates a
`TemporaryDirectory` for the session report and removes it on exit — so you never find
untracked JSON next to cassettes you commit. Pass `report_path=` to opt into a durable
file. The faults sidecar derives from the report's directory and is cleaned up with it.

## HT-03.7 Faults, matching, and pacing

Every knob the fixture has is a keyword argument:

```python
from mcp_cassette import Fault, FaultOverlay, MatchConfig, PaceConfig, use_cassette

with use_cassette(
    "cassettes/search.mcp.json",
    mode="none",
    match=MatchConfig(ordering="strict"),
    faults=FaultOverlay(faults=[Fault.timeout("tools/call", nth=1)]),
    pace=PaceConfig(mode="recorded", scale=0.2),
) as session:
    ...
```

## HT-03.8 Limits worth knowing

- **Nesting is allowed, and sharing is detected.** Two blocks may be open at once for two
  different cassettes (two MCP servers in one agent). Two sessions *writing* the same
  cassette path are detected rather than merely documented: the recording session holds a
  claim, so the second waits under `once` and raises `CassetteError` under `all` or
  `new_episodes`. Replaying one cassette from many sessions is always safe. See
  [OP-06](../operations/OP-06-parallel-test-runs.md).
- **The sync door refuses a running event loop.** Entering `use_cassette` on an event-loop
  thread raises `RuntimeError` naming `use_cassette_async`, because its blocking portal
  would deadlock there. From a worker thread it still works.

## HT-03.9 Async code: `use_cassette_async`

```python
from mcp_cassette import use_cassette_async

async def test_agent_reads_tracker():
    async with use_cassette_async("cassettes/tracker.mcp.json") as session:
        url = session.server_url("https://mcp.example.com/mcp")
        result = await run_my_agent(mcp_servers={"tracker": {"url": url}})
    assert "triaged" in result
```

It takes the same arguments as `use_cassette` and resolves modes the same way. What
differs is where the HTTP server runs:

- **No portal thread.** The sync door runs the server on a background thread. Here
  `server_url` starts it as a task in the caller's own event loop, so there is no thread
  hop in either direction, a debugger steps straight into the server, and shutdown joins
  nothing. A second `server_url` call returns the same URL.
- **asyncio and trio both work.** Everything underneath is anyio.
- **Cleanup is shielded from cancellation.** If the task running the block is cancelled,
  the server still stops and the write claim is still released, so the next run does not
  find a stale claim. A claim wait uses `anyio.sleep`, never blocking your loop.
- **Failures read the same.** A clean exit calls `afinalize()` and raises `CassetteError`
  on an empty recording or a replay miss. A raising body calls `aclose()` and your
  exception propagates unwrapped.
- **stdio is unchanged.** `server_command` still returns a command list, for the reason in
  [HT-03.3](#ht-033-the-one-asymmetry-stated-up-front): a stdio server is a program the
  client launches. Async does not change that.

`examples/library_mode_async.py` is runnable from a clone.

**Verify:** run the example twice. The first run records, the second replays with the echo
server stopped.

## HT-03.10 Related

- [HT-01. Record and replay a stdio server](HT-01-record-and-replay.md)
- [HT-02. Record and replay a remote HTTP server](HT-02-remote-http.md)
- [HT-05. Replay timing](HT-05-replay-timing.md)
