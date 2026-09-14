# Troubleshooting

Symptom to fix, for test authors. Operators should also see the
[runbook](operations/OP-05-runbook-replay-misses.md).

## Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| `mcp-cassette: command not found` (exit `127`) | The console script is installed inside the virtualenv, never on `PATH`. | Run it as `uv run mcp-cassette ...`, or activate the venv first. |
| `error: Requirement name 'mcp-cassette' matches project name` (exit `2`) | You ran `uv add mcp-cassette` inside a clone of this repo, where it is a self-dependency. | In a clone the install is `uv sync`. `uv add --dev` is for adding it to *your own* project. See [OP-01.2](operations/OP-01-install.md#op-012-install-the-package). |
| `<path> is not a cassette: expected JSON, but parsing failed at ...` | `serve`, `inspect`, `diff`, or `lint` was pointed at a file that is not a cassette. | Check the path. A cassette is the JSON file written by `mcp-cassette record --cassette PATH`. |
| `[Errno 2] No such file or directory: '<path>'` (exit `2`) | The cassette does not exist yet — nothing has recorded it, or the path is wrong. | Record it first: `mcp-cassette record --cassette <path> -- <your server>`. Under the pytest fixture, default `once` mode records it on the first run. |
| `inspect` prints `unanswered requests: N` | The recorded server never answered N client requests — usually because it failed to launch. This is a failed recording, not a short one. | Re-record. `record` returns the wrapped server's own exit code, so check it; the server's stderr is forwarded to yours and names the real cause. |
| `fixture 'mcp_cassette' not found` | The package is not installed in the environment pytest runs in. | Install it there; verify with `uv run pytest --fixtures -q \| grep mcp_cassette` (PowerShell: `\| Select-String mcp_cassette`). |
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
| `ImportError` mentioning `httpx` or `h11` when calling `server_url()` | The `[http]` extra is not installed. | `uv add --dev "mcp-cassette[http]"` in your own project; `uv sync` in a clone of this repo. |
| HTTP replay answers `404` | The client is not echoing the `Mcp-Session-Id` header from the `initialize` response. | Capture that header and send it on every later request. |
| `MCP_CASSETTE_MODE=all` turns tests red | Faults are replay-only and determinism assertions cannot hold while recording. | Refresh per file: delete the cassette and run in default `once` mode. |
| A recording never ends | `record` finishes on client EOF or a signal, and nothing closed the stream. | Close the client's stdin, interrupt it, or pass `--max-idle SECONDS`. |
| Recording was killed and the cassette is missing | The recording never finalized. | Recover `<cassette>.partial` — it is a valid cassette holding everything up to the last checkpoint. |
| A secret appears in a committed cassette | The value's key matched no redaction rule, or it lives inside a text body. | Rotate the credential, then re-record with a `--redact` JSON-pointer rule or a redaction pack. See [HT-07](how-to/HT-07-redact-secrets.md) and [HT-10](how-to/HT-10-redact-pii.md). `lint` reports what got through as `R005` ([HT-11](how-to/HT-11-detect-secrets.md)). |
| My recording vanished under `pytest -n auto` | Before 0.4.0, two workers recording one cassette path raced and the last save won. | Upgrade: writers now take a claim, so one records and the rest replay. See [OP-06](operations/OP-06-parallel-test-runs.md). |
| `cassette <path> is held for writing by ...` (exit `6`, or `CassetteError`) | Another live process is recording the same cassette. | Give each test its own cassette path, run with `-n0`, or retry. `record --force` breaks the claim when you know the holder is gone. See [OP-06](operations/OP-06-parallel-test-runs.md). |
| `use_cassette() was called from inside a running event loop` | The sync library door was entered from async code, where its blocking portal deadlocks. | Use `async with use_cassette_async(...)`. See [HT-03.9](how-to/HT-03-use-as-a-library.md#ht-039-async-code-use_cassette_async). |
| `redaction pack '<id>' (sha256 ...) named by the cassette's redaction manifest was not found` (exit `2`) | Replay cannot find the pack that scrubbed the recording: it moved, or its contents changed. | Pass it with `serve --pii-pack PATH` (`pii_packs=` from Python), or re-record. See [HT-10.5](how-to/HT-10-redact-pii.md#ht-105-replay-re-applies-the-same-scrubbing). |
| `MCP_CASSETTE_REDACT_SALT is not set, but --redact-salt-env ... needs it` (exit `2`) | `record --redact-salt-env` keys pseudonyms with that variable. | Set it, or drop the flag. See [HT-10.6](how-to/HT-10-redact-pii.md#ht-106-the-salt-and-what-a-pseudonym-does-not-protect). |
| `<path> carries no redaction manifest; re-record it with ...` (exit `4`) | `lint --require-redaction` (or the pre-commit redaction hook) found a cassette recorded without that profile. | Run the `record` command the message names. |
| `unknown rule id(s) '<id>' in --select (valid: ...)` (exit `2`) | A typo in `--select`, `--ignore`, or `[tool.mcp_cassette.lint]`. Before 0.4.0 it was silently ignored. | Use an id from the list the message prints. |
| The CI Action fails with `cannot resolve a merge-base` (exit `2`) | `actions/checkout` fetched one commit, so there is no history to find the base cassette in. | Set `fetch-depth: 0` on `actions/checkout`. See [OP-03.3.1](operations/OP-03-ci.md#op-0331-why-fetch-depth-0-is-required). |

## A replay missed — read the timeline

Before guessing at `ignore_params`, look at what was actually recorded:

```
mcp-cassette inspect tests/cassettes/test_agent/test_agent.mcp.json --timeline --grep 'tools/call'
```

The timeline shows every recorded message in order with its method, id, and payload size,
so you can see whether the request your agent sent is absent, or present with different
params. If you re-recorded against an upgraded server, `diff` names what changed:

```
mcp-cassette diff tests/cassettes/test_agent/test_agent.mcp.json fresh.mcp.json --tools-only
```

Both commands, with real output you can run from a clone, are in
[HT-06. Inspect and diff cassettes](how-to/HT-06-inspect-and-diff.md).

## Still stuck

1. Look at the cassette. It is plain JSON — `mcp-cassette inspect <path> --timeline` for
   the shape, an editor for the exact bytes.
2. Reproduce outside pytest by running the same `serve` command by hand and piping
   requests into it; the runnable recipes are in
   [`examples/README.md`](../../examples/README.md).
3. File it at
   [github.com/cheneeheng/mcp-cassette](https://github.com/cheneeheng/mcp-cassette) with
   the cassette, the command, and the exact error.
