# OP-04. CLI reference

[← Guide index](../index.md)

**Audience:** operators. The authoritative surface is `mcp-cassette <command> --help`;
this page mirrors it.

```
mcp-cassette record  --cassette PATH [--url URL] [flags] [-- CMD ...]
mcp-cassette serve   CASSETTE [flags] [-- CMD ...]
mcp-cassette inspect CASSETTE [--method METHOD] [--grep PATTERN] [--timeline | --tools] [--format text|json] [--faults PATH]
mcp-cassette diff    OLD NEW [--format text|json] [--tools-only]
mcp-cassette lint    CASSETTE... [--baseline PATH] [--require-redaction NAME] [--format text|json] [--annotate github] [--select RULE] [--ignore RULE] [--pattern-pack PATH] [--fail-on error|warning] [--no-config] [--entropy | --no-entropy] [--entropy-min-bits FLOAT] [--entropy-min-length N] [--entropy-allow STRING]
```

`python -m mcp_cassette ...` is equivalent to the `mcp-cassette` console script.

## OP-04.1 Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. For `lint`: no error-severity findings. |
| `2` | Usage error, or a cassette, fault overlay, or pack that is missing, unreadable, malformed, or has an unsupported `format_version`. Also: an unknown rule id in `--select`/`--ignore`, a redaction pack the cassette's manifest names that cannot be resolved, and `--redact-salt-env` without `MCP_CASSETTE_REDACT_SALT`. |
| `3` | `serve`: an unmatched request was received. |
| `4` | `lint`: at least one finding at or above `--fail-on` (default: error severity), or a cassette failing `--require-redaction`. |
| `5` | `diff`: the two cassettes differ. |
| `6` | `record`, `serve --new-episodes`: another live process holds the cassette for writing. Worth retrying; see [OP-06](OP-06-parallel-test-runs.md). |
| `130` | Recording interrupted by a signal; the cassette was finalized first. |
| other | `record`: the wrapped server's own exit code. |

## OP-04.2 `record`

Records a real server: wrap a stdio command after `--`, or proxy a remote URL. The two
are mutually exclusive, and one is required.

| Flag | Default | Effect |
|---|---|---|
| `--cassette PATH` | required | Where to write the cassette. |
| `--url URL` | — | Remote Streamable HTTP endpoint to record. Needs the `[http]` extra. |
| `--port N` | `0` (ephemeral) | Local port for the HTTP recording proxy. `--url` only. |
| `--max-idle SECONDS` | off | End the recording after this much client inactivity. `--url` only. |
| `--checkpoint-interval SECONDS` | `5` | Interval for `<cassette>.partial` checkpoints; `0` disables. |
| `--redact LOCATOR[=REPLACEMENT]` | — | Extra redaction rule. Repeatable. Key-glob, or JSON pointer if it starts with `/`. |
| `--no-default-redactions` | off | Disable the always-on defaults: the structural key rules and the bundled PII pack. |
| `--pii-pack PATH` | — | TOML redaction pack scrubbing free text. Repeatable; additive to the bundled pack. |
| `--redact-profile NAME` | — | Profile stamped on the cassette's redaction manifest, which `lint --require-redaction` checks. |
| `--redact-salt-env` | off | Key `hash` pseudonyms with `MCP_CASSETTE_REDACT_SALT` instead of the stable default. Re-records then produce different bytes. |
| `--force` | off | Break another live process's write claim, with a warning, instead of exiting `6`. |
| `--claim-wait SECONDS` | `0` | Wait this long for another writer's claim to be released before exiting `6`. |
| `--report PATH` | — | Write a JSON session report here. |

```
mcp-cassette record --cassette demo.json -- python tools/server.py
mcp-cassette record --cassette demo.json --url https://mcp.example.com/mcp --port 8902 --max-idle 30
mcp-cassette record --cassette demo.json --pii-pack team.toml --redact-profile team-baseline -- python tools/server.py
```

**Redaction flag combinations.**

| Combination | Result |
|---|---|
| `record --pii-pack` with `--no-default-redactions` | accepted: the flag drops the bundled rules, not packs you name |
| `record --redact-salt-env` without `MCP_CASSETTE_REDACT_SALT` | exit `2` naming the variable and the flag |
| `serve --pii-pack` on a cassette with no redaction manifest | exit `2`: there is nothing to resolve the pack against |
| `serve --pii-pack` naming a pack the manifest does not list | accepted and ignored with a note, so one command can serve several cassettes |

Packs and the salt are explained in [HT-10](../how-to/HT-10-redact-pii.md).

`--port` and `--max-idle` belong to the `--url` proxy; passing either with a stdio
`-- CMD` is a usage error (exit `2`) rather than a silently ignored flag.

`record` is a transparent proxy: it forwards whatever arrives on its own stdin to the
wrapped server. Nothing is captured unless a client drives it. The real server's stderr
is forwarded to yours, never swallowed.

**What `record` writes, and how to undo it.**

| Situation | Writes | Undo |
|---|---|---|
| `--cassette` path does not exist | the cassette, plus a `<cassette>.partial` sidecar while the session runs | delete the two files |
| `--cassette` path already exists | replaces the cassette wholesale at session end | **none — copy the file first** |

The overwrite has no undo because the recording unit is the whole session: there is no partial
update to roll back to. `record` says so on stderr before it starts, while you can still act on
it:

```
mcp-cassette record: demo.json already exists and will be replaced when this session ends; copy it first to keep it
```

The warning is advisory: the recording proceeds. Copy the file first, or record to a new
path and `diff` the two, if the existing cassette still matters. Interrupting does not
save you — `Ctrl+C` and `SIGTERM` both finalize the session before exiting, which is the
same overwrite. The one session that leaves the old file untouched is one that captured
zero messages, because that writes no file at all. The `<cassette>.partial` checkpoint
sidecar is replaced the same way, throughout the run.

## OP-04.3 `serve`

Stands up a replay server. The transport is inferred from the cassette.

| Flag | Default | Effect |
|---|---|---|
| `--port N` | `0` (ephemeral) | Port for an http cassette. The URL is printed on startup. |
| `--url URL` | cassette's `server_url` | Fall-through target for `--new-episodes` on an http cassette. |
| `--ordering per_method\|strict\|none` | `per_method` | Match ordering discipline. |
| `--ignore-param POINTER` | — | JSON pointer excluded from matching. Repeatable. |
| `--rewrite-protocol-version` | off | Answer `initialize` with the client's requested version. |
| `--faults PATH` | — | Fault overlay JSON sidecar. Replay-only: with `--new-episodes` it is a usage error (exit `2`). |
| `--pii-pack PATH` | — | A redaction pack the cassette's manifest names, when its recorded path no longer resolves. Repeatable. |
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
a stdio cassette is a usage error (exit `2`), as is `--faults` with `--new-episodes`.

## OP-04.4 `inspect`

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

`--timeline` and `--tools` each replace the whole text report, so they are mutually exclusive: passing both is a usage error (exit `2`) rather than a silently dropped view.

**Broken recordings announce themselves.** When the cassette holds client requests the
server never answered, the summary ends with an extra line, and the JSON document carries
the same number as `unanswered_requests`:

```
unanswered requests: 1 (the server never responded to these; replay exits 3 on one of them)
```

That is the signal a recording *failed* rather than merely being short — a session against
a server that never launched still captures the opening `initialize` request, so a
non-zero `messages` count alone cannot tell the two apart. The count is always taken over
the whole cassette, never the `--method`/`--grep` subset, so filtering cannot invent one.

```
mcp-cassette inspect demo.json
mcp-cassette inspect demo.json --timeline --grep 'tools/call'
mcp-cassette inspect demo.json --format json > summary.json
mcp-cassette inspect demo.json --faults demo.faults.json
```

## OP-04.5 `diff`

Structurally compares two cassettes: metadata, per-method counts, tool surfaces, and the
exchange sequence. JSON-RPC ids, `t_offset_ms`, and `seq` are never compared — they are
re-stamped or clock-derived.

| Flag | Default | Effect |
|---|---|---|
| `--format text\|json` | `text` | `json` is deterministic and diffable. |
| `--tools-only` | off | Compare tool surfaces only — the common CI use. When they match, the text report says `identical: no tool surface differences`, naming the narrowed question rather than claiming the whole cassette matched. |

```
mcp-cassette diff old.json new.json
mcp-cassette diff old.json new.json --tools-only
```

Exit `0` identical, `5` they differ, `2` a file would not load. `diff` is descriptive;
lint's `R002` is the gate. See
[HT-06. Inspect and diff cassettes](../how-to/HT-06-inspect-and-diff.md).

## OP-04.6 `lint`

Heuristic security scan of recorded tool descriptions and results.

Scope: the pattern rules read tool definitions from recorded `tools/list` responses (names,
descriptions, `inputSchema` descriptions and enum values) and text content from recorded
`tools/call` responses. `R005` additionally walks every string value in the cassette. A
cassette containing neither method can still only produce `R005` findings, and a clean lint
means nothing matched, not that the cassette is safe.

| Rule | Severity | What it catches |
|---|---|---|
| `R001` | error | Instruction-injection phrasing in a tool description. |
| `R002` | error | Description/schema drift versus a baseline — the "rug pull". Requires `--baseline`. |
| `R003` | warning | Duplicate tool names. |
| `R004` | warning | Instruction-shaped tool results. |
| `R005` | warning | High-entropy strings that look like secrets, anywhere in the cassette. See [HT-11](../how-to/HT-11-detect-secrets.md). |
| `R006` | warning | Injection phrasing in a tool name or in `inputSchema` descriptions and enum values. |
| `R007` | warning | Non-ASCII or mixed-script tool names — lookalike identifiers. |

| Flag | Default | Effect |
|---|---|---|
| `CASSETTE...` | required | One or more cassettes. With several, text output gains a header per file and JSON output is a list. |
| `--baseline PATH` | — | Older cassette to diff tool surfaces against; enables `R002`. One `CASSETTE` only. |
| `--require-redaction NAME` | — | Exit `4` unless every cassette's redaction manifest names this profile. The reason goes to stderr. |
| `--format text\|json` | `text` | `json` is deterministic and diffable — use it in CI. |
| `--annotate github` | off | Also print one GitHub Actions workflow command per finding, after the `--format` output. File-level; nothing when there are no findings. |
| `--select RULE` | all | Run only these rule ids. Repeatable. An unknown id exits `2`. |
| `--ignore RULE` | — | Skip these rule ids. Repeatable. `--select` wins on a conflict, with a printed note. An unknown id exits `2`. |
| `--pattern-pack PATH` | — | TOML pattern pack. Repeatable, and additive to the project config's packs. |
| `--fail-on error\|warning` | `error` | Lowest severity that exits `4`. Changes only the exit code, never a finding's severity. |
| `--no-config` | off | Ignore `[tool.mcp_cassette.lint]` in the nearest `pyproject.toml`. |
| `--entropy` / `--no-entropy` | on | Run `R005`. |
| `--entropy-min-bits FLOAT` | `3.5` | Lowest Shannon entropy per character `R005` reports. |
| `--entropy-min-length N` | `20` | Shortest token `R005` considers. |
| `--entropy-allow STRING` | — | Exact token `R005` never reports. Repeatable; replaces the project config's allowlist. |

`--annotate github` output, after the normal text:

```
::error file=examples/cassettes/tools-v2.mcp.json,title=R001::R001 /messages/4/payload/result/tools/0/description tool "echo": description matches injection pattern (override-instructions)
```

Packs extend the bundled rules; they never replace them. See
[HT-08. Lint with your own pattern packs](../how-to/HT-08-lint-pattern-packs.md).

Exit `0` when nothing meets the `--fail-on` threshold (warnings alone do not fail by
default), `4` otherwise. Every finding carries a JSON-pointer locator into the cassette.

> Heuristic pattern rules, not a guarantee — a clean lint is the absence of *known*
> smells, nothing more.

---

[← OP-03 CI pipeline](OP-03-ci.md) · [Guide index](../index.md) · [OP-05 Runbook: replay misses and failed recordings →](OP-05-runbook-replay-misses.md)
