# Architecture

Living picture of `mcp-cassette` - "vcrpy for MCP". Record real MCP stdio/HTTP
sessions into cassettes, then replay them as deterministic mock servers. Diagrams
show what exists today; the Key Decisions log carries the durable "why".

## System context

Where the library sits between its three callers and a real MCP server.

```mermaid
flowchart LR
    suite(["Agent test suite (pytest)"]) -->|"mcp_cassette fixture / marker"| cass["mcp-cassette"]
    code(["Plain Python (harness, notebook, benchmark)"]) -->|"use_cassette()"| cass
    op(["Operator shell"]) -->|"record / serve / inspect / diff / lint"| cass
    cass -->|"record: proxy in front of"| server["Real MCP server (stdio or HTTP)"]
    cass -->|"replay: mock, no server"| suite
    cass <-->|"read / write"| file[("Cassette JSON (+ .faults.json)")]
```

## Components

Internal modules, grouped by record path, replay path, read-only analysis, and core.

```mermaid
flowchart TB
    cli["cli.py / __main__"] --> rec
    cli --> rep
    cli --> tools
    fixture["pytest_plugin.py"] --> session["session.py<br/>resolve_mode, mode -> command,<br/>use_cassette()"]
    library["plain Python caller"] --> session
    session --> report["report.py<br/>(sidecar)"]

    subgraph rec["Record"]
        proxy["record/proxy.py<br/>StdioRecordingProxy"] --> pump["record/pump.py<br/>line pumps"]
        proxy --> recorder["record/recorder.py<br/>SessionRecorder"]
        proxy --> ckpt["record/checkpoint.py<br/>.partial sidecar"]
        httpproxy["transports/http/proxy.py"] --> recorder
    end

    subgraph rep["Replay"]
        rserver["replay/server.py<br/>ReplayServer"] --> matching["matching.py<br/>Exchanges"]
        rserver --> faults["replay/faults.py"]
        rserver --> pacing["replay/pacing.py<br/>Pacer (opt-in)"]
        rserver --> sreq["replay/server_requests.py<br/>sampling / elicitation"]
        rserver --> newep["replay/new_episodes.py"]
        httpserver["transports/http/server.py"] --> matching
        httpserver --> pacing
    end

    subgraph tools["Read-only analysis"]
        diffing["diffing.py<br/>diff_cassettes"]
        lint["lint/engine + rules<br/>R001-R004"]
        packs["lint/packs.py<br/>TOML pattern packs,<br/>pyproject config"]
        packs --> lint
        lint --> diffing
    end

    subgraph core["Core"]
        cassette["cassette.py<br/>schema + redaction + IO"]
        signals["_signals.py"]
        stdio["_stdio.py<br/>unbuffered"]
    end

    session -->|"in-process for HTTP only;<br/>stdio gets a subprocess command"| rep
    recorder --> cassette
    matching --> cassette
    faults --> cassette
    pacing --> cassette
    diffing --> cassette
```

`diffing.py` depends on lint only for `latest_tools`, the shared tool-surface
extractor, so `diff` and rule R002 can never disagree about what a tool surface is.

## Record flow

Transparent proxy taps both directions, classifies, redacts, and finalizes.

```mermaid
sequenceDiagram
    participant A as Agent
    participant P as StdioRecordingProxy
    participant R as SessionRecorder
    participant S as Real MCP server
    A->>P: JSON-RPC request line
    P->>R: tap (classify, timestamp, redact copy)
    P->>S: forward verbatim
    S-->>P: response / notification line
    P->>R: tap
    P-->>A: forward verbatim
    Note over R: periodic checkpoint -> <cassette>.partial
    Note over P,R: on shutdown: finalize -> atomic Cassette save
```

## Replay flow

Answers from the recording; no server, no network, and no wall-clock unless paced.

```mermaid
sequenceDiagram
    participant A as Agent
    participant V as ReplayServer
    participant M as matching.py
    participant P as Pacer
    participant C as Cassette
    A->>V: JSON-RPC request line
    V->>M: match request per MatchConfig (id ignored)
    M->>C: locate Exchange
    C-->>V: recorded response + anchored notifications
    V->>P: wait(previous, current)
    Note over P: no-op when pacing off (default):<br/>no sleep, no clock read
    P-->>V: recorded gap elapsed
    V-->>A: response with re-stamped id
    Note over V: pace precedes faults, so an injected delay is additive
    Note over V: server-initiated requests emitted with recorded msg_id
    Note over V: unmatched request -> exit code 3
```

## Data model

Pydantic v2 schema. Only `Cassette` is persisted by us; overlays and configs sit
beside it and never mutate it.

```mermaid
erDiagram
    Cassette ||--o{ Message : contains
    Cassette ||--|| MatchConfig : configures
    Cassette ||--|| PaceConfig : configures
    Cassette ||--o{ RedactionRule : "redacts with"
    FaultOverlay ||--o{ Fault : holds
    Fault ||--|| FaultTarget : targets
    FaultOverlay }o..|| Cassette : "overlays (by path)"

    Cassette {
        int format_version "2 since v0.2.0"
        string transport
        string server_url
    }
    Message {
        string kind
        string channel
        int t_offset_ms "replayed only when paced"
    }
    MatchConfig {
        string ordering "per_method|strict|none"
    }
    PaceConfig {
        string mode "none|recorded"
        float scale
        int cap_ms "0 = uncapped"
    }
    Fault {
        string type "delay|timeout|error|malformed|disconnect"
    }
```

Read-only outputs - `LintReport`/`LintFinding` and `CassetteDiff` - are report models,
never written into a cassette. `PatternRule` and `ProjectLintConfig` are loaded from
user TOML (a pattern pack, or `[tool.mcp_cassette.lint]` in `pyproject.toml`) and are
never persisted by us.

## Key Decisions

### 2026-07-18 - Operate at the transport level, never import the mcp SDK at runtime

**Status:** Accepted
**Context:** The library must work with any MCP client/server unmodified. Parsing
or validating against the `mcp` SDK would couple every consumer to it and break on
protocol drift.
**Decision:** Treat messages semi-opaquely - capture verbatim newline-delimited
JSON-RPC whatever the method. Runtime dependencies are only `anyio` and `pydantic`;
the `mcp` SDK is a dev-only dependency used by the reference test server.
**Consequences:** Works with any client unchanged and survives new methods for free.
The cost is no semantic validation of captured traffic - matching is structural over
parsed JSON, not schema-aware.

### 2026-07-18 - JSON-RPC id is never matched and always re-stamped

**Status:** Accepted
**Context:** Request ids vary per run and per client; matching on them would make
replay brittle.
**Decision:** Match structurally per `MatchConfig`; ignore the `id` entirely and
re-stamp the recorded response with the incoming request's id at replay time.
**Consequences:** Replay is stable across runs. Correlation relies on ordering
discipline (`per_method` default) rather than ids.

### 2026-07-18 - Faults live in a separate overlay; cassettes are immutable under faults

**Status:** Accepted
**Context:** One recording should drive a whole resilience matrix (timeouts, errors,
disconnects) without re-recording.
**Decision:** Keep faults in a `FaultOverlay` (in-memory or `<cassette>.faults.json`
sidecar), applied at replay time. The recorded cassette is never rewritten.
**Consequences:** One cassette powers many failure scenarios; the source of truth
stays pristine. Faults are resolved against the cassette at serve time.

### 2026-07-18 - Redact at capture time on a deep copy

**Status:** Accepted
**Context:** Secrets in traffic must never land in a cassette, but altering bytes in
flight would corrupt the live session.
**Decision:** Apply redaction rules to a deep copy at capture time; bytes forwarded
between agent and server are never modified. Defaults (`*token*`, `*secret*`,
`authorization`, ...) are always on unless disabled.
**Consequences:** Safe-by-default recordings; the live session is untouched.

### 2026-07-18 - Signal-driven shutdown that hard-exits instead of unwinding

**Status:** Accepted
**Context:** The client stdin read runs in an un-cancellable anyio `FileReadStream`
worker thread, so a targeted signal cannot interrupt it and a graceful task-group
unwind would hang waiting on it. asyncio has no `add_signal_handler` on Windows.
**Decision:** On interrupt both platforms converge on `_interrupt_shutdown`:
terminate the child, finalize the cassette, `os._exit(130)`. POSIX uses
`anyio.open_signal_receiver`; Windows uses a `signal.signal` SIGINT/SIGBREAK handler
polled by `_watch_signals_windows`. Off the main thread, shutdown degrades to
EOF-driven.
**Consequences:** No hangs on interrupt; cassette is finalized on the way out. The
hard exit discards subprocess coverage, so interrupt paths are covered in-process
with `os._exit` mocked.

### 2026-07-18 - Cross-process miss signalling via exit code 3 and a report sidecar

**Status:** Accepted
**Context:** Record/replay run in a separate process from the test, so failures must
cross the process boundary.
**Decision:** The replay server exits `3` on any unmatched request; a small JSON
report sidecar (`report.py`) is written by the subprocess and read back by the
fixture, which surfaces misses and empty recordings as test failures.
**Consequences:** Deterministic test failures on drift without shared memory. Adds a
sidecar file to the contract between fixture and subprocess.

### 2026-07-18 - Cassette format versioning is an integer, decoupled from package version

**Status:** Accepted
**Context:** The on-disk schema needs forward-compat gating independent of the
library's release version.
**Decision:** `FORMAT_VERSION` is an `int` embedded in every cassette; loading a
newer cassette raises `UnsupportedFormatVersion`. It advances one step per schema
change, not per release.
**Consequences:** Old readers reject unknown formats cleanly. The schema version does
not track the package version (e.g. package 0.2.0 still writes `format_version` 2).

### 2026-07-19 - v0.2.0: widen to HTTP transport and server-initiated requests

**Status:** Accepted
**Context:** v0.1.0 handled stdio only and refused cassettes containing sampling or
elicitation at load.
**Decision:** Add a Streamable HTTP transport (`transports/http/*`, `mcp-cassette[http]`
extra) with a recording reverse proxy and an offline mock HTTP server; SSE is
passthrough and `Mcp-Session-Id` is captured as evidence while replay issues a fresh
id. Add server-initiated request replay (`replay/server_requests.py`) with anchored
emission on the recorded `msg_id`, accept-anything response handling, and
release-on-response gating. Bump `FORMAT_VERSION` to 2 with optional HTTP metadata.
**Consequences:** Agents over HTTP and sampling/elicitation flows now record and
replay on both transports. `UnsupportedCassetteFeature` was removed from the public
API. HTTP support is an optional extra, keeping the core dependency set unchanged.

### 2026-07-19 - v0.2.0: crash-safety checkpoints during recording

**Status:** Accepted
**Context:** A hard kill mid-recording lost the whole session.
**Decision:** Periodically write the in-progress recording to a `<cassette>.partial`
sidecar (`--checkpoint-interval`, default 5s). Never write to the cassette path
itself, because `once` mode resolves record-vs-replay by that file's existence and a
truncated cassette there would replay as a finished one.
**Consequences:** A hard kill loses only the tail since the last checkpoint. Adds a
`.partial` sidecar during recording.

### 2026-07-19 - v0.2.0: cassette linting for third-party content

**Status:** Accepted
**Context:** Recorded tool descriptions and results are third-party content that
reaches a model and can carry prompt-injection or supply-chain risk.
**Decision:** Add `mcp-cassette lint` (`lint/*`: engine, patterns, rules) with
heuristic rules, `--baseline` drift detection, and `--format json`. Exposed
programmatically as `LintFinding` and `LintReport`.
**Consequences:** CI can gate on recorded-content drift. Rules are heuristic, not a
security guarantee.

### 2026-07-21 - v0.3.0: a third front door, and why stdio still returns a command

**Status:** Accepted
**Context:** Everything behind the fixture was already pytest-free, but a harness that
is not a pytest suite - a notebook, a benchmark runner, another test framework - had
no supported entry point. The open question was whether "library mode" should also
mean replaying stdio in-process, without a subprocess.
**Decision:** Add `use_cassette()`, a context manager over the same `CassetteSession`,
plus `resolve_mode()` which the fixture now delegates its mode validation to so the two
doors cannot drift. For stdio it returns a **command list**, exactly as the fixture
does, because an MCP stdio server *is* a program the client launches and the only seam
is which command it launches. Only Streamable HTTP gets an in-process server, because
an HTTP config carries no command and something must already be listening before the
agent connects.
**Consequences:** Three callers share one code path. In-process stdio replay stays
deferred, not blocked: it would need SDK-shaped types behind an optional
`mcp-cassette[sdk]` extra and would only serve agents wired directly against
`ClientSession`, so anything configured by JSON `command`/`args` could not use it.
Library callers get their session report in a temp directory rather than beside the
cassette, and an exception inside the block skips report checks so the real failure is
never buried under a replay-miss error.

### 2026-07-21 - v0.3.0: replay pacing is a deliberate, default-off exception to the no-clock invariant

**Status:** Accepted, amends "no wall-clock reads in the response path"
**Context:** `t_offset_ms` has been recorded since v0.1.0 and always ignored. Instant
replay hides a class of agent bug - timeout handling, progress-stream UX, retry and
backoff logic - that only appears when the server answers in 800 ms instead of 0.
**Decision:** Add `replay/pacing.py` (`Pacer`) and `PaceConfig`, wired at every
emission point on both transports including SSE inter-event spacing. Off by default;
with pacing off the pacer returns without sleeping and without reading a clock, which
a unit test enforces rather than a comment. Pacing precedes fault injection, so a
`delay` fault is additive and a `timeout` spends no sleep before its silence. Gaps are
replayed, never the absolute recorded timeline, and each gap is capped at 5000 ms by
default so one interactive human pause cannot look like a hung CI job.
**Consequences:** The standing invariant now reads "...unless pacing is explicitly
enabled", recorded here and in CLAUDE.md so a future reader does not mistake it for an
accident. Paced tests trade determinism for latency fidelity by choice. Under
`new_episodes` only replayed hits are paced - fall-through misses are inherently
live-timed.

### 2026-07-21 - v0.3.0: lint extensibility is declarative TOML, never a Python plugin API

**Status:** Accepted
**Context:** The bundled rules catch generic smells but not project-specific ones - a
vendor name, an internal hostname, domain-specific exfiltration phrasing. The obvious
design is a `Rule` protocol with `register_rule()` and entry-point discovery.
**Decision:** Rejected the Python tier. Add `lint/packs.py`: TOML pattern packs
(`PatternRule`, `PatternSet`) with their own ids and severities, plus
`[tool.mcp_cassette.lint]` discovery from the nearest `pyproject.toml`
(`ProjectLintConfig`). Pack regexes are compiled, never evaluated, and no code is
imported from a pack. Packs extend the bundled set and cannot replace it - there is no
`--no-bundled` flag, because "disable all built-in security rules" is an attractive
nuisance on this surface.
**Consequences:** A public rule contract that would need semver stability forever is
avoided, and `lint` never executes third-party code on a supply-chain-security surface.
The cost is that a project needing non-regex logic has no escape hatch. Bundled
R001-R004 findings stay byte-identical when no pack is configured, pinned by a
regression test, so extensibility did not move the existing rules.

### 2026-07-21 - v0.3.0: diff is descriptive, R002 is the gate

**Status:** Accepted
**Context:** Re-recording after a server upgrade produces a cassette whose interesting
content is the delta, but `git diff` on the raw JSON drowns it in re-stamped ids and
shifted offsets. Lint's R002 already compares tool surfaces against a baseline, so the
two overlap.
**Decision:** Add `diffing.py` and a `diff OLD NEW` subcommand that compares metadata,
per-method counts, tool surfaces, and the exchange sequence - ignoring exactly what
replay ignores (ids, `t_offset_ms`, `seq`). Keep the overlap and make the split
explicit: **R002 is a gate** (error severity, tool surfaces only, exit 4) while
**`diff` is descriptive** (everything that changed, no severity, exit 5 for a human to
read). Both draw tool surfaces from one shared extractor.
**Consequences:** A new exit code 5 joins the contract (0 clean, 2 usage/load, 3 replay
miss, 4 lint findings). CI can gate on either or both, and a failure names which fired.
Neither command replaces the other, which the guide states in one line so the overlap
does not read as duplication.
