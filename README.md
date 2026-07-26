# mcp-cassette

[![CI](https://github.com/cheneeheng/mcp-cassette/actions/workflows/ci.yml/badge.svg)](https://github.com/cheneeheng/mcp-cassette/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-cassette.svg)](https://pypi.org/project/mcp-cassette/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-cassette.svg)](https://pypi.org/project/mcp-cassette/)
[![License](https://img.shields.io/pypi/l/mcp-cassette.svg)](https://github.com/cheneeheng/mcp-cassette/blob/main/LICENSE)

Record/replay testing for MCP (Model Context Protocol) agents: capture real sessions as cassettes, replay them as deterministic mock servers — vcrpy for MCP.

A **cassette** records the entire session between your agent and a real MCP server — local stdio or remote Streamable HTTP — as a structured, diffable, committable file. Replayed, it stands in for the live server, so your agent test suite stops hitting live servers and stops being flaky, slow, and expensive.

mcp-cassette operates at the **transport level** (newline-delimited JSON-RPC over stdio; h11 + hand-rolled SSE framing over Streamable HTTP), treats messages semi-opaquely, and does **not** depend on the official `mcp` SDK at runtime — so it works with any MCP client (Claude Code included) unmodified. Sessions containing server-initiated requests (sampling, elicitation) record and replay too.

Full documentation: **[docs/guide/](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/index.md)** — chapters in
reading order: getting started and how-to guides for test authors (`GS`, `HT`, `TS`),
then installation, configuration, CI, CLI reference, and the runbook for operators (`OP`).
The sections below summarize; each ends with a pointer to its full chapter.

## 1. Install

```
uv add mcp-cassette              # or: pip install mcp-cassette
uv add "mcp-cassette[http]"      # remote (Streamable HTTP) record/replay
```

Python ≥ 3.12. Linux, macOS, and Windows supported. The core install depends only on `anyio` and `pydantic`; the `[http]` extra adds `httpx` and `h11`.

Full chapter: [OP-01. Installation](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-01-install.md).

## 2. The three front doors

One machinery, three ways in: the **pytest fixture**, the **`use_cassette` library door**, and the **CLI**. Same cassette format, same record modes (§3), same fault matrix (§5), same failure semantics — they differ only in who drives the session and where the cassette path comes from.

### 2.1 The pytest fixture (the main surface)

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

Full chapters: [HT-01. Record and replay a stdio server](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-01-record-and-replay.md), [HT-02. Record and replay a remote HTTP server](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-02-remote-http.md).

### 2.2 Use it as a library

Not a pytest suite? `use_cassette` is the same machinery behind a context manager — same modes, same fault matrix, same failure semantics:

```python
from mcp_cassette import use_cassette

with use_cassette("cassettes/search.mcp.json", mode="once") as session:
    cmd = session.server_command(["python", "-m", "my_server"])
    run_my_agent(mcp_servers={"search": {"command": cmd[0], "args": cmd[1:]}})
# clean exit -> finalize(): background server stopped, report checked,
#               CassetteError raised on an empty recording or any replay miss
```

The session report goes to a temp directory that is removed on exit — no untracked JSON next to cassettes you commit. `examples/library_mode.py` is runnable.

Full chapter: [HT-03. Use it as a library](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-03-use-as-a-library.md).

### 2.3 The CLI

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

A recording is checkpointed to a `<cassette>.partial` sidecar every 5 seconds (`--checkpoint-interval SECONDS`, `0` disables), so a hard kill loses only what arrived since the last checkpoint. The sidecar is a valid cassette — see [§OP-02.6 Checkpointing](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-02-configure.md#op-026-checkpointing) for recovery and why it is never written to the cassette path itself.

`inspect`, `lint` (§8.1), and `diff` (§8.2) are CLI-only workflows in practice, but not CLI-only code: `lint_cassette` and `diff_cassettes` are exported from the package for scripted gates.

Full chapter: [OP-04. CLI reference](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-04-cli-reference.md).

## 3. Record modes

The mode decides, once per test run, whether that run records or replays; the recording unit is always the entire session — every message from server launch to session end — never an individual tool call.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail — recording is forbidden | replay |
| `all` | record | re-record |
| `new_episodes` | record | replay; misses fall through to the real server and are appended |

How each door selects a mode, highest precedence first:

| Door | Selection |
|---|---|
| pytest fixture | `MCP_CASSETTE_MODE` (env) → marker `mode=` → `mcp_cassette_mode` (ini) → default `once` |
| `use_cassette` | `MCP_CASSETTE_MODE` (env) → `mode=` argument → default `once` |
| CLI | explicit by command: `record` records, `serve` replays, `serve --new-episodes` appends misses |

CI should set `MCP_CASSETTE_MODE=none` so no pipeline silently hits a live server — the env var wins through both programmatic doors, and the CLI has no door that records by accident.

Cassette paths come from the marker's `cassette=`, else `mcp_cassette_dir` (ini), else `<rootpath>/tests/cassettes`; `pytest -o mcp_cassette_dir=...` overrides it for one invocation. That setting is fixture-only, and deliberately has no env var — the fixture is the one door that *derives* a path from the test name. The CLI and `use_cassette` take the full path from you, so you compose the directory into it yourself.

Full chapter: [OP-02. Configuration](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-02-configure.md).

## 4. The CI contract: record once, commit, replay forever

Three steps, and the third is the one that has to be enforced:

1. **Record once, locally**, against the real server. Default `once` mode does this when no cassette exists.
2. **Commit the cassette.** Review it like code — a changed tool description is a supply-chain event, not a fixture edit.
3. **CI only replays and lints.** `MCP_CASSETTE_MODE=none` makes a missing cassette a red build instead of a live call with production credentials.

`examples/cassettes/echo_and_add.mcp.json` is this repo's golden cassette — the committed recording behind `test_echo_and_add`. Prove replay-only mode against it, one test or the whole directory:

```bash
MCP_CASSETTE_MODE=none uv run pytest examples/test_echo.py -q   # one file: 4 passed
MCP_CASSETTE_MODE=none uv run pytest examples/ -q               # all examples: 5 passed
```

Both run with no server, no network, and no credentials. Under `none`, a deleted or unmerged cassette fails with `no cassette at <path> and recording is forbidden` — delete one on a scratch branch to see it.

Full chapter: [OP-03. CI pipeline](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-03-ci.md).

## 5. Fault injection

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

Every door can inject them: `with_faults(...)` on the fixture session, `use_cassette(..., faults=FaultOverlay(...))` in library code, and `serve --faults <overlay>.json` on the CLI — the fixture just writes your overlay to a temp file and passes that same flag. Faults are replay-only, on every door: `with_faults` under a recording mode raises (`new_episodes` counts as recording), and `serve --new-episodes --faults` is a usage error.

Faults fire on the **response** side, after a request matched a recorded exchange — there is no fault that corrupts the request on its way in. An unmatched request never reaches the injector at all: the client gets a `-32001` unmatched error and the server exits `3`, whether or not a fault targeted that method. `inspect --faults` dry-runs which recorded requests an overlay would hit, and a fault that never fired warns at shutdown, so a typo'd method name is visible instead of silent.

Full chapter: [HT-04. Inject faults](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-04-inject-faults.md).

## 6. Replay timing

Replay is instant by default. When your agent's timeout, progress-stream, or retry logic depends on *how long* the server took, replay the recorded gaps instead:

```
mcp-cassette serve demo.json --pace recorded                     # recorded latency
mcp-cassette serve demo.json --pace recorded --pace-scale 0.2    # 5x faster
```

Also `@pytest.mark.mcp_cassette(pace="recorded", pace_scale=0.2)` and `use_cassette(..., pace=PaceConfig(mode="recorded"))`. Per-gap cap defaults to 5000 ms so one pathological recorded pause cannot look like a hung job; `--pace-cap-ms 0` opts into uncapped. A `delay` fault stacks on top of recorded latency.

Full chapter: [HT-05. Replay timing](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-05-replay-timing.md).

## 7. Redaction

Cassettes are verbatim transcripts, and you commit them — so redaction runs at capture time, on a deep copy, with defaults always on: values under keys matching `*token*`, `*secret*`, `*password*`, `*apikey*`, `*api_key*`, or `authorization` are replaced with `REDACTED` before the cassette is written. Add your own rules with `--redact` (key-glob or JSON pointer). Read every new cassette before its first commit anyway.

Full chapter: [HT-07. Redact secrets](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-07-redact-secrets.md).

## 8. Gating your cassettes

Two gates, and they cover each other's blind spots: `lint` catches text that *looks* hostile, `diff` catches a surface that *moved*. A poisoned description that was there from the first recording never moves; an innocuous new parameter never looks hostile.

Both run against a cassette file, not a session, so they belong in CI next to your test run rather than inside it.

### 8.1 Lint: injection smells

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

A pack adds *patterns*, not *surfaces*: patterns match tool descriptions (from `tools/list`) and tool result text (from `tools/call`), which is everything lint reads. A tool's `name` and `inputSchema` are compared, not pattern-matched — that is `R002`'s and `diff`'s job.

> Heuristic pattern rules, not a guarantee — a clean lint is the absence of *known* smells, nothing more.

### 8.2 Diff: a drifting server surface

`diff --tools-only` exits `5` when a tool's description or schema moved between two recordings — including changes that carry no smell at all. Committed cassettes make the whole gate runnable from a clone, with no server and no network:

```
mcp-cassette lint examples/cassettes/tools.mcp.json                    # clean: exit 0
mcp-cassette lint examples/cassettes/tools-v2.mcp.json                 # injected description: exit 4
mcp-cassette diff examples/cassettes/tools.mcp.json \
                  examples/cassettes/tools-v2.mcp.json --tools-only    # surface moved: exit 5
```

`tools-v2` is the same server one version later. Its description picked up an injection payload, *and* `echo` grew a `callback_url` parameter. The new parameter reads as perfectly innocuous, so `lint` never flags it — only `diff` does. Both steps run in this repo's CI, asserted by exit code.

A red drift gate is not a failure to re-record away: read the diff, decide whether you accept the new surface, and only then commit the fresh cassette as the new baseline.

Full chapters: [HT-08. Lint with your own pattern packs](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-08-lint-pattern-packs.md), [HT-09. Gate a drifting server surface](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-09-gate-a-drifting-server.md).

## 9. Built with Claude Code

mcp-cassette was built with the help of [Claude Code](https://claude.com/claude-code) under human direction and
review.

It is an independent open-source project: not an Anthropic product, and not affiliated
with or endorsed by Anthropic. "Model Context Protocol" and "Claude" are Anthropic's.

## 10. License

Apache-2.0 — see [LICENSE](https://github.com/cheneeheng/mcp-cassette/blob/main/LICENSE).
