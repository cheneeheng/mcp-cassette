# 14. CLI reference

**Audience:** operators. The authoritative surface is `mcp-cassette <command> --help`;
this page mirrors it.

```
mcp-cassette record  --cassette PATH [--url URL] [flags] [-- CMD ...]
mcp-cassette serve   CASSETTE [flags] [-- CMD ...]
mcp-cassette inspect CASSETTE [--method METHOD] [--grep PATTERN] [--timeline] [--tools] [--format text|json] [--faults PATH]
mcp-cassette diff    OLD NEW [--format text|json] [--tools-only]
mcp-cassette lint    CASSETTE [--baseline PATH] [--format text|json] [--select RULE] [--ignore RULE] [--pattern-pack PATH] [--fail-on error|warning] [--no-config]
```

`python -m mcp_cassette ...` is equivalent to the `mcp-cassette` console script.

## 14.1 Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. For `lint`: no error-severity findings. |
| `2` | Usage error, or a cassette that is missing or has an unsupported `format_version`. |
| `3` | `serve`: an unmatched request was received. |
| `4` | `lint`: at least one finding at or above `--fail-on` (default: error severity). |
| `5` | `diff`: the two cassettes differ. |
| `130` | Recording interrupted by a signal; the cassette was finalized first. |
| other | `record`: the wrapped server's own exit code. |

## 14.2 `record`

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

## 14.3 `serve`

Stands up a replay server. The transport is inferred from the cassette.

| Flag | Default | Effect |
|---|---|---|
| `--port N` | `0` (ephemeral) | Port for an http cassette. The URL is printed on startup. |
| `--url URL` | cassette's `server_url` | Fall-through target for `--new-episodes` on an http cassette. |
| `--ordering per_method\|strict\|none` | `per_method` | Match ordering discipline. |
| `--ignore-param POINTER` | — | JSON pointer excluded from matching. Repeatable. |
| `--rewrite-protocol-version` | off | Answer `initialize` with the client's requested version. |
| `--faults PATH` | — | Fault overlay JSON sidecar. |
| `--pace none\|recorded` | `none` | Replay recorded inter-message latency. Off by default — replay is instant. |
| `--pace-scale FLOAT` | `1.0` | Multiply every recorded gap. Must be `> 0`. Requires `--pace recorded`. |
| `--pace-cap-ms MS` | `5000` | Per-gap upper bound; `0` is uncapped. Requires `--pace recorded`. |
| `--new-episodes` | off | Replay matches; send misses to the real server and append them. Needs `-- CMD` for a stdio cassette. |
| `--report PATH` | — | Write a JSON session report here. |

```
mcp-cassette serve demo.json
mcp-cassette serve demo.json --faults demo.faults.json
mcp-cassette serve demo.json --new-episodes -- python tools/server.py
```

Replay answers requests but emits nothing on its own — it needs a client. `--url` against
a stdio cassette is a usage error (exit `2`).

## 14.4 `inspect`

Human-readable cassette summary: format version, transport, timestamp, protocol version,
server identity, per-method message counts, and the timing span. For http cassettes it
also prints the recorded server host and exchange count.

| Flag | Effect |
|---|---|
| `--method METHOD` | Summarize only messages for this method. |
| `--grep PATTERN` | Python regex matched against each message payload. Composes with `--method` (both must match). Invalid regex exits `2`. |
| `--timeline` | One line per message: `seq`, `t_offset_ms`, direction, kind, method, id, payload bytes. HTTP cassettes add `exch` and `chan`. |
| `--tools` | One line per recorded tool, deduplicated by name (last seen wins). |
| `--format text\|json` | `json` emits one deterministic, byte-stable document; add `--timeline` to include the rows. |
| `--faults PATH` | Dry-run an overlay: print which recorded requests it hits, and `WARNING` for faults that match nothing. |

```
mcp-cassette inspect demo.json
mcp-cassette inspect demo.json --timeline --grep 'tools/call'
mcp-cassette inspect demo.json --format json > summary.json
mcp-cassette inspect demo.json --faults demo.faults.json
```

## 14.5 `diff`

Structurally compares two cassettes: metadata, per-method counts, tool surfaces, and the
exchange sequence. JSON-RPC ids, `t_offset_ms`, and `seq` are never compared — they are
re-stamped or clock-derived.

| Flag | Default | Effect |
|---|---|---|
| `--format text\|json` | `text` | `json` is deterministic and diffable. |
| `--tools-only` | off | Compare tool surfaces only — the common CI use. |

```
mcp-cassette diff old.json new.json
mcp-cassette diff old.json new.json --tools-only
```

Exit `0` identical, `5` they differ, `2` a file would not load. `diff` is descriptive;
lint's `R002` is the gate. See
[7. Inspect and diff cassettes](../how-to/07-inspect-and-diff.md).

## 14.6 `lint`

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
| `--ignore RULE` | — | Skip these rule ids. Repeatable. `--select` wins on a conflict, with a printed note. |
| `--pattern-pack PATH` | — | TOML pattern pack. Repeatable, and additive to the project config's packs. |
| `--fail-on error\|warning` | `error` | Lowest severity that exits `4`. Changes only the exit code, never a finding's severity. |
| `--no-config` | off | Ignore `[tool.mcp_cassette.lint]` in the nearest `pyproject.toml`. |

Packs extend the bundled rules; they never replace them. See
[9. Lint with your own pattern packs](../how-to/09-lint-pattern-packs.md).

Exit `0` when nothing meets the `--fail-on` threshold (warnings alone do not fail by
default), `4` otherwise. Every finding carries a JSON-pointer locator into the cassette.

> Heuristic pattern rules, not a guarantee — a clean lint is the absence of *known*
> smells, nothing more.
