# TS-01. Troubleshooting

Symptom to fix, for test authors. Operators should also see the
[runbook](operations/OP-05-runbook-replay-misses.md).

## TS-01.1 Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| `fixture 'mcp_cassette' not found` | The package is not installed in the environment pytest runs in. | Install it there; verify with `uv run pytest --fixtures -q \| grep mcp_cassette`. |
| `no cassette at <path> and recording is forbidden (mode=none)` | `MCP_CASSETTE_MODE=none` and no cassette exists. | Record one locally with `once` mode and commit it. |
| `recording captured zero messages — agent never spoke to the proxied server` | The command from `server_command()` never reached the agent. | Print `cmd` and confirm the agent launches exactly that list. |
| `replay had N unmatched request(s)` | The agent asked for something the cassette does not contain, or a param drifted. | See [the runbook](operations/OP-05-runbook-replay-misses.md#op-051-incident-1--replay-had-unmatched-requests); usually `ignore_params` or `new_episodes`. |
| Replay hangs on a `tools/call` and never returns | The cassette holds a server-initiated request (sampling/elicitation). Replay re-emits it and holds the recorded result until the agent answers — deliberately, with no internal timeout, so an agent that ignores it fails here exactly as it would against the real server. | Make the agent answer server-initiated requests. On shutdown the pending request is named; `mcp-cassette inspect <cassette> --timeline` shows it as a `<-` `request` line. |
| `invalid mcp_cassette mode 'X' from <source>; expected one of (...)` | A typo in `MCP_CASSETTE_MODE`, the marker, the ini option, or `use_cassette(mode=...)`. The message names which one. | Use `once`, `none`, `all`, or `new_episodes`. |
| `cassette <path> was recorded over Streamable HTTP; use mcp_cassette.server_url(...)` | Calling `server_command()` on an http cassette. | Switch to `server_url(real_url)`. |
| `cassette <path> was recorded over stdio; use mcp_cassette.server_command(...)` | Calling `server_url()` on a stdio cassette. | Switch to `server_command(real_cmd)`. |
| `faults apply to replay only; with_faults cannot run under a recording mode` | `with_faults()` while the mode resolves to record (no cassette yet, or `MCP_CASSETTE_MODE=all`). | Record the cassette first, then run the fault test in replay. |
| A fault seems to do nothing | Faults fire only *after* a request matches; the target method may not be in the cassette. | `mcp-cassette inspect <cassette> --faults <overlay>` — inert faults print a `WARNING`. |
| `cassette format_version N is newer than supported M` | The cassette was written by a newer mcp-cassette. | Upgrade the package. |
| `ImportError` mentioning `httpx` or `h11` when calling `server_url()` | The `[http]` extra is not installed. | `uv add "mcp-cassette[http]"`. |
| HTTP replay answers `404` | The client is not echoing the `Mcp-Session-Id` header from the `initialize` response. | Capture that header and send it on every later request. |
| `MCP_CASSETTE_MODE=all` turns tests red | Faults are replay-only and determinism assertions cannot hold while recording. | Refresh per file: delete the cassette and run in default `once` mode. |
| A recording never ends | `record` finishes on client EOF or a signal, and nothing closed the stream. | Close the client's stdin, interrupt it, or pass `--max-idle SECONDS`. |
| Recording was killed and the cassette is missing | The recording never finalized. | Recover `<cassette>.partial` — it is a valid cassette holding everything up to the last checkpoint. |
| A secret appears in a committed cassette | The value's key matched no redaction rule, or it lives inside a text body. | Rotate the credential, then re-record with a `--redact` JSON-pointer rule. See [HT-07. Redact secrets](how-to/HT-07-redact-secrets.md). |

## TS-01.2 A replay missed — read the timeline

Before guessing at `ignore_params`, look at what was actually recorded:

```
mcp-cassette inspect <cassette> --timeline --grep 'tools/call'
```

The timeline shows every recorded message in order with its method, id, and payload size,
so you can see whether the request your agent sent is absent, or present with different
params. If you re-recorded against an upgraded server, `mcp-cassette diff old.json
new.json` names what changed. See
[HT-06. Inspect and diff cassettes](how-to/HT-06-inspect-and-diff.md).

## TS-01.3 Still stuck

1. Look at the cassette. It is plain JSON — `mcp-cassette inspect <path> --timeline` for
   the shape, an editor for the exact bytes.
2. Reproduce outside pytest by running the same `serve` command by hand and piping
   requests into it; the runnable recipes are in
   [`examples/README.md`](../../examples/README.md).
3. File it at
   [github.com/cheneeheng/mcp-cassette](https://github.com/cheneeheng/mcp-cassette) with
   the cassette, the command, and the exact error.
