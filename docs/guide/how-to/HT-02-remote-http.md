# HT-02. Record and replay a remote HTTP server

**When:** the MCP server your agent talks to is a remote Streamable HTTP endpoint, not a
local command.
**Prerequisites:** the `[http]` extra installed; network access to the real endpoint on
the recording run only.

```
uv add "mcp-cassette[http]"
```

The extra adds `httpx` and `h11`. Without it, `server_url()` raises `CassetteError`
carrying the import error text.

This is [HT-01](HT-01-record-and-replay.md) with one substitution changed: you swap a
**URL** into the agent's configuration instead of a command list. Pick your door — the
sections do not build on each other.

| Door | Section | Read it if |
|---|---|---|
| pytest fixture | [HT-02.1](#ht-021-with-the-pytest-fixture) | your tests are a pytest suite |
| library (`use_cassette`) | [HT-02.2](#ht-022-with-use_cassette) | your harness is a notebook, benchmark, or another test framework |
| CLI | [HT-02.3](#ht-023-with-the-cli) | you record by hand or from a shell script |

[HT-02.4](#ht-024-behaviour-shared-by-all-three-doors) holds what is identical everywhere:
credentials, transport mixing, and what a client must do to drive the replay server.

## HT-02.1 With the pytest fixture

1. Call `server_url()` with the real endpoint and give the result to your agent.

   ```python
   def test_agent_reads_remote_tracker(mcp_cassette):
       url = mcp_cassette.server_url("https://mcp.example.com/mcp")
       result = run_my_agent(mcp_servers={"tracker": {"url": url}})
       assert "triaged" in result
   ```

   The returned URL is always local — `http://127.0.0.1:<port>/mcp`. What answers on it
   depends on the run: a recording proxy in front of the real endpoint on the first run, a
   local mock rebuilt from the cassette on every run after.

2. Run the test once to record, again to replay.

   ```
   uv run pytest tests/test_agent.py::test_agent_reads_remote_tracker
   ```

The server runs on a background thread owned by the session, and is stopped — with the
cassette and report finalized — when the fixture tears down.

**Verify:** point at an endpoint that cannot resolve and re-run.

```python
url = mcp_cassette.server_url("https://dead.invalid/mcp")
```

The test still passes on replay. That is the whole guarantee.

## HT-02.2 With `use_cassette`

1. Open the block and take the URL from the session.

   ```python
   from mcp_cassette import use_cassette

   with use_cassette("cassettes/tracker.mcp.json", mode="once") as session:
       url = session.server_url("https://mcp.example.com/mcp")
       run_my_agent(mcp_servers={"tracker": {"url": url}})
   ```

2. Run your harness once to record, and again to replay.

`server_url` starts the server in *this* process on a background thread bound to
`127.0.0.1` on an ephemeral port, and stops it when the block exits.

**Verify:** `mcp-cassette inspect cassettes/tracker.mcp.json` reports `transport: http`
after the first run.

> **Note:** stdio hands you a command list, HTTP hands you a running server. That
> asymmetry is not a preference — an HTTP config carries no command, so something must
> already be listening before the agent connects. See
> [HT-03.3](HT-03-use-as-a-library.md#ht-033-the-one-asymmetry-stated-up-front).

## HT-02.3 With the CLI

1. Start the recording proxy against the real endpoint:

   ```
   mcp-cassette record --cassette cassettes/tracker.mcp.json \
     --url https://mcp.example.com/mcp --port 8902 --max-idle 30
   ```

   `--port` pins the proxy port so you know where to send the client. `--max-idle 30`
   finalizes the cassette and exits after 30 seconds of client inactivity, so no interrupt
   is needed — the unattended-CI escape hatch.

2. Point your agent at `http://127.0.0.1:8902/mcp` and run it.

3. Replay from the cassette on the same port:

   ```
   mcp-cassette serve cassettes/tracker.mcp.json --port 8902
   ```

   `serve` infers the transport from the cassette and prints the chosen URL on startup.

**Verify:**

```
mcp-cassette inspect cassettes/tracker.mcp.json
```

Expected: `transport: http`, plus the recorded server host and exchange count.

A worked, runnable version using the bundled sample servers is in
[`examples/README.md`](../../../examples/README.md).

## HT-02.4 Behaviour shared by all three doors

### Headers and credentials

Every request header, `Authorization` included, is forwarded upstream during recording but
is **never written to the cassette**. Payload-level secrets are a separate concern — see
[HT-07. Redact secrets](HT-07-redact-secrets.md).

The server's `Mcp-Session-Id` is recorded as provenance only. Replay issues a fresh session
id and never reuses the recorded one.

### Do not mix transports

A cassette carries its transport, and the wrong accessor raises `CassetteError`:

| You call | Against | Error says |
|---|---|---|
| `server_command()` | an `http` cassette | use `server_url(...)` |
| `server_url()` | a `stdio` cassette | use `server_command(...)` |

The check applies only once a cassette exists; on a fresh recording either accessor decides
the transport.

### What a client must do

Anything driving the replay server must behave like a Streamable HTTP MCP client:

- POST each JSON-RPC object to `/mcp` with `Content-Type: application/json` and
  `Accept: application/json, text/event-stream`.
- Capture the `Mcp-Session-Id` header from the `initialize` response and send it on every
  later request. Without it the replay server answers `404`, per the spec.
- Expect a JSON body for requests, and a bodyless `202` for notifications and client-side
  responses.

## HT-02.5 Related

- [HT-01. Record and replay a stdio server](HT-01-record-and-replay.md) — the same task
  for a local command, plus record modes and matching, which are transport-independent.
- [HT-05. Replay timing](HT-05-replay-timing.md) — SSE inter-event spacing is the
  highest-fidelity thing pacing buys, and it is HTTP-only.
- [OP-04. CLI reference](../operations/OP-04-cli-reference.md) — every flag.
