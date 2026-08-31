# HT-01. Record and replay a stdio server

**When:** your MCP server runs as a local command and you want a test or harness to drive
an agent against it once, then offline forever.
**Prerequisites:** mcp-cassette installed; the server launchable as a command, for example
`python tools/github_server.py`.

The task is the same through all three doors: put a cassette in the slot where the real
server command goes. Pick your door and read that section — they do not build on each
other.

| Door | Section | Read it if |
|---|---|---|
| pytest fixture | [HT-01.1](#ht-011-with-the-pytest-fixture) | your tests are a pytest suite |
| library (`use_cassette`) | [HT-01.2](#ht-012-with-use_cassette) | your harness is not pytest |
| CLI | [HT-01.3](#ht-013-with-the-cli) | you record by hand or from a shell script |

The library door covers a notebook, a benchmark runner, or another test framework.

[HT-01.4](#ht-014-behaviour-shared-by-all-three-doors) holds what is identical everywhere:
record modes, re-recording, matching, and what failure looks like.

## HT-01.1 With the pytest fixture

1. Take the command from the fixture instead of hard-coding it.

   ```python
   def test_agent_summarizes_repo(mcp_cassette):
       cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
       result = run_my_agent(mcp_servers={"github": cmd})
       assert "summary" in result
   ```

   `cmd` is a plain `list[str]`. The agent is never patched — it launches whatever you
   give it.

   `run_my_agent` is a stand-in for *your* agent; nothing ships under that name. To run
   this door before you have an agent, [`examples/test_echo.py`](../../../examples/test_echo.py)
   is this same test with a minimal client in that slot — `uv run pytest
   examples/test_echo.py` from a clone. `examples/` is in the repo, not in the package,
   so from your own project use the
   [CLI walkthrough](../getting-started.md#first-run-with-the-cli) instead: it proves the
   same loop against a [paste-able server](../getting-started.md#no-server-to-record-against-yet).

2. Run the test. No cassette exists, so this records.

   ```
   uv run pytest tests/test_agent.py::test_agent_summarizes_repo
   ```

3. Run it again. The cassette exists, so this replays.

   ```
   uv run pytest tests/test_agent.py::test_agent_summarizes_repo
   ```

**Verify:** the second run passes with the real server stopped. Prove it by pointing at a
command that cannot start — `server_command(["python", "does-not-exist.py"])` still
passes on replay, because the real command is never launched once a cassette exists.

**Cassette location** is a fixture concern only (the other two doors take an explicit
path). The default is `tests/cassettes/<test-module>/<test-node>.mcp.json`, with any
character outside `A-Za-z0-9_.-` in the node name replaced by `_`, so parametrized tests
each get their own file. Override for the suite:

```toml
[tool.pytest.ini_options]
mcp_cassette_dir = "tests/fixtures/cassettes"
```

or for one test:

```python
@pytest.mark.mcp_cassette(cassette="tests/cassettes/shared/github.mcp.json")
def test_agent_summarizes_repo(mcp_cassette):
    ...
```

## HT-01.2 With `use_cassette`

1. Open the block and take the command from the session.

   ```python
   from mcp_cassette import use_cassette

   with use_cassette("cassettes/github.mcp.json", mode="once") as session:
       cmd = session.server_command(["python", "tools/github_server.py"])
       run_my_agent(mcp_servers={"github": {"command": cmd[0], "args": cmd[1:]}})
   ```

2. Run your harness once to record, and again to replay. `once` mode decides by whether
   the file exists — the same rule the fixture uses.

**Verify:** `cassettes/github.mcp.json` exists after the first run, and the second run
completes with the real server stopped.

A clean exit calls `finalize()`, which raises `CassetteError` on an empty recording or an
unmatched replay request. If your own code raises inside the block, the session is closed
(no thread or socket leaks) and **your** exception propagates untouched — the report check
is skipped deliberately, because a replay miss is usually a consequence of the real
failure and chaining it on top buries the cause.

Full library surface, including the report sidecar and nesting rules:
[HT-03. Use it as a library](HT-03-use-as-a-library.md).

## HT-01.3 With the CLI

There is no mode here — you choose by picking the subcommand.

1. Point the agent's MCP configuration at `record`, wrapping the real command after `--`:

   ```
   mcp-cassette record --cassette cassettes/github.mcp.json -- python tools/github_server.py
   ```

2. Run the agent. `record` is a transparent proxy: it forwards its own stdin to the
   wrapped server and captures both directions on the way through. Nothing is captured
   unless a client actually drives it.

3. Swap the configuration to `serve` and drop the real command:

   ```
   mcp-cassette serve cassettes/github.mcp.json
   ```

**Verify:** inspect the file between the two steps.

```
mcp-cassette inspect cassettes/github.mcp.json
```

A non-zero `messages` count means the proxy was driven; the absence of an
`unanswered requests:` line means the server actually answered. Both matter — a
recording against a server that failed to launch still captures the opening request,
so a non-zero count on its own does not mean the recording is good. Replay answers
requests but emits nothing on its own, so it also needs a client to drive it.

Every flag is in [OP-04. CLI reference](../operations/OP-04-cli-reference.md).

## HT-01.4 Behaviour shared by all three doors

### Record modes

The mode answers one question, decided once per run: does this run record or replay? The
unit is always the **entire session** — every message from server launch to session end —
never an individual tool call. `all` therefore re-records the whole cassette file, not
single entries in it.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail — recording is forbidden | replay |
| `all` | record | re-record |
| `new_episodes` | record | replay; misses fall through to the real server and are appended |

Precedence differs by door, but `MCP_CASSETTE_MODE` is the top tier in both that have
one — so the CI invariant holds no matter which door a suite uses:

| Door | Precedence, highest first |
|---|---|
| fixture | `MCP_CASSETTE_MODE` → marker `mode=` → `mcp_cassette_mode` ini → `once` |
| library | `MCP_CASSETTE_MODE` → `mode=` argument → `once` |
| CLI | n/a — `record` and `serve` are separate commands |

An invalid mode raises `ValueError` naming the bad value and its source.

### Re-record after the server changes

> **Warning:** re-recording overwrites the cassette in place. The old recording is gone
> unless it is committed to git. Commit first, or work on a branch.

1. **One cassette** — delete the file and run normally; `once` records it again.

   ```
   rm tests/cassettes/test_agent/test_agent_summarizes_repo.mcp.json
   uv run pytest tests/test_agent.py::test_agent_summarizes_repo
   ```

2. **A whole suite** — force record mode for that run.

   ```
   MCP_CASSETTE_MODE=all uv run pytest tests/test_agent.py
   ```

   In PowerShell:

   ```powershell
   $env:MCP_CASSETTE_MODE = "all"; uv run pytest tests/test_agent.py
   ```

3. **Only the new interactions** — `new_episodes` replays what is recorded and appends
   anything that misses, going to the real server for it.

   ```
   MCP_CASSETTE_MODE=new_episodes uv run pytest tests/test_agent.py
   ```

**Verify:** `git diff tests/cassettes/` shows the change you expect, and a subsequent
plain `uv run pytest` (replay) passes.

> **Note:** `MCP_CASSETTE_MODE=all` cannot produce a green run for tests that depend on
> replay semantics — determinism assertions, and anything using `with_faults()` (faults
> are replay-only and raise `CassetteError` under a recording action). Re-record those
> with the delete-and-rerun approach instead.

### Matching

The JSON-RPC `id` is never matched on; the replay server re-stamps the client's `id` onto
the recorded response. Matching is structural over the parsed JSON, and by default
compares `method` and `params`.

| `ordering` | Behaviour |
|---|---|
| `per_method` (default) | Earliest unconsumed exchange with an equal key wins. |
| `strict` | The next unconsumed exchange must match, or the request is a miss. |
| `none` | Any matching exchange answers, unlimited times, in any order. |

Under `per_method` the chosen exchange is marked consumed, so repeat calls to the same
method replay in the order they were recorded.

Same three settings, three spellings:

| Setting | Fixture marker | Library | CLI |
|---|---|---|---|
| ordering | `ordering="strict"` | `MatchConfig(ordering="strict")` | `--ordering strict` |
| ignore a field | `ignore_params=[...]` | `MatchConfig(ignore_params=[...])` | `--ignore-param PTR` |
| accept client protocol | `rewrite_protocol_version=True` | same keyword | `--rewrite-protocol-version` |

`ignore_params` entries are JSON pointers into the request object.

### What failure looks like

Two conditions fail a session: a recording that captured zero messages (the agent never
spoke to the proxied server), and a replay that hit any unmatched request. Each door
reports them differently:

| Door | How it surfaces |
|---|---|
| fixture | `finalize()` on teardown fails the test, listing each miss as `method params=<digest>` |
| library | `finalize()` raises `CassetteError` on clean block exit |
| CLI | `serve` exits `3` on an unmatched request |

### Server-initiated requests

Sampling and elicitation — the server asking the *client* mid-call — are recorded
generically and replay on both transports. The recorded server request is re-emitted with
its recorded id, the client's answer is accepted as-is and never matched against the
recording, and the recorded response is released only after the client answers. There is
deliberately no internal timeout for an unanswered server request: use your test runner's
own timeout, and the shutdown summary names the request still pending.

## HT-01.5 Related

- [HT-02. Record and replay a remote HTTP server](HT-02-remote-http.md) — the same task
  when the server is a URL, not a command.
- [HT-03. Use it as a library](HT-03-use-as-a-library.md) — the full `use_cassette`
  surface.
- [OP-02. Configuration](../operations/OP-02-configure.md) — every ini option and marker
  keyword.
- [OP-05. Runbook: replay misses](../operations/OP-05-runbook-replay-misses.md) — when a
  replay misses in CI.
