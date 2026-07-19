# Examples

Runnable, self-contained demos of mcp-cassette. Nothing here needs the official `mcp`
SDK — the sample servers speak raw JSON-RPC (newline-delimited over stdio, JSON bodies
over Streamable HTTP), which is exactly the transport level mcp-cassette records and
replays.

## Files

| File | What it is |
|---|---|
| `echo_server.py` | An MCP-style stdio server (stdlib only): tools `echo`, `add`, and `summarize`. `echo` returns a random per-call token to make non-determinism visible; `summarize` asks the *client* to sample a summary mid-call (a server-initiated request). |
| `echo_http_server.py` | The same server exposed over Streamable HTTP (stdlib only): one `POST /mcp` endpoint, JSON response mode. The "remote server" for the HTTP examples. |
| `mcp_client.py` | A tiny transport-level JSON-RPC stdio client, including answering server-initiated requests. |
| `mcp_http_client.py` | Its Streamable HTTP twin: POSTs JSON-RPC, echoes the issued `Mcp-Session-Id`. |
| `test_echo.py` | Four pytest examples built on the `mcp_cassette` fixture (stdio). |
| `test_echo_http.py` | One pytest example built on `mcp_cassette.server_url` (Streamable HTTP; needs the `[http]` extra — the repo's dev group has it). |
| `cassettes/` | The committed cassettes those tests replay, plus two for the lint demo: `tools.mcp.json` (a clean `tools/list` recording) and `injected.mcp.json` (the same recording with a deliberately poisoned tool description). |

## Run them

From the repo root:

```
uv run pytest examples/                        # replay the committed cassettes (offline, deterministic)
MCP_CASSETTE_MODE=none uv run pytest examples/ # same, but forbid recording (what CI does)

# refresh one cassette: delete it, then a normal run re-records just that one
rm examples/cassettes/echo_and_add.mcp.json && uv run pytest examples/
```

The main test suite (`uv run pytest`) does **not** collect `examples/` — `testpaths` is
`tests`, so these stay standalone.

**On re-recording:** `MCP_CASSETTE_MODE=all` force-records *every* test, so it can't
produce a green run here — `test_replay_is_deterministic` asserts a stable token that
only holds on replay, `test_survives_injected_error` uses faults (replay-only), and
`test_http_echo_and_add` only starts its live server when its cassette is missing.
Refresh cassettes per-file instead (delete + default `once` mode, as above).
`fault.mcp.json` can't be regenerated through its own test at all; record it from a
plain `echo` session — e.g. the CLI `record` command below. The lint-demo cassettes
are also CLI-recorded, not test-recorded (see the lint section).

## What each test shows

- **`test_echo_and_add`** — the core loop. First run (no cassette) records through the
  proxy; every run after replays offline. The agent command is the only thing that
  changes, and the fixture builds it for you.
- **`test_replay_is_deterministic`** — `echo` mints a fresh random token per call, yet two
  replays of the cassette return the *same* token. A live server would not. That
  stability is the entire point of a cassette.
- **`test_survives_injected_error`** — `with_faults` overlays a fault at replay time so
  `tools/call` returns a JSON-RPC error, without touching the recorded cassette. One
  recording drives a whole resilience matrix. Faults fire *after* a request matches, so
  the faulted call must still correspond to a recorded interaction.
- **`test_server_initiated_sampling`** (v2) — the `summarize` tool sends a
  `sampling/createMessage` request *to the client* and only answers once the client
  responds. Replay re-emits the recorded sampling request, accepts whatever the client
  answers (the answer comes from an LLM and legitimately differs every run — it is
  never matched against the recording), and only then releases the recorded tool
  result. Change the canned answer in `_answer_sampling` and replay still returns the
  recorded summary.
- **`test_http_echo_and_add`** (v2) — `mcp_cassette.server_url(real_url)` is the HTTP
  analog of `server_command`: the fixture hands back a local `/mcp` URL to plug into
  the agent's config. First run it is a recording proxy in front of the real server;
  every run after it is a local mock server rebuilt from the cassette — the test then
  passes a *dead* URL to prove the remote is never contacted.

## Try it by hand (the CLI)

`record` is a transparent proxy: it spawns the real server after `--` and forwards
whatever arrives on its own stdin. So to record something you have to *drive it* — send
JSON-RPC requests in, exactly as a real MCP client would. The easiest way by hand is to
pipe newline-delimited requests into the proxy's stdin:

Replay is a server, not a player — it answers requests from the cassette but emits nothing on its own, so `serve` needs the same piped requests to have anything to respond to; the pipe stands in for the client.

```bash
# record a live session — pipe requests in so there is actually traffic to capture
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}' \
  | mcp-cassette record --cassette demo.mcp.json -- python examples/echo_server.py

# inspect what was captured
mcp-cassette inspect demo.mcp.json

# replay it as a drop-in mock server (no subprocess, no randomness).
# same trick to send it a request — replay answers from the cassette, offline:
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}' \
  | mcp-cassette serve demo.mcp.json
```

The same thing in Windows PowerShell — `printf` and `\` continuations aren't native, so
pipe an array of single-quoted lines instead (single quotes keep PowerShell from mangling
the `"` inside the JSON):

```powershell
# record a live session — each array element is sent as one line on the proxy's stdin
@(
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}'
  '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}'
) | mcp-cassette record --cassette demo.mcp.json -- python examples/echo_server.py

# inspect what was captured
mcp-cassette inspect demo.mcp.json

# replay it offline; same array trick to drive it
@(
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}}}'
  '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}'
) | mcp-cassette serve demo.mcp.json
```

Piping closes stdin at EOF, which cleanly ends the session. In a real test the client is
your agent, not `printf` — the fixture just hands it `mcp-cassette record ... -- <server>`
(or `mcp-cassette serve <cassette>`) as the server command and the agent drives it.

### Over HTTP (v2)

The HTTP flavors take a URL instead of a command, and *serve* one instead of speaking
stdio — so unlike the pipes above, driving them by hand takes an HTTP client. The
bundled `mcp_http_client.py` is exactly that. Run everything below **from the
`examples/` directory** so the `python -c` imports resolve; on Windows, run each
`&`-backgrounded command in its own terminal instead (the snippets themselves work
unchanged).

**Record.** Start the "remote" server, put the recording proxy in front of it, and
send a session *through the proxy*. `--port` pins the proxy's port so the client
knows where to go; `--max-idle 30` finalizes the cassette and exits the proxy after
thirty quiet seconds, so no Ctrl+C is needed — just don't dawdle before sending
traffic, or raise the value:

```bash
python echo_http_server.py --port 8901 &                   # the "remote" server

mcp-cassette record --cassette demo-http.mcp.json \
  --url http://127.0.0.1:8901/mcp --port 8902 --max-idle 30 &
# -> mcp-cassette: recording at http://127.0.0.1:8902/mcp -> point the agent there

# simulate the agent: a scripted session against the PROXY (8902), not the server
python -c "
from mcp_client import initialize, tool_call
from mcp_http_client import run
for obj in run('http://127.0.0.1:8902/mcp',
               [*initialize(), tool_call(2, 'add', {'a': 2, 'b': 3})]):
    print(obj)
"

# ~30 idle seconds later the proxy exits and writes the cassette
mcp-cassette inspect demo-http.mcp.json
```

**Replay.** Kill the real server (`kill %1`) — replay never contacts it. `serve`
infers the transport from the cassette and stands up a local mock HTTP server;
drive it with the *same* client at the *same* URL, offline:

```bash
mcp-cassette serve demo-http.mcp.json --port 8902 &
# -> mcp-cassette: replaying at http://127.0.0.1:8902/mcp

python -c "
from mcp_client import initialize, tool_call
from mcp_http_client import run
for obj in run('http://127.0.0.1:8902/mcp',
               [*initialize(), tool_call(2, 'add', {'a': 2, 'b': 3})]):
    print(obj)
"

kill %2    # replay serves until interrupted; stop it when done
```

What a client has to get right (`mcp_http_client.py` does all of this, ~20 lines):

- POST each JSON-RPC object to `/mcp` with `Content-Type: application/json` and
  `Accept: application/json, text/event-stream`.
- Capture the `Mcp-Session-Id` header from the `initialize` response and echo it on
  every later request — per the Streamable HTTP spec the replay server answers `404`
  without it.
- Requests get a JSON body back; notifications and client responses get a bodyless
  `202`.

The same handshake in raw `curl`, to see the session mechanics on the wire:

```bash
curl -si http://127.0.0.1:8902/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
# note the mcp-session-id response header; every later request must send it back:
#   -H 'mcp-session-id: <value from above>'
```

## Linting your cassettes (v2)

Recorded tool descriptions and results are third-party content headed for a model's
context window; `lint` scans them for known injection smells before they get there.
`cassettes/tools.mcp.json` is a clean `tools/list` recording;
`cassettes/injected.mcp.json` is a copy with one tool description deliberately
poisoned — exactly the artifact rules R001 (injection) and R002 (the "rug pull" drift
vs a baseline) exist to catch:

```bash
mcp-cassette lint examples/cassettes/tools.mcp.json        # clean: exit 0
mcp-cassette lint examples/cassettes/injected.mcp.json     # 3 x R001 (error): exit 4

# drift detection: yesterday's recording as the baseline for today's
mcp-cassette lint examples/cassettes/injected.mcp.json \
  --baseline examples/cassettes/tools.mcp.json             # + R002 with a unified diff

mcp-cassette lint examples/cassettes/injected.mcp.json --format json   # for CI
```

Each finding carries a JSON-pointer locator into the cassette (open it and jump
there). Exit `0` means no error-severity findings — warnings (R003 duplicate tool
names, R004 instruction-shaped result text) alone don't fail the run. These are
heuristic pattern rules, not a guarantee.

To regenerate the pair: record `tools.mcp.json` with the pipe trick above (send
`{"jsonrpc":"2.0","id":2,"method":"tools/list"}` as the third line), copy it to
`injected.mcp.json`, and plant something suspicious in a `description`.
