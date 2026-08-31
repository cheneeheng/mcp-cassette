# Getting started

Goal: go from nothing to a session that records against a real MCP server once and then
replays it offline, forever.

**Time:** about 10 minutes for one door.

## Before you begin

Everyone needs:

- Python 3.12 or newer. Linux, macOS, and Windows are all supported.
- An MCP server you can launch as a local command, for example
  `python tools/github_server.py`.

Recording contacts the real server, so you also need whatever that server needs —
credentials, network access — but only on the first run.

### No server to record against yet?

The walkthroughs below name `examples/echo_server.py`, which exists **only in a clone
of this repo** — installing from PyPI does not bring `examples/` with it. If you
installed into your own project and want a real server to record right now, save this
as `echo_server.py` next to your `pyproject.toml`. It is an MCP server in 30 lines of
standard library, no `mcp` SDK, and it is enough to complete every step on this page:

```python
import json
import secrets
import sys


def handle(request):
    method = request.get("method")
    msg_id = request.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo-example", "version": "1.0.0"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/call":
        params = request.get("params") or {}
        args = params.get("arguments") or {}
        if params.get("name") == "add":
            text = str(int(args.get("a", 0)) + int(args.get("b", 0)))
        else:
            text = f"{args.get('text', '')} [{secrets.token_hex(4)}]"
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text", "text": text}], "isError": False}}
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


for line in sys.stdin:
    if line.strip():
        response = handle(json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
```

It writes nothing and opens no socket — it reads JSON-RPC lines on stdin and answers on
stdout, which is the whole transport mcp-cassette records. Two tools: `add` returns a
sum, and anything else echoes your text with a **fresh random token per call**. That
token is what makes replay visible: the real server mints a new one every time, and the
cassette returns the recorded one forever.

Wherever a command below says `python examples/echo_server.py`, use `python
echo_server.py` instead. The CLI walkthrough is the one to follow — it drives the
session with a shell pipe, so it needs no client of your own. The fixture and
`use_cassette` doors put *your* agent where that pipe is.

Per door, one thing more:

| Door | Also needs |
|---|---|
| pytest fixture | pytest 8 or newer, and an agent whose MCP server config you can set from test code |
| `use_cassette` | nothing else — plain Python |
| CLI | a client you can point at a command, or a shell that can pipe JSON-RPC lines |

## Install

Two situations, two different commands. Pick the one you are actually in.

**Adding mcp-cassette to your own project** — the normal case. It is a test-only tool,
so it goes in the dev group:

```
uv add --dev mcp-cassette
```

or, with pip:

```
pip install mcp-cassette
```

This writes `pyproject.toml` and `uv.lock` and installs into `.venv/`. To undo it:
`uv remove --dev mcp-cassette`.

This install brings the library and the CLI, and nothing else — `examples/` is not part
of the package. To follow the walkthroughs below without cloning, paste the 30-line
server from [No server to record against yet?](#no-server-to-record-against-yet).

**Standing in a clone of the mcp-cassette repo** — which is what the CLI walkthrough
below and everything under `examples/` assume, because they run the bundled echo server:

```
uv sync
```

This installs the checkout plus its dev group. It writes `.venv/` and, if the lock was
stale, `uv.lock`; it does not touch `pyproject.toml`. To undo it: delete `.venv/`.

Do **not** run `uv add mcp-cassette` inside the clone. uv rejects it as a self-dependency
and exits `2`, and the `--dev` form silently rebuilds the checkout as its own dependency
rather than doing what you meant. `uv sync` is the clone's install.

Either way the CLI lands inside a virtualenv and is *not* placed on `PATH`,
so invoke it as `uv run mcp-cassette ...` (or activate the venv first) — a bare
`mcp-cassette` is `command not found`. Every command below carries the prefix.

The pytest plugin registers itself through the `pytest11` entry point — there is nothing
to configure. If the `mcp_cassette` fixture turns up missing later, the package is
installed in a different environment than the one pytest runs in; the health check in
[OP-01.3](operations/OP-01-install.md) confirms it.

## Pick your door

There are three ways in, and they open the same machinery: the same cassette format, the
same matching rules, the same failure semantics. Pick the one that matches how your tests
already run — the sections below do not build on each other, so read one and skip the
other two.

| Door | You call | Pick it when | Section |
|---|---|---|---|
| pytest fixture | `mcp_cassette.server_command(...)` | your tests are a pytest suite | [First run with the pytest fixture](#first-run-with-the-pytest-fixture) |
| library | `with use_cassette(...) as session:` | your harness is a notebook, a benchmark runner, or another test framework | [First run with `use_cassette`](#first-run-with-use_cassette) |
| CLI | `mcp-cassette record` / `serve` | you drive recording by hand, from a shell script, or your agent is configured outside Python | [First run with the CLI](#first-run-with-the-cli) |

Whichever you pick, the move is the same: put a cassette in the slot where the real
server command goes. Nothing about your agent changes — it is never patched.

## First run with the pytest fixture

1. Take the server command from the fixture instead of hard-coding it.

   ```python
   def test_agent_summarizes_repo(mcp_cassette):
       cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
       result = run_my_agent(mcp_servers={"github": cmd})
       assert "summary" in result
   ```

   `cmd` is a plain `list[str]`. Nothing else changes.

   `run_my_agent` is a stand-in for *your* agent — nothing ships under that name. If you
   do not have one to hand yet, [`examples/test_echo.py`](../../examples/test_echo.py) is
   this same test with a minimal client in that slot, runnable from a clone with `uv run
   pytest examples/test_echo.py`. That file is in the repo, not in the package, so from
   your own project take [First run with the CLI](#first-run-with-the-cli) instead — it
   proves the same record-then-replay loop with a shell pipe in the agent's place.

2. Run the test. The default mode is `once`: no cassette exists yet, so this run launches
   the recording proxy in front of the real server and captures every JSON-RPC message in
   both directions — the whole session, from server launch to shutdown, not just the
   first tool call.

   ```
   uv run pytest tests/test_agent.py::test_agent_summarizes_repo
   ```

3. Run the same command again. The cassette now exists, so this run replays it.

**Verify:** after step 2 a cassette exists at
`tests/cassettes/<test-module>/<test-name>.mcp.json`:

```
uv run mcp-cassette inspect tests/cassettes/test_agent/test_agent_summarizes_repo.mcp.json
```

Every line below is printed; only the values differ for your server:

```
cassette: tests/cassettes/test_agent/test_agent_summarizes_repo.mcp.json
format_version: 2
transport: stdio
recorded_at: 2026-08-01T15:50:25.605552+00:00
protocol_version: 2024-11-05
server: github-server 1.0.0
messages: 8
  <response>: 3
  initialize: 1
  notifications/initialized: 1
  tools/call: 3
timing span: 62 ms
```

Check two lines, not one. A non-zero `messages` count says the proxy was driven; **no
`unanswered requests:` line** says the server actually answered. A recording against a
server that failed to launch still captures the client's opening request, so it has a
non-zero count and is still broken — `inspect` prints

```
unanswered requests: 1 (the server never responded to these; replay exits 3 on one of them)
```

for exactly that case. Re-record rather than committing it.

Then prove step 3 really ran offline by breaking the real command:

```python
cmd = mcp_cassette.server_command(["python", "does-not-exist.py"])
```

The test still passes, because the real command is never launched once a cassette exists.

**If it fails:** `recording captured zero messages — agent never spoke to the proxied
server` means the agent never launched the command you were handed. Print `cmd` and
confirm it reaches the agent's MCP server configuration verbatim.

Full treatment — cassette paths, markers, re-recording — is in
[HT-01. Record and replay a stdio server](how-to/HT-01-record-and-replay.md).

## First run with `use_cassette`

1. Open the block and take the command from the session.

   ```python
   from mcp_cassette import use_cassette

   with use_cassette("cassettes/github.mcp.json", mode="once") as session:
       cmd = session.server_command(["python", "tools/github_server.py"])
       run_my_agent(mcp_servers={"github": {"command": cmd[0], "args": cmd[1:]}})
   ```

2. Run your harness once. `once` decides record-vs-replay by whether the cassette file
   exists — the same rule the fixture uses — so this first run records.

3. Run it again. The file exists now, so this replays.

**Verify:** `cassettes/github.mcp.json` exists after step 2, and step 3 completes with the
real server stopped.

A runnable version ships with the repo — in the repo, not in the package, so this needs a
clone. Driving the bundled echo server, run it twice:

```
uv run python examples/library_mode.py
uv run python examples/library_mode.py
```

```
cassette: .../examples/library_mode.mcp.json (will record)
echo result: hello from the library [7024dad9]
```

```
cassette: .../examples/library_mode.mcp.json (exists)
echo result: hello from the library [7024dad9]
```

The bracketed token is minted fresh by the server on every real call, so a live second run
would print a different one. Yours will differ from the one above — what matters is that
it is *identical across the two runs*. That is replay, and it is the whole point.

**If it fails:** on a clean exit the block calls `finalize()`, which raises `CassetteError`
on an empty recording or an unmatched replay request. If your own code raises inside the
block, your exception propagates untouched and the report check is skipped deliberately.

Full surface — modes, the report sidecar, nesting — is in
[HT-03. Use it as a library](how-to/HT-03-use-as-a-library.md).

## First run with the CLI

There is no mode here: you choose by picking the subcommand. `record` is a transparent
proxy that forwards its own stdin to the wrapped server, and `serve` answers from the
cassette — both need a client to drive them, so the examples below pipe raw JSON-RPC in
to stand in for your agent.

What the two commands write, before you run them: `record` writes the cassette path you
name (plus a `<cassette>.partial` sidecar while it runs, removed on a clean finish) and
nothing else — to undo it, delete that file. If the cassette already exists, `record`
says so and names the replacement *before* it touches anything, so you can copy the old
one first. `serve` writes nothing at all: it only reads the cassette and answers on
stdout. Full table in [OP-04.2](operations/OP-04-cli-reference.md#op-042-record).

1. Record, wrapping the real server command after `--`. This runs from a clone against
   the bundled echo server; if you pasted the server into your own project instead, drop
   the `examples/` and use `-- python echo_server.py`:

   ```bash
   printf '%s\n' \
     '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}' \
     '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
     '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}' \
     | uv run mcp-cassette record --cassette demo.mcp.json -- python examples/echo_server.py
   ```

   The server's own answers come back on stdout as they are captured:

   ```
   {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "echo-example", "version": "1.0.0"}}}
   {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "5"}], "isError": false}}
   ```

   Piping closes stdin at EOF, which cleanly ends the session and writes the cassette.

2. Replay the same requests from the cassette — drop the real command entirely:

   ```bash
   printf '%s\n' \
     '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}' \
     '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
     '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}' \
     | uv run mcp-cassette serve demo.mcp.json
   ```

3. In a real setup, step 1 and step 2 are the *server command* in your agent's MCP
   configuration — `mcp-cassette record --cassette <path> -- <real server>` first, then
   `mcp-cassette serve <path>`. Your agent replaces the `printf`.

**Verify:** step 2 prints the same two JSON-RPC responses as step 1 — same ids, same
`"5"` result — with no server subprocess and no network. Compare the JSON, not the raw
bytes: on Windows, `record` passes the real server's `\r\n` line endings through
untouched while replay writes `\n`, so `diff` reports a difference where the messages are
in fact the same. Check the recording landed:

```
uv run mcp-cassette inspect demo.mcp.json
```

```
cassette: demo.mcp.json
format_version: 2
transport: stdio
recorded_at: 2026-08-01T15:50:25.605552+00:00
protocol_version: 2024-11-05
server: echo-example 1.0.0
messages: 5
  <response>: 2
  initialize: 1
  notifications/initialized: 1
  tools/call: 1
timing span: 48 ms
```

**If it fails:** the first signal is step 1's own exit code — `record` returns the wrapped
server's exit code, so a non-zero one (`echo $?`, or `$LASTEXITCODE` in PowerShell) means
the server never ran and the cassette it left behind is a failed recording, not a short
one. `inspect` says the same thing in band with an `unanswered requests:` line. Beyond
that: `serve` exits `3` on any request the cassette cannot answer, naming the misses, and
a `messages: 0` count after step 1 means nothing ever drove the proxy.

`printf` and `\` continuations are not native to PowerShell; the equivalent using a
piped array, plus the HTTP version, is in
[`examples/README.md`](../../examples/README.md). Every flag is in
[OP-04. CLI reference](operations/OP-04-cli-reference.md).

## Commit the cassette

Whichever door recorded it, the cassette is a file you commit:

```
git add tests/cassettes/
git commit -m "test: record github server cassette"
```

Cassettes are JSON with stable key order and two-space indentation, so they diff and
review like source. Read the recorded content before committing — see
[HT-07. Redact secrets](how-to/HT-07-redact-secrets.md).

## Lock CI down

Set this in your pipeline so no CI run can silently record against a live server:

```
MCP_CASSETTE_MODE=none
```

In `none` mode a missing cassette fails the run instead of recording it. The environment
variable is the top tier on both doors that have a mode — it outranks a marker, an ini
option, and a hard-coded `mode=` argument alike — so a suite cannot opt itself back into
recording. The CLI has no mode to override: `record` and `serve` are separate commands, so
a pipeline simply never invokes `record`. Full pipeline setup is in
[OP-03. CI pipeline](operations/OP-03-ci.md).

## You're set up

From here:

- Re-record after the server changes:
  [HT-01. Record and replay a stdio server](how-to/HT-01-record-and-replay.md).
- Test failure paths without a broken server:
  [HT-04. Inject faults](how-to/HT-04-inject-faults.md).
- Your MCP server is remote, not a local command:
  [HT-02. Record and replay a remote HTTP server](how-to/HT-02-remote-http.md).
