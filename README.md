# mcp-cassette

[![CI](https://github.com/cheneeheng/mcp-cassette/actions/workflows/ci.yml/badge.svg)](https://github.com/cheneeheng/mcp-cassette/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-cassette.svg)](https://pypi.org/project/mcp-cassette/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-cassette.svg)](https://pypi.org/project/mcp-cassette/)
[![License](https://img.shields.io/pypi/l/mcp-cassette.svg)](https://github.com/cheneeheng/mcp-cassette/blob/main/LICENSE)

Record what the MCP servers your agent depends on actually said, then use that one recording two ways: a CI check that fails the day a server you don't own breaks or changes its tools, and an offline replay that lets you evaluate every agent change against the same tool data, with the exact step where a changed agent departs from the recording.

A **cassette** is the whole session between your agent and a real MCP server, kept as a readable JSON file you commit and review like code. mcp-cassette works at the **transport level** (newline-delimited JSON-RPC over stdio), treats messages semi-opaquely, and does **not** depend on the `mcp` SDK at runtime, so it works with any MCP client unmodified.

Full documentation: **[docs/guide/](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/index.md)**. The sections below summarize, and each ends with a pointer to its full chapter.

## 1. Install

**Adding mcp-cassette to your own project**, the normal case. It is a test-only tool, so it goes in the dev group:

```
uv add --dev mcp-cassette              # or: pip install mcp-cassette
```

**Standing in a clone of this repo**, to run `examples/` or the test suite:

```
uv sync                                # installs this checkout plus its dev group
```

Do **not** run `uv add mcp-cassette` inside the clone: uv rejects it as a self-dependency.

Python ≥ 3.12, on Linux, macOS and Windows. The core install depends only on `anyio` and `pydantic`. Run the CLI as **`uv run mcp-cassette ...`** (or activate the venv first); the listings below drop the prefix.

Full chapter: [OP-01. Installation](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-01-install.md).

## 2. The upgrade tripwire

A server launched through `uvx` or `npx` is whatever version resolves that day. `check` re-launches it, reads its tool list, and compares it with the committed cassette. No agent, no model call, no test suite. In CI it is one step per server:

```
mcp-cassette check tests/cassettes/fetch.mcp.json -- uvx mcp-server-fetch
```

| Exit | Meaning |
|---|---|
| `0` | unchanged |
| `5` | a tool was added or removed, or its description or input schema changed; the diff is printed |
| `7` | the server did not start; its last stderr lines are printed |

Write the first baseline with `--record`, review it, and commit it. To accept an intended server change, run `--record` again: it rewrites a baseline that holds only the handshake, and refuses to replace a full test session. `--env KEY=dummy` passes a placeholder key to a server that will not start without one. Runnable from a clone, against the bundled echo server:

```
mcp-cassette check examples/cassettes/tools.mcp.json -- python examples/echo_server.py      # exit 0
mcp-cassette check examples/cassettes/tools-v2.mcp.json -- python examples/echo_server.py   # exit 5
```

A red tripwire is not a failure to re-record away: read the diff, decide whether you accept the new tools, and only then commit the new baseline.

Full chapter: [HT-09. Catch a server that changed under you](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-09-gate-a-drifting-server.md).

## 3. Record once, replay offline

```python
def test_agent_summarizes_repo(mcp_cassette):
    cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert "summary" in result
```

The first run records the real session through a recording proxy; every run after replays it offline, so every prompt or model you try sees the same tool data. The fixture never monkeypatches your agent: it hands you a *command list* to plug into the agent's MCP server configuration. `run_my_agent` stands in for your agent; [`examples/test_echo.py`](https://github.com/cheneeheng/mcp-cassette/blob/main/examples/test_echo.py) is the same test with a minimal client in that slot.

The mode decides, once per run, whether that run records or replays. The recording unit is always the whole session, never one tool call.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail: recording is forbidden | replay |
| `all` | record | re-record |

Precedence: `MCP_CASSETTE_MODE` (env), then the marker's `mode=`, then `mcp_cassette_mode` (ini), then `once`. Cassette paths come from the marker's `cassette=`, else `mcp_cassette_dir` (ini), else `<rootpath>/tests/cassettes`.

Full chapters: [Getting started](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/getting-started.md), [HT-01. Record and replay a stdio server](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-01-record-and-replay.md).

## 4. The divergence report

When a changed agent makes a call the recording has no answer for, replay stops at that step and shows the recorded call and the new one, as matching sees them:

```
mcp-cassette: replay diverged at step 2: tools/call: no recorded call matches
--- recorded
+++ new
@@ -1,6 +1,6 @@
 {
   "arguments": {
-    "text": "hello cassette"
+    "text": "hello, changed prompt"
   },
   "name": "echo"
 }
```

The run exits `3` and the fixture fails the test with the same block. Replay never falls through to the live server, because live data would make the comparison with the old agent unfair. Trace metadata in `params._meta`, where Langfuse and OpenTelemetry put a fresh id on every call, never decides a match. For an agent that recovers from a failed call, `on_unmatched="error"` on the marker (`serve --on-unmatched error`) answers the miss with an error and keeps serving; the run still exits `3`.

Runnable from a clone: [`examples/test_divergence.py`](https://github.com/cheneeheng/mcp-cassette/blob/main/examples/test_divergence.py).

Full chapter: [OP-05. Runbook: replay misses and failed recordings](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-05-runbook-replay-misses.md).

## 5. Failure on cue

One recorded cassette drives a whole resilience matrix, so "the agent degrades gracefully" becomes a test:

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

Fault types: `delay`, `timeout`, `error`, `malformed`, `disconnect`. Faults live in a separate `FaultOverlay` (or a `serve --faults` sidecar file); the recorded cassette is never changed. Faults are replay-only.

Full chapter: [HT-04. Inject faults](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-04-inject-faults.md).

## 6. CI settings

1. **Record once, locally**, against the real server. The default `once` mode does this when no cassette exists.
2. **Commit the cassette.** Review it like code: a changed tool description is a supply-chain event, not a fixture edit.
3. **CI only replays and checks.** Set `MCP_CASSETTE_MODE=none`, so a missing cassette is a red build instead of a live call with production credentials, and `check --record` is refused.

Prove replay-only mode from a clone, with no server, network or credentials:

```bash
MCP_CASSETTE_MODE=none uv run pytest examples/ -q
```

Full chapter: [OP-03. CI pipeline](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-03-ci.md).

## 7. No secrets in the committed file

Cassettes are verbatim transcripts you commit, so redaction runs at capture time, on a copy, with defaults always on: values under keys matching `*token*`, `*secret*`, `*password*`, `*apikey*`, `*api_key*`, or `authorization` (with `-` and `_` folded, so `X-API-Key` matches) are replaced with `REDACTED` before the file is written. Add your own rules with `record --redact` (key-glob or JSON pointer). Read every new cassette before its first commit anyway.

Full chapter: [HT-07. Redact secrets](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-07-redact-secrets.md).

## 8. Format stability

Every cassette carries a `format_version`, and a newer one than this release understands fails to load with a clear error rather than replaying wrongly. The format is **unstable (0.x)** until three outside repositories use it: a release may change it, and the changelog says how to move. Pin the version you record with.

## 9. Beyond Stage 1

These ship and are tested, but get no new features until a user needs them. Each has its guide page:

- Remote Streamable HTTP record and replay (`[http]` extra, `server_url`): [HT-02](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-02-remote-http.md)
- The `use_cassette` and `use_cassette_async` library doors: [HT-03](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-03-use-as-a-library.md)
- Replay timing (`--pace recorded`): [HT-05](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-05-replay-timing.md)
- `inspect` and `diff`: [HT-06](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-06-inspect-and-diff.md)
- `lint` and pattern packs: [HT-08](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-08-lint-pattern-packs.md), [HT-11](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-11-detect-secrets.md)
- Free-text PII redaction packs: [HT-10](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/how-to/HT-10-redact-pii.md)
- `new_episodes` mode, which appends misses from the live server: [OP-02](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-02-configure.md)
- Parallel runs and the single-writer claim: [OP-06](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-06-parallel-test-runs.md)
- The packaged GitHub Action and pre-commit hooks: [OP-03](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-03-ci.md), [OP-07](https://github.com/cheneeheng/mcp-cassette/blob/main/docs/guide/operations/OP-07-pre-commit.md)

## 10. Built with Claude Code

mcp-cassette was built with the help of [Claude Code](https://claude.com/claude-code) under human direction and
review.

It is an independent open-source project: not an Anthropic product, and not affiliated
with or endorsed by Anthropic. "Model Context Protocol" and "Claude" are Anthropic's.

## 11. License

Apache-2.0 — see [LICENSE](https://github.com/cheneeheng/mcp-cassette/blob/main/LICENSE).
