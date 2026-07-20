# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] - 2026-07-20

Documentation only; no code changes.

### Added

- `docs/guide/` — a task-oriented user and operator guide, split by audience.
  For test authors: getting started, and how-to pages for stdio record/replay,
  remote Streamable HTTP, fault injection, and redaction. For operators:
  install, configuration (every mode, ini option, marker, and matching
  setting), CI pipeline, CLI reference with exit codes, and an incident
  runbook for replay misses and failed recordings. Plus a symptom-to-fix
  troubleshooting table.
- README now links to the guide.

## [0.2.1] - 2026-07-19

Documentation only; no code changes.

### Added

- `.agents_workspace/ARCHITECTURE.md`: living architecture doc — the standard
  Mermaid diagram set (system context, components, record/replay sequences, data
  model) plus a Key Decisions log.

### Fixed

- Two CHANGELOG references left stale by the v0.x version relabel: the 0.1.0
  release note (Beta, not "stable") and the `[Unreleased]` compare link.

## [0.2.0] - 2026-07-19

Remote servers, server-initiated requests, and supply-chain linting. Cassettes now
record and replay Streamable HTTP sessions as well as stdio, sampling and elicitation
round-trip on both transports, and recorded third-party text can be linted in CI.

### Added

- Streamable HTTP transport (`mcp-cassette[http]` extra): `mcp-cassette record --url`
  stands up a local recording reverse proxy in front of a remote MCP endpoint, and
  `mcp-cassette serve` infers the transport from the cassette and replays it as a local
  mock HTTP server — offline, with no contact with the real server. SSE is passthrough
  (never buffered), and `Mcp-Session-Id` is captured as evidence while replay issues its
  own fresh id.
- `mcp_cassette.server_url(real_url)` — the HTTP twin of `server_command`, returning a
  local URL to plug into the agent's MCP config. The fixture still never monkeypatches
  the agent.
- Server-initiated request replay (sampling, elicitation) on both transports: anchored
  emission with the recorded `msg_id`, accept-anything response handling (the agent's
  answer is never matched against the recording), and release-on-response gating. v1
  refused such cassettes at load; they now replay.
- `mcp-cassette lint` — heuristic rules over recorded tool descriptions and results
  (third-party content that reaches a model), with `--baseline` drift detection and
  `--format json`. Exposed programmatically as `LintFinding` and `LintReport`.
- Periodic crash-safety checkpoints during recording (`--checkpoint-interval SECONDS`,
  default 5, `0` disables). A recording is written to a `<cassette>.partial` sidecar as
  it runs, so a hard kill loses only what arrived since the last checkpoint instead of
  the whole session. The sidecar is never written to the cassette path itself, because
  `once` mode resolves record-vs-replay by that file's existence.
- Cassette format version 2, widening version 1 with optional HTTP metadata
  (`transport`, `server_url`, `session_id`, per-message `exchange` and `channel`).

### Changed

- Recording is no longer purely in-memory-until-shutdown; see checkpoints above.
- `Authorization` and every other HTTP header is forwarded upstream untouched but never
  written to a cassette — stronger than redaction, since no field could hold it.

### Removed

- **Breaking:** `UnsupportedCassetteFeature` is gone from the public API. It existed
  only to refuse cassettes containing server-initiated requests at load; those cassettes
  now replay, so nothing raises it. Remove any `except UnsupportedCassetteFeature`
  handler — v1 cassettes themselves load unchanged.

### Fixed

- HTTP proxy: cancel the run scope on a fatal first-contact error or a `disconnect`
  fault, instead of hanging until the client gave up.
- Lint: use an ASCII minus in the R002 finding message, which crashed
  `lint --baseline` on cp1252 Windows consoles.

## [0.1.0] - 2026-07-18

First public release. `mcp-cassette` is "vcrpy for MCP": record real MCP stdio
sessions between an agent and an MCP server into cassettes, then replay them as
deterministic mock servers so agent test suites stop hitting live servers.

### Added

- Recording proxy (`mcp-cassette record`) that wraps a real MCP server over
  newline-delimited JSON-RPC stdio, taps both directions plus stderr, timestamps
  against a monotonic clock, and saves an atomic cassette on any shutdown path.
- Replay server (`mcp-cassette serve`) that answers client requests from a
  recorded cassette with no network, subprocess, or wall-clock reads, re-stamping
  the JSON-RPC `id` onto each recorded response.
- Structural request matching with three ordering disciplines (`per_method`
  default, `strict`, `none`) via `MatchConfig`; the JSON-RPC `id` is never matched.
- pytest fixture `mcp_cassette` and `@pytest.mark.mcp_cassette` marker with record
  modes `once` (default), `none`, `all`, and `new_episodes`; mode precedence
  `MCP_CASSETTE_MODE` env > marker > `mcp_cassette_mode` ini > `once`. The fixture
  hands back a server command list rather than monkeypatching the agent.
- Fault injection (`Fault`, `FaultOverlay`, `FaultTarget`) with `delay`, `timeout`,
  `error`, `malformed`, and `disconnect` faults; faults live in a separate overlay
  (in-memory or `<cassette>.faults.json`) and never mutate the recorded cassette.
- Redaction at capture time on a deep copy, with default rules (`*token*`,
  `*secret*`, `authorization`, …) always on unless disabled; bytes in flight are
  never altered.
- `mcp-cassette inspect` for per-method counts, timing, and fault dry-runs.
- Cross-process miss signalling: the replay server exits `3` on any unmatched
  request and the fixture surfaces misses (and empty recordings) as test failures.
- Graceful, cassette-finalizing shutdown on Linux, macOS, and Windows.
- Pydantic v2 cassette schema with `FORMAT_VERSION` forward-compat gating.

### Notes

- Runtime dependencies are only `anyio` and `pydantic`; the `mcp` SDK is never a
  runtime dependency.
- Server-initiated requests (sampling/elicitation) are recorded generically but
  not replayable in this release; such cassettes are refused at load.

[Unreleased]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/cheneeheng/mcp-cassette/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cheneeheng/mcp-cassette/releases/tag/v0.1.0
