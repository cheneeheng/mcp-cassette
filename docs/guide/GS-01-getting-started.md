# GS-01. Getting started

Goal: go from nothing to a test that records a real MCP session once and then replays it
offline, forever.

**Time:** about 10 minutes.

## GS-01.1 Before you begin

- Python 3.12 or newer. Linux, macOS, and Windows are all supported.
- pytest 8 or newer in your test environment.
- An MCP server you can launch as a local command, for example
  `python tools/github_server.py`.
- An agent (or any MCP client) whose MCP server configuration you can set from test code.

Recording contacts the real server, so you also need whatever that server needs —
credentials, network access — but only on the first run.

## GS-01.2 Install

```
uv add --dev mcp-cassette
```

or, with pip:

```
pip install mcp-cassette
```

The pytest plugin registers itself through the `pytest11` entry point — there is nothing
to configure. If the `mcp_cassette` fixture turns up missing in the next step, the
package is installed in a different environment than the one pytest runs in; the health
check in [OP-01.3](operations/OP-01-install.md) confirms it.

## GS-01.3 Write the test

The fixture gives you a `CassetteSession`. Call `server_command()` with the real server
command and hand the result to your agent instead of the real command.

```python
def test_agent_summarizes_repo(mcp_cassette):
    cmd = mcp_cassette.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert "summary" in result
```

Nothing else changes. `cmd` is a plain `list[str]`.

## GS-01.4 Record the first run

```
uv run pytest tests/test_agent.py::test_agent_summarizes_repo
```

The default mode is `once`: no cassette exists yet, so this run launches the recording
proxy in front of the real server and captures every JSON-RPC message in both
directions — the whole session, from server launch to shutdown, not just the first tool
call.

**Verify:** the test passes and a cassette file now exists at
`tests/cassettes/<test-module>/<test-name>.mcp.json`:

```
uv run mcp-cassette inspect tests/cassettes/test_agent/test_agent_summarizes_repo.mcp.json
```

Expected output starts, roughly:

```
cassette: tests/cassettes/test_agent/test_agent_summarizes_repo.mcp.json
transport: stdio
server: github-server 1.0.0
messages: 8
```

A non-zero `messages` count is the thing to check. The full summary — protocol version,
per-method counts, timing span — is in
[HT-06. Inspect and diff cassettes](how-to/HT-06-inspect-and-diff.md).

**If it fails:** an error reading `recording captured zero messages — agent never spoke
to the proxied server` means the agent never launched the command you were handed. Check
that `cmd` really reached the agent's MCP server configuration.

## GS-01.5 Replay

Run the same test again:

```
uv run pytest tests/test_agent.py::test_agent_summarizes_repo
```

**Verify:** the test passes with no network access and no server subprocess of your own —
the cassette answers every request. Prove it by breaking the real command:

```python
cmd = mcp_cassette.server_command(["python", "does-not-exist.py"])
```

The test still passes on replay, because the real command is never launched once a
cassette exists.

## GS-01.6 Commit the cassette

```
git add tests/cassettes/
git commit -m "test: record github server cassette"
```

Cassettes are JSON with stable key order and two-space indentation, so they diff and
review like source. Read the recorded content before committing — see
[HT-07. Redact secrets](how-to/HT-07-redact-secrets.md).

## GS-01.7 Lock CI down

Set this in your pipeline so no CI run can silently record against a live server:

```
MCP_CASSETTE_MODE=none
```

In `none` mode a missing cassette fails the test instead of recording it. Full pipeline
setup is in [OP-03. CI pipeline](operations/OP-03-ci.md).

## GS-01.8 You're set up

From here:

- Re-record after the server changes:
  [HT-01. Record and replay a stdio server](how-to/HT-01-record-and-replay.md).
- Test failure paths without a broken server:
  [HT-04. Inject faults](how-to/HT-04-inject-faults.md).
- Your MCP server is remote, not a local command:
  [HT-02. Record and replay a remote HTTP server](how-to/HT-02-remote-http.md).
