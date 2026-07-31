# OP-02. Configuration

**Audience:** operators. Every setting that changes record/replay behaviour, its default,
and its effect.

## OP-02.0 One setting, three spellings

Most settings exist on all three doors under different names. Find the row, then read the
detail section it points at.

| Setting | pytest marker / ini | `use_cassette` | CLI | Detail |
|---|---|---|---|---|
| record mode | `mode=` / `mcp_cassette_mode` | `mode=` | pick `record` or `serve` | [OP-02.1](#op-021-record-mode) |
| cassette path | `cassette=` / `mcp_cassette_dir` | `cassette` argument | `--cassette PATH` / positional | [OP-02.2](#op-022-ini-options) |
| match ordering | `ordering=` | `MatchConfig(ordering=)` | `--ordering` | [OP-02.4](#op-024-matching) |
| ignore a param | `ignore_params=` | `MatchConfig(ignore_params=)` | `--ignore-param` | [OP-02.4](#op-024-matching) |
| protocol rewrite | `rewrite_protocol_version=` | `MatchConfig(rewrite_protocol_version=)` | `--rewrite-protocol-version` | [OP-02.4](#op-024-matching) |
| faults | `with_faults(...)` | `faults=` | `--faults PATH` | [HT-04](../how-to/HT-04-inject-faults.md) |
| pacing | `pace=`, `pace_scale=`, `pace_cap_ms=` | `pace=PaceConfig(...)` | `--pace`, `--pace-scale`, `--pace-cap-ms` | [HT-05](../how-to/HT-05-replay-timing.md) |
| redaction | **not available** | **not available** | `--redact`, `--no-default-redactions` | [OP-02.5](#op-025-redaction) |
| checkpoint interval | not available | not available | `--checkpoint-interval` | [OP-02.6](#op-026-checkpointing) |

Two rows are deliberately uneven. `MCP_CASSETTE_MODE` is the only genuinely cross-door
*environment* setting — all three doors delegate to `resolve_mode`, which is what makes the
CI `none` invariant hold everywhere. Redaction and checkpointing are record-time proxy
settings and are reachable only from the CLI or from `StdioRecordingProxy` directly; see
[HT-07.3](../how-to/HT-07-redact-secrets.md#ht-073-the-gap-in-the-fixture-and-use_cassette)
for what to do when you need a custom rule from a pytest suite.

## OP-02.1 Record mode

Precedence, highest first: `MCP_CASSETTE_MODE` (env) → marker `mode=` →
`mcp_cassette_mode` (ini) → default `once`. Resolved at fixture setup.

The mode decides, once per test run, whether that run records or replays; the recording
unit is always the entire session, never an individual tool call (see §HT-01.3).

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

## OP-02.2 ini options

Set in `pyproject.toml` under `[tool.pytest.ini_options]`, or in `pytest.ini` / `setup.cfg`.

| Option | Default | Effect |
|---|---|---|
| `mcp_cassette_mode` | `once` | Suite-wide default record mode. |
| `mcp_cassette_dir` | `""` (means `<rootpath>/tests/cassettes`) | Base directory for generated cassette paths. A relative value resolves against pytest's `rootpath`, not the current directory, so the same cassettes are found from any subdirectory. |

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

`pytest -o mcp_cassette_dir=/mnt/cassettes` overrides the ini value for a single
invocation — pytest's own mechanism, no mcp-cassette flag involved.

**`mcp_cassette_dir` is fixture-only, and there is no `MCP_CASSETTE_DIR` env var.** The
fixture is the one door that *derives* a cassette path, because a test node name is the
only thing that can name a cassette automatically; the base directory exists solely to be
joined onto that derivation. The other two doors take the full path from you:

| Door | Cassette named by |
|---|---|
| pytest fixture | derived — `<mcp_cassette_dir>/<module>/<node name>.mcp.json` |
| `mcp-cassette record` / `serve` | `--cassette PATH` / positional `PATH` |
| `use_cassette(...)` | the `cassette` argument |

So configure the directory where it belongs — in the path you pass:

```python
CASSETTES = Path(os.environ.get("MY_CASSETTE_DIR", "cassettes"))
with use_cassette(CASSETTES / "search.mcp.json") as session:
    ...
```

This is the opposite of `MCP_CASSETTE_MODE`, which is genuinely cross-door: `resolve_mode`
reads it and all three doors delegate there, so the `none` invariant holds everywhere. A
directory env var would reach exactly one door of three.

## OP-02.3 Marker options

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

## OP-02.4 Matching

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

## OP-02.5 Redaction

Always-on default rules (key-globs, case-insensitive, replacement `REDACTED`):
`*token*`, `*secret*`, `*password*`, `*apikey*`, `*api_key*`, `authorization`.

- Add rules: `--redact LOCATOR[=REPLACEMENT]`, repeatable. A locator starting with `/` is
  a JSON pointer; anything else is a key-glob.
- Turn defaults off: `--no-default-redactions`.

Details and limits: [HT-07. Redact secrets](../how-to/HT-07-redact-secrets.md).

## OP-02.6 Checkpointing

While a recording runs, the session is written periodically to a `<cassette>.partial`
sidecar so a hard kill loses only the tail.

| Flag | Default | Effect |
|---|---|---|
| `--checkpoint-interval SECONDS` | `5` | Seconds between checkpoints. `0` disables. |

The sidecar is a valid cassette: inspect it, or rename it over the real path to keep it.
It is removed when the recording finalizes normally. It is deliberately **never** written
to the cassette path itself, because `once` mode decides record-vs-replay by that file's
existence and a truncated file there would replay as a finished recording.

## OP-02.7 Unattended recording

| Flag | Default | Effect |
|---|---|---|
| `--max-idle SECONDS` | off | End the recording after this much client inactivity. |

Recording otherwise ends on client EOF or on an interrupt signal. `--max-idle` is the
escape hatch for a recording run with nobody around to press Ctrl+C.

## OP-02.8 Shutdown behaviour

Proxy shutdown is signal-driven: SIGINT/SIGTERM on POSIX, SIGINT/SIGBREAK on Windows.
Both platforms converge on the same path — terminate the child, finalize the cassette,
exit `130`. SIGTERM has no graceful-finalize semantics on Windows.

Off the main thread, where no signal handler can be installed, shutdown degrades to
EOF-driven: close the client's stdin to end the session.
