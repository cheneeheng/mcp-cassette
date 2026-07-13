# mcp-cassette

vcrpy for MCP. Record real MCP stdio sessions between an agent and an MCP server into **cassettes** — structured, diffable, committable files — then replay those cassettes as deterministic mock MCP servers so your agent test suite stops hitting live servers and stops being flaky, slow, and expensive.

mcp-cassette operates at the **transport level** (newline-delimited JSON-RPC over stdio), treats messages semi-opaquely, and does **not** depend on the official `mcp` SDK at runtime — so it works with any MCP client (Claude Code included) unmodified.

## Install

```
uv add mcp-cassette        # or: pip install mcp-cassette
```

Python ≥ 3.10. Linux, macOS, and Windows supported.

## The pytest fixture (the main surface)

```python
def test_agent_summarizes_repo(mcp_cassette):
    cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert "summary" in result
```

First run records through the recording proxy; every run after replays offline, deterministic and fast. The fixture never monkeypatches your agent — it hands you a *command list* to plug into the agent's MCP server configuration.

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
mcp-cassette serve demo.json                                         # drop-in replay server
mcp-cassette serve demo.json --faults demo.faults.json               # replay with faults
mcp-cassette inspect demo.json                                       # per-method counts + timing
mcp-cassette inspect demo.json --faults demo.faults.json             # dry-run: which requests a fault hits
```

## License

See [LICENSE](LICENSE).
