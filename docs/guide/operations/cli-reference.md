# CLI reference

**Audience:** operators. The authoritative surface is `mcp-cassette <command> --help`;
this page mirrors it.

```
mcp-cassette record  --cassette PATH [--url URL] [flags] [-- CMD ...]
mcp-cassette serve   CASSETTE [flags] [-- CMD ...]
mcp-cassette inspect CASSETTE [--method METHOD] [--faults PATH]
mcp-cassette lint    CASSETTE [--baseline PATH] [--format text|json] [--select RULE] [--ignore RULE]
```

`python -m mcp_cassette ...` is equivalent to the `mcp-cassette` console script.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. For `lint`: no error-severity findings. |
| `2` | Usage error, or a cassette that is missing or has an unsupported `format_version`. |
| `3` | `serve`: an unmatched request was received. |
| `4` | `lint`: at least one error-severity finding. |
| `130` | Recording interrupted by a signal; the cassette was finalized first. |
| other | `record`: the wrapped server's own exit code. |

## `record`

Records a real server: wrap a stdio command after `--`, or proxy a remote URL. The two
are mutually exclusive, and one is required.

| Flag | Default | Effect |
|---|---|---|
| `--cassette PATH` | required | Where to write the cassette. |
| `--url URL` | — | Remote Streamable HTTP endpoint to record. Needs the `[http]` extra. |
| `--port N` | `0` (ephemeral) | Local port for the HTTP recording proxy. |
| `--max-idle SECONDS` | off | End the recording after this much client inactivity. |
| `--checkpoint-interval SECONDS` | `5` | Interval for `<cassette>.partial` checkpoints; `0` disables. |
| `--redact LOCATOR[=REPLACEMENT]` | — | Extra redaction rule. Repeatable. Key-glob, or JSON pointer if it starts with `/`. |
| `--no-default-redactions` | off | Disable the always-on default rule set. |
| `--report PATH` | — | Write a JSON session report here. |

```
mcp-cassette record --cassette demo.json -- python tools/server.py
mcp-cassette record --cassette demo.json --url https://mcp.example.com/mcp --port 8902 --max-idle 30
```

`record` is a transparent proxy: it forwards whatever arrives on its own stdin to the
wrapped server. Nothing is captured unless a client drives it. The real server's stderr
is forwarded to yours, never swallowed.

## `serve`

Stands up a replay server. The transport is inferred from the cassette.

| Flag | Default | Effect |
|---|---|---|
| `--port N` | `0` (ephemeral) | Port for an http cassette. The URL is printed on startup. |
| `--url URL` | cassette's `server_url` | Fall-through target for `--new-episodes` on an http cassette. |
| `--ordering per_method\|strict\|none` | `per_method` | Match ordering discipline. |
| `--ignore-param POINTER` | — | JSON pointer excluded from matching. Repeatable. |
| `--rewrite-protocol-version` | off | Answer `initialize` with the client's requested version. |
| `--faults PATH` | — | Fault overlay JSON sidecar. |
| `--new-episodes` | off | Replay matches; send misses to the real server and append them. Needs `-- CMD` for a stdio cassette. |
| `--report PATH` | — | Write a JSON session report here. |

```
mcp-cassette serve demo.json
mcp-cassette serve demo.json --faults demo.faults.json
mcp-cassette serve demo.json --new-episodes -- python tools/server.py
```

Replay answers requests but emits nothing on its own — it needs a client. `--url` against
a stdio cassette is a usage error (exit `2`).

## `inspect`

Human-readable cassette summary: format version, transport, timestamp, protocol version,
server identity, per-method message counts, and the timing span. For http cassettes it
also prints the recorded server host and exchange count.

| Flag | Effect |
|---|---|
| `--method METHOD` | Summarize only messages for this method. |
| `--faults PATH` | Dry-run an overlay: print which recorded requests it hits, and `WARNING` for faults that match nothing. |

```
mcp-cassette inspect demo.json
mcp-cassette inspect demo.json --faults demo.faults.json
```

## `lint`

Heuristic security scan of recorded tool descriptions and results.

| Rule | Severity | What it catches |
|---|---|---|
| `R001` | error | Instruction-injection phrasing in a tool description. |
| `R002` | error | Description/schema drift versus a baseline — the "rug pull". Requires `--baseline`. |
| `R003` | warning | Duplicate tool names. |
| `R004` | warning | Instruction-shaped tool results. |

| Flag | Default | Effect |
|---|---|---|
| `--baseline PATH` | — | Older cassette to diff tool surfaces against; enables `R002`. |
| `--format text\|json` | `text` | `json` is deterministic and diffable — use it in CI. |
| `--select RULE` | all | Run only these rule ids. Repeatable. |
| `--ignore RULE` | — | Skip these rule ids. Repeatable. |

Exit `0` when no error-severity finding exists (warnings alone do not fail), `4`
otherwise. Every finding carries a JSON-pointer locator into the cassette.

> These are heuristic pattern rules, not a guarantee. A clean lint is the absence of
> *known* smells, nothing more.
