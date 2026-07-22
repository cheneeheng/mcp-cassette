# 4. Use it as a library

**When:** your agent harness is not a pytest suite — a notebook, a benchmark runner, a
CLI of your own, or a different test framework.
**Prerequisites:** `mcp-cassette` installed (plus the `[http]` extra for HTTP servers).

The pytest fixture and the CLI are two front doors onto the same machinery. `use_cassette`
is the third: a context manager that hands you a `CassetteSession` with the same modes,
the same fault matrix, and the same failure semantics.

## 4.1 stdio: command substitution

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

## 4.2 Streamable HTTP: URL substitution

```python
with use_cassette("cassettes/remote.mcp.json", mode="once") as session:
    url = session.server_url("https://mcp.example.com/mcp")
    run_my_agent(mcp_servers={"remote": {"url": url}})
```

`server_url` starts a server in *this* process on a background thread bound to
`127.0.0.1` on an ephemeral port. It is stopped when the block exits.

## 4.3 The one asymmetry, stated up front

For stdio you get a **command list**, not an in-process server. An MCP stdio server *is*
a program the client launches; the only seam is which command it launches. HTTP is the
opposite: an HTTP config carries no command at all, so something must already be
listening before the agent connects — running it ourselves is the minimum, not a
preference.

## 4.4 Modes and precedence

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

## 4.5 What the block raises, and when

A clean exit calls `finalize()`, which raises `CassetteError` if:

- a recording captured zero messages (the agent never spoke to the proxied server), or
- replay hit any unmatched request (the message lists every miss).

If the `with` body raises, the session is closed — no thread or socket leaks — and **your**
exception propagates untouched. Report checks are skipped deliberately: a replay miss is
usually a consequence of the real failure, and chaining it on top buries the cause.

## 4.6 The report sidecar goes to a temp directory

Unlike the fixture (which passes pytest's `tmp_path`), `use_cassette` creates a
`TemporaryDirectory` for the session report and removes it on exit — so you never find
untracked JSON next to cassettes you commit. Pass `report_path=` to opt into a durable
file. The faults sidecar derives from the report's directory and is cleaned up with it.

## 4.7 Faults, matching, and pacing

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

## 4.8 Limits worth knowing

- **Nesting is allowed, sharing is not.** Two blocks may be open at once for two
  different cassettes (two MCP servers in one agent). Two sessions on the *same* cassette
  path concurrently is unsupported and undetected — it surfaces immediately as a miss.
- **No async entry point yet.** There is no `use_cassette_async`. The blocking portal
  works from async callers as long as the block is entered from a thread that is not the
  event loop.

## 4.9 Related

- [2. Record and replay a stdio server](02-record-and-replay.md)
- [3. Record and replay a remote HTTP server](03-remote-http.md)
- [6. Replay timing](06-replay-timing.md)
