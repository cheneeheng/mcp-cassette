# 3. Record and replay a remote HTTP server

**When:** the MCP server your agent talks to is a remote Streamable HTTP endpoint, not a
local command.
**Prerequisites:** the `[http]` extra installed; network access to the real endpoint on
the recording run only.

## 3.1 Install the extra

```
uv add "mcp-cassette[http]"
```

The extra adds `httpx` and `h11`. Without it, `server_url()` raises `CassetteError` with
the import error text.

## 3.2 Use `server_url` instead of `server_command`

```python
def test_agent_reads_remote_tracker(mcp_cassette):
    url = mcp_cassette.server_url("https://mcp.example.com/mcp")
    result = run_my_agent(mcp_servers={"tracker": {"url": url}})
    assert "triaged" in result
```

`server_url()` returns a local URL of the form `http://127.0.0.1:<port>/mcp`:

- **Record run** — that URL is a recording proxy sitting in front of
  `https://mcp.example.com/mcp`.
- **Replay run** — it is a local mock Streamable HTTP server rebuilt from the cassette.
  The real endpoint is never contacted.
- **`new_episodes`** — replayed matches come from the cassette; misses go upstream live
  and are appended.

The server runs in a background thread owned by the session and is stopped, with the
cassette and report finalized, when the fixture tears down.

**Verify:** after the first run, `mcp-cassette inspect <cassette>` reports
`transport: http` and prints the recorded server host and exchange count.

## 3.3 Prove the remote is never contacted

Once the cassette exists, pass a URL that cannot resolve and re-run:

```python
url = mcp_cassette.server_url("https://dead.invalid/mcp")
```

The test still passes. That is the whole guarantee.

## 3.4 Headers and credentials

Every request header, `Authorization` included, is forwarded upstream during recording
but is **never written to the cassette**. Payload-level secrets are a separate concern —
see [8. Redact secrets](08-redact-secrets.md).

The server's `Mcp-Session-Id` is recorded as provenance only. Replay issues a fresh
session id and never reuses the recorded one.

## 3.5 Do not mix transports

A cassette carries its transport. Calling the wrong accessor raises `CassetteError`:

- `server_command()` against an `http` cassette → "use `mcp_cassette.server_url(...)`".
- `server_url()` against a `stdio` cassette → "use `mcp_cassette.server_command(...)`".

The check only applies once a cassette exists; on a fresh recording either accessor
decides the transport.

## 3.6 By hand, from the CLI

```
mcp-cassette record --cassette demo-http.json \
  --url https://mcp.example.com/mcp --port 8902 --max-idle 30
```

`--port` pins the proxy port so you know where to point the client; `--max-idle 30`
finalizes the cassette and exits after 30 seconds of client inactivity, so no interrupt
is needed. Then:

```
mcp-cassette serve demo-http.json --port 8902
```

`serve` infers the transport from the cassette. The chosen URL is printed on startup.

A worked, runnable version of this using the bundled sample servers is in
[`examples/README.md`](../../../examples/README.md).

## 3.7 Client requirements

Anything driving the replay server must behave like a Streamable HTTP MCP client:

- POST each JSON-RPC object to `/mcp` with `Content-Type: application/json` and
  `Accept: application/json, text/event-stream`.
- Capture the `Mcp-Session-Id` header from the `initialize` response and send it on every
  later request. Without it the replay server answers `404`, per the spec.
- Expect a JSON body for requests, and a bodyless `202` for notifications and
  client-side responses.
