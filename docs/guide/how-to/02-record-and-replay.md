# 2. Record and replay a stdio server

**When:** your MCP server runs as a local command and your test drives an agent against
it.
**Prerequisites:** [1. Getting started](../01-getting-started.md) completed; a working
test using the `mcp_cassette` fixture.

## 2.1 The core loop

```python
def test_agent_summarizes_repo(mcp_cassette):
    cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert "summary" in result
```

`server_command()` returns one of two things, decided by the record mode and whether the
cassette file exists:

| Resolved action | What the returned command is |
|---|---|
| record | `python -m mcp_cassette record --cassette <path> --report <path> -- <your command>` |
| replay | `python -m mcp_cassette serve <path> --report <path> --ordering per_method` |
| new_episodes | `python -m mcp_cassette serve <path> ... --new-episodes -- <your command>` |

The agent is never patched. It launches whatever command you give it.

## 2.2 Choose where the cassette lives

By default the cassette path is
`tests/cassettes/<test-module-name>/<test-node-name>.mcp.json`, with any character
outside `A-Za-z0-9_.-` in the node name replaced by `_` (so parametrized tests each get
their own file).

Override the directory for the whole suite in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
mcp_cassette_dir = "tests/fixtures/cassettes"
```

Override one test's file with the marker:

```python
@pytest.mark.mcp_cassette(cassette="tests/cassettes/shared/github.mcp.json")
def test_agent_summarizes_repo(mcp_cassette):
    ...
```

**Verify:** run the test and confirm the file appears where you expect.

## 2.3 Pick a record mode

The modes answer one question, decided once per test run: does this run record or
replay? The unit is always the **entire session** — every message from server launch to
session end, all tool calls included — never an individual tool call. `all` therefore
re-records and overwrites the whole cassette file each run, not single entries in it.

Precedence, highest first: `MCP_CASSETTE_MODE` (env) → marker `mode=` →
`mcp_cassette_mode` (ini) → default `once`.

| Mode | Cassette absent | Cassette present |
|---|---|---|
| `once` (default) | record | replay |
| `none` | fail — recording is forbidden | replay |
| `all` | record | re-record |
| `new_episodes` | record | replay; misses fall through to the real server and are appended |

An invalid mode raises `ValueError: invalid mcp_cassette mode ...` at fixture setup.

```python
@pytest.mark.mcp_cassette(mode="none")
def test_never_records(mcp_cassette):
    ...
```

## 2.4 Re-record after the server changes

> **Warning:** re-recording overwrites the cassette in place. The old recording is gone
> unless it is committed to git. Commit first, or work on a branch.

Pick one:

1. **One test, one cassette** — delete the file and run normally. `once` mode records it
   again.

   ```
   rm tests/cassettes/test_agent/test_agent_summarizes_repo.mcp.json
   uv run pytest tests/test_agent.py::test_agent_summarizes_repo
   ```

2. **A whole file or suite** — force record mode for that run.

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
> replay semantics — determinism assertions and any test using `with_faults()` (faults
> are replay-only and raise `CassetteError` under a recording action). Re-record those
> per file with the delete-and-rerun approach.

## 2.5 Control how requests are matched

The JSON-RPC `id` is never matched on; the replay server re-stamps the client's `id`
onto the recorded response. Matching is structural over the parsed JSON, and by default
compares `method` and `params`.

Three ordering disciplines, set per test on the marker:

| `ordering` | Behaviour |
|---|---|
| `per_method` (default) | Answer with the earliest unconsumed exchange whose match key is equal; mark it consumed. Repeat calls to the same method replay in recorded order. |
| `strict` | The next unconsumed exchange must match, or the request is a miss. |
| `none` | Any matching exchange answers, unlimited times, in any order. |

Ignore a volatile field so it does not break matching:

```python
@pytest.mark.mcp_cassette(
    ordering="strict",
    ignore_params=["/params/arguments/requestId"],
)
def test_agent(mcp_cassette):
    ...
```

`ignore_params` entries are JSON pointers into the request object.

If the client and the recording disagree on the MCP protocol version at `initialize`,
`rewrite_protocol_version=True` makes replay answer with the version the client asked
for instead of the recorded one:

```python
@pytest.mark.mcp_cassette(rewrite_protocol_version=True)
def test_agent(mcp_cassette):
    ...
```

**Verify:** the test passes on replay with no `unmatched request(s)` failure.

## 2.6 What happens on failure

On teardown, the fixture calls `finalize()` and fails the test when:

- a recording captured zero messages (the agent never spoke to the proxied server), or
- replay hit any unmatched request. The failure message lists each miss as
  `method params=<digest>` and tells you to re-record.

The replay subprocess itself exits with code `3` on an unmatched request.

## 2.7 Server-initiated requests

Sampling and elicitation (the server asking the *client* mid-call) are recorded
generically and replay on both transports. On replay the recorded server request is
re-emitted with its recorded id, the client's answer is accepted as-is and never matched
against the recording, and the recorded response is released only after the client
answers.

There is deliberately no internal timeout for an unanswered server request. Use pytest's
own timeout; the shutdown summary names the request still pending.
