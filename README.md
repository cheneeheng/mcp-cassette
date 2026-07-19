# mcp-cassette

vcrpy for MCP. Record real MCP sessions between an agent and an MCP server — local stdio or remote Streamable HTTP — into **cassettes** — structured, diffable, committable files — then replay those cassettes as deterministic mock MCP servers so your agent test suite stops hitting live servers and stops being flaky, slow, and expensive.

mcp-cassette operates at the **transport level** (newline-delimited JSON-RPC over stdio; h11 + hand-rolled SSE framing over Streamable HTTP), treats messages semi-opaquely, and does **not** depend on the official `mcp` SDK at runtime — so it works with any MCP client (Claude Code included) unmodified. Sessions containing server-initiated requests (sampling, elicitation) record and replay too.

## Install

```
uv add mcp-cassette              # or: pip install mcp-cassette
uv add "mcp-cassette[http]"      # remote (Streamable HTTP) record/replay
```

Python ≥ 3.12. Linux, macOS, and Windows supported. The core install depends only on `anyio` and `pydantic`; the `[http]` extra adds `httpx` and `h11`.

## The pytest fixture (the main surface)

```python
def test_agent_summarizes_repo(mcp_cassette):
    cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert "summary" in result
```

First run records through the recording proxy; every run after replays offline, deterministic and fast. The fixture never monkeypatches your agent — it hands you a *command list* to plug into the agent's MCP server configuration.

For a remote server, `server_url` is the drop-in twin (needs the `[http]` extra):

```python
def test_agent_reads_remote_tracker(mcp_cassette):
    url = mcp_cassette.server_url("https://mcp.example.com/mcp")
    result = run_my_agent(mcp_servers={"tracker": {"url": url}})
    assert "triaged" in result
```

First run stands up a local recording proxy in front of the real URL; every run after replays from the cassette on a local mock Streamable HTTP server. Same record modes, same fault matrix. `Authorization` (and every other header) is forwarded upstream but never written to the cassette.

### Record modes

Set via `MCP_CASSETTE_MODE` (env) > `@pytest.mark.mcp_cassette(mode=...)` > `mcp_cassette_mode` ini > default `once`.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail the test | replay |
| `all` | record | re-record |
| `new_episodes` | record | replay; misses fall through to the real server and are appended |

CI should set `MCP_CASSETTE_MODE=none` so no pipeline silently hits a live server.

## Fault injection

One recorded cassette drives a whole resilience matrix:

```python
import mcp_cassette as mcc

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

Fault types: `delay`, `timeout`, `error`, `malformed`, `disconnect`. Faults live in a `FaultOverlay`; the recorded cassette is never mutated.

## CLI

```
mcp-cassette record --cassette demo.json -- python tools/server.py   # wrap a real server
mcp-cassette record --cassette demo.json --url https://mcp.example.com/mcp   # proxy a remote one
mcp-cassette serve demo.json                                         # drop-in replay server (transport inferred)
mcp-cassette serve demo.json --faults demo.faults.json               # replay with faults
mcp-cassette inspect demo.json                                       # per-method counts + timing
mcp-cassette inspect demo.json --faults demo.faults.json             # dry-run: which requests a fault hits
```

A recording is checkpointed to a `<cassette>.partial` sidecar every 5 seconds (`--checkpoint-interval SECONDS`, `0` disables), so a hard kill loses only what arrived since the last checkpoint. The sidecar is a valid cassette — inspect it, rename it over the real path to keep it — and is removed once the recording finalizes normally. It is deliberately never written to the cassette path itself: `once` mode decides record-vs-replay by that file's existence, and a truncated cassette there would silently replay as a finished one.

## Linting your cassettes

Recorded tool descriptions and results are third-party content; lint them in CI before they reach a model:

```
mcp-cassette lint demo-http.json
mcp-cassette lint new.json --baseline tests/cassettes/old.json --format json
```

Rules: `R001` instruction injection in a tool description (error), `R002` description/schema drift vs a baseline — the "rug pull" (error), `R003` duplicate tool names (warning), `R004` instruction-shaped tool results (warning). Exit `0` = no error-severity findings, `4` = at least one. Each finding carries a JSON-pointer locator into the cassette.

These are heuristic pattern rules, not a guarantee — a clean lint is absence of *known* smells, nothing more.

## License

See [LICENSE](LICENSE).
