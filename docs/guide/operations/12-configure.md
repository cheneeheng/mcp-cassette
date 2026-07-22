# 12. Configuration

**Audience:** operators. Every setting that changes record/replay behaviour, its default,
and its effect.

## 12.1 Record mode

Precedence, highest first: `MCP_CASSETTE_MODE` (env) → marker `mode=` →
`mcp_cassette_mode` (ini) → default `once`. Resolved at fixture setup.

Valid values are `once`, `none`, `all`, `new_episodes`. Anything else raises
`ValueError: invalid mcp_cassette mode <value>; expected one of ('once', 'none', 'all',
'new_episodes')`.

| Mode | Cassette absent | Cassette present | Use it for |
|---|---|---|---|
| `once` (default) | record | replay | local development, the default |
| `none` | fail — recording is forbidden | replay | **CI** — forbids recording outright |
| `all` | record | re-record | deliberate refresh of a whole file |
| `new_episodes` | record | replay; misses fall through to the real server and are appended | incrementally extending a recording |

The environment variable is read at fixture setup and nothing is cached at module level,
so `monkeypatch.setenv` works within a session.

## 12.2 ini options

Set in `pyproject.toml` under `[tool.pytest.ini_options]`, or in `pytest.ini` / `setup.cfg`.

| Option | Default | Effect |
|---|---|---|
| `mcp_cassette_mode` | `once` | Suite-wide default record mode. |
| `mcp_cassette_dir` | `""` (means `<rootpath>/tests/cassettes`) | Base directory for generated cassette paths. |

```toml
[tool.pytest.ini_options]
mcp_cassette_mode = "once"
mcp_cassette_dir = "tests/fixtures/cassettes"
```

Cassette path when the marker gives no explicit `cassette=`:

```
<mcp_cassette_dir>/<test module stem>/<sanitized test node name>.mcp.json
```

Sanitizing replaces every run of characters outside `A-Za-z0-9_.-` with a single `_`, so
parametrized tests get distinct files.

## 12.3 Marker options

```python
@pytest.mark.mcp_cassette(
    mode="none",
    cassette="tests/cassettes/shared/github.mcp.json",
    ordering="strict",
    ignore_params=["/params/arguments/requestId"],
    rewrite_protocol_version=True,
)
```

| Keyword | Default | Effect |
|---|---|---|
| `mode` | (falls through to ini) | Record mode for this test. |
| `cassette` | derived path | Explicit cassette path. |
| `ordering` | `per_method` | Match ordering discipline. |
| `ignore_params` | `[]` | JSON pointers excluded from the match key. |
| `rewrite_protocol_version` | `False` | Answer `initialize` with the client's requested `protocolVersion` instead of the recorded one. |

## 12.4 Matching

`MatchConfig` fields, all also reachable from the CLI `serve` flags:

| Field | Default | Notes |
|---|---|---|
| `match_on` | `["method", "params"]` | The JSON-RPC `id` is **never** matched on; replay re-stamps the client's id onto the recorded response. |
| `ignore_params` | `[]` | JSON pointers dropped before the key is computed. CLI: `--ignore-param` (repeatable). |
| `ordering` | `per_method` | CLI: `--ordering per_method\|strict\|none`. |
| `on_unmatched` | `error` | Unmatched requests are always an error; the replay process exits `3`. |
| `rewrite_protocol_version` | `False` | CLI: `--rewrite-protocol-version`. |

Three ordering disciplines:

| `ordering` | Behaviour |
|---|---|
| `per_method` (default) | Answer with the earliest unconsumed exchange whose match key is equal; mark it consumed. Repeat calls to the same method replay in recorded order. |
| `strict` | The next unconsumed exchange must match, or the request is a miss. |
| `none` | Any matching exchange answers, unlimited times, in any order. |

## 12.5 Redaction

Always-on default rules (key-globs, case-insensitive, replacement `REDACTED`):
`*token*`, `*secret*`, `*password*`, `*apikey*`, `*api_key*`, `authorization`.

- Add rules: `--redact LOCATOR[=REPLACEMENT]`, repeatable. A locator starting with `/` is
  a JSON pointer; anything else is a key-glob.
- Turn defaults off: `--no-default-redactions`.

Details and limits: [8. Redact secrets](../how-to/08-redact-secrets.md).

## 12.6 Checkpointing

While a recording runs, the session is written periodically to a `<cassette>.partial`
sidecar so a hard kill loses only the tail.

| Flag | Default | Effect |
|---|---|---|
| `--checkpoint-interval SECONDS` | `5` | Seconds between checkpoints. `0` disables. |

The sidecar is a valid cassette: inspect it, or rename it over the real path to keep it.
It is removed when the recording finalizes normally. It is deliberately **never** written
to the cassette path itself, because `once` mode decides record-vs-replay by that file's
existence and a truncated file there would replay as a finished recording.

## 12.7 Unattended recording

| Flag | Default | Effect |
|---|---|---|
| `--max-idle SECONDS` | off | End the recording after this much client inactivity. |

Recording otherwise ends on client EOF or on an interrupt signal. `--max-idle` is the
escape hatch for a recording run with nobody around to press Ctrl+C.

## 12.8 Shutdown behaviour

Proxy shutdown is signal-driven: SIGINT/SIGTERM on POSIX, SIGINT/SIGBREAK on Windows.
Both platforms converge on the same path — terminate the child, finalize the cassette,
exit `130`. SIGTERM has no graceful-finalize semantics on Windows.

Off the main thread, where no signal handler can be installed, shutdown degrades to
EOF-driven: close the client's stdin to end the session.
