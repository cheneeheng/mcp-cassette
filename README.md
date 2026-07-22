# mcp-cassette

vcrpy for MCP. Record real MCP sessions between an agent and an MCP server — local stdio or remote Streamable HTTP — into **cassettes** — structured, diffable, committable files — then replay those cassettes as deterministic mock MCP servers so your agent test suite stops hitting live servers and stops being flaky, slow, and expensive.

mcp-cassette operates at the **transport level** (newline-delimited JSON-RPC over stdio; h11 + hand-rolled SSE framing over Streamable HTTP), treats messages semi-opaquely, and does **not** depend on the official `mcp` SDK at runtime — so it works with any MCP client (Claude Code included) unmodified. Sessions containing server-initiated requests (sampling, elicitation) record and replay too.

Full documentation: **[docs/guide/](docs/guide/index.md)** — 15 numbered chapters in
reading order: getting started and how-to guides for test authors (1–10), then
installation, configuration, CI, CLI reference, and the runbook for operators (11–15).
The sections below summarize; each ends with a pointer to its full chapter.

## 1. Install

```
uv add mcp-cassette              # or: pip install mcp-cassette
uv add "mcp-cassette[http]"      # remote (Streamable HTTP) record/replay
```

Python ≥ 3.12. Linux, macOS, and Windows supported. The core install depends only on `anyio` and `pydantic`; the `[http]` extra adds `httpx` and `h11`.

Full chapter: [11. Installation](docs/guide/operations/11-install.md).

## 2. The pytest fixture (the main surface)

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

Full chapters: [2. Record and replay a stdio server](docs/guide/how-to/02-record-and-replay.md), [3. Record and replay a remote HTTP server](docs/guide/how-to/03-remote-http.md).

### 2.1 Record modes

Precedence, highest first: `MCP_CASSETTE_MODE` (env) → marker `mode=` → `mcp_cassette_mode` (ini) → default `once`.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail — recording is forbidden | replay |
| `all` | record | re-record |
| `new_episodes` | record | replay; misses fall through to the real server and are appended |

CI should set `MCP_CASSETTE_MODE=none` so no pipeline silently hits a live server.

Full chapters: [12. Configuration](docs/guide/operations/12-configure.md), [13. CI pipeline](docs/guide/operations/13-ci.md).

## 3. Use it as a library

Not a pytest suite? `use_cassette` is the same machinery behind a context manager — same modes, same fault matrix, same failure semantics:

```python
from mcp_cassette import use_cassette

with use_cassette("cassettes/search.mcp.json", mode="once") as session:
    cmd = session.server_command(["python", "-m", "my_server"])
    run_my_agent(mcp_servers={"search": {"command": cmd[0], "args": cmd[1:]}})
# clean exit -> finalize(): background server stopped, report checked,
#               CassetteError raised on an empty recording or any replay miss
```

Precedence, highest first: `MCP_CASSETTE_MODE` (env) → `mode=` argument → default `once` — so the CI invariant holds through this door too. The session report goes to a temp directory that is removed on exit — no untracked JSON next to cassettes you commit. `examples/library_mode.py` is runnable.

Full chapter: [4. Use it as a library](docs/guide/how-to/04-use-as-a-library.md).

## 4. Fault injection

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

Full chapter: [5. Inject faults](docs/guide/how-to/05-inject-faults.md).

## 5. Replay timing

Replay is instant by default. When your agent's timeout, progress-stream, or retry logic depends on *how long* the server took, replay the recorded gaps instead:

```
mcp-cassette serve demo.json --pace recorded                     # recorded latency
mcp-cassette serve demo.json --pace recorded --pace-scale 0.2    # 5x faster
```

Also `@pytest.mark.mcp_cassette(pace="recorded", pace_scale=0.2)` and `use_cassette(..., pace=PaceConfig(mode="recorded"))`. Per-gap cap defaults to 5000 ms so one pathological recorded pause cannot look like a hung job; `--pace-cap-ms 0` opts into uncapped. A `delay` fault stacks on top of recorded latency.

Full chapter: [6. Replay timing](docs/guide/how-to/06-replay-timing.md).

## 6. The CLI

```
mcp-cassette record --cassette demo.json -- python tools/server.py   # wrap a real server
mcp-cassette record --cassette demo.json --url https://mcp.example.com/mcp   # proxy a remote one
mcp-cassette serve demo.json                                         # drop-in replay server (transport inferred)
mcp-cassette serve demo.json --faults demo.faults.json               # replay with faults
mcp-cassette inspect demo.json                                       # per-method counts + timing
mcp-cassette inspect demo.json --timeline --grep 'tools/call'        # message timeline, payload-grepped
mcp-cassette inspect demo.json --format json > summary.json          # deterministic, diffable
mcp-cassette inspect demo.json --faults demo.faults.json             # dry-run: which requests a fault hits
mcp-cassette diff old.json new.json --tools-only                     # exit 5 when the server surface moved
```

A recording is checkpointed to a `<cassette>.partial` sidecar every 5 seconds (`--checkpoint-interval SECONDS`, `0` disables), so a hard kill loses only what arrived since the last checkpoint. The sidecar is a valid cassette — see [§12.6 Checkpointing](docs/guide/operations/12-configure.md#126-checkpointing) for recovery and why it is never written to the cassette path itself.

Full chapter: [14. CLI reference](docs/guide/operations/14-cli-reference.md).

## 7. Redaction

Cassettes are verbatim transcripts, and you commit them — so redaction runs at capture time, on a deep copy, with defaults always on: values under keys matching `*token*`, `*secret*`, `*password*`, `*apikey*`, `*api_key*`, or `authorization` are replaced with `REDACTED` before the cassette is written. Add your own rules with `--redact` (key-glob or JSON pointer). Read every new cassette before its first commit anyway.

Full chapter: [8. Redact secrets](docs/guide/how-to/08-redact-secrets.md).

## 8. Linting your cassettes

Recorded tool descriptions and results are third-party content; lint them in CI before they reach a model:

```
mcp-cassette lint demo-http.json
mcp-cassette lint new.json --baseline tests/cassettes/old.json --format json
```

Rules: `R001` instruction injection in a tool description (error), `R002` description/schema drift vs a baseline — the "rug pull" (error), `R003` duplicate tool names (warning), `R004` instruction-shaped tool results (warning). Exit `0` = no error-severity findings, `4` = at least one. Each finding carries a JSON-pointer locator into the cassette.

Bring your own rules with a declarative TOML pattern pack — no Python plugin API, deliberately, because `lint` should never execute third-party code on a supply-chain-security surface:

```
mcp-cassette lint demo.json --pattern-pack examples/lint-pack.toml
mcp-cassette lint demo.json --fail-on warning
```

`[tool.mcp_cassette.lint]` in `pyproject.toml` makes your packs, selection, and failure threshold the default for every invocation, so the CI command stays generic. Packs extend the bundled rules; they never replace them.

> Heuristic pattern rules, not a guarantee — a clean lint is the absence of *known* smells, nothing more.

Full chapter: [9. Lint with your own pattern packs](docs/guide/how-to/09-lint-pattern-packs.md).

## 9. License

See [LICENSE](LICENSE).
