# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.3] - 2026-07-22

Packaging-only release preparing the first PyPI publish. No code, flag, or
behavior changes.

### Added

- Ship the PEP 561 `py.typed` marker in the wheel so consumers' type checkers
  see the library's type hints.
- `.github/workflows/publish.yml`: build once, publish to TestPyPI via manual
  `workflow_dispatch` and to PyPI on GitHub release, both through Trusted
  Publishing (OIDC) — no long-lived tokens.

### Changed

- License metadata now uses the PEP 639 SPDX form (`license = "Apache-2.0"`,
  `license-files = ["LICENSE"]`) instead of the deprecated table form;
  hatchling pinned `>=1.27` accordingly.
- The sdist no longer includes internal agent-workspace docs and repo tooling
  (`.agents_workspace/`, `.claude/`, `CLAUDE.md`, `.pre-commit-config.yaml`).

## [0.3.2] - 2026-07-22

Documentation-only release. No code, flag, or behavior changes.

### Changed

- State explicitly that the unit of recording is the entire session — every
  message from server launch to session end — never an individual tool call,
  and that the record modes decide record-vs-replay once per test run (`all`
  overwrites the whole cassette file, not single entries). Added to the guide's
  record-mode chapter (§2.3), the operator configuration reference (§12.1), the
  getting-started first-run walkthrough (§1.4), and the README's canonical
  record-mode table (§2.1).

## [0.3.1] - 2026-07-22

Documentation-only release: the guide and README are restructured and numbered.
No code, flag, or behavior changes.

### Changed

- Number the guide as 15 chapters in reading order — test authors (1–10), then
  operators (11–15) — with the chapter number in each filename
  (`01-getting-started.md` … `operations/15-runbook-replay-misses.md`) and
  numbered `X.Y`/`X.Y.Z` section headings throughout, so sections are citable
  as e.g. §12.6.
- Rewrite `docs/guide/index.md` as a two-part numbered table of contents that
  states the numbering convention.
- Number the README sections 1–9 and end each with a uniform "Full chapter:"
  pointer into the guide; add a Redaction section so the capture-time
  scrubbing defaults are visible from the front page.
- Present repeated content uniformly across README and guide: one canonical
  record-mode table and precedence phrasing, word-identical ordering-discipline
  tables, and one lint-disclaimer wording.

## [0.3.0] - 2026-07-21

Four additions, all opt-in: a library front door, replay pacing, richer
inspect/diff, and per-project lint packs. Every existing command, flag, and
export behaves exactly as in 0.2.x when the new flags are absent, and the
cassette `format_version` stays 2.

### Added

- **Embedded library mode.** `use_cassette(...)` is a context manager giving
  plain Python code — an agent harness, a notebook, a benchmark runner, a
  non-pytest test framework — the same session the pytest fixture gets: same
  modes, same fault matrix, same failure semantics. New exports:
  `use_cassette`, `resolve_mode`, `CassetteSession.close()`, `CassetteError`,
  `Mode`, and `lint_cassette`. The session report goes to a temporary directory
  removed on exit, so no untracked JSON lands next to committed cassettes; a
  raising `with` body propagates untouched rather than being buried under a
  replay-miss error. `examples/library_mode.py` is runnable.
- **Replay pacing.** `--pace recorded` replays the recorded `t_offset_ms` gaps
  on both transports, including SSE inter-event spacing; `--pace-scale` and
  `--pace-cap-ms` (default 5000, `0` uncapped) bound it. Also available as
  `PaceConfig`, the `pace=`/`pace_scale=`/`pace_cap_ms=` marker arguments, and
  `use_cassette(pace=...)`. Off by default — with pacing off the response path
  still performs no sleep and reads no clock. Pacing precedes faults, so a
  `delay` fault is additive and a `timeout` spends no sleep.
- **`inspect` views.** `--timeline` (one line per message with direction, kind,
  method, id, and payload bytes; `exch`/`chan` for http), `--tools`,
  `--grep PATTERN`, and `--format json` with byte-stable output.
- **`diff OLD NEW`.** Structural comparison of two cassettes — metadata, method
  counts, tool surfaces, exchange sequence — with `--tools-only` and
  `--format json`. Exit `0` identical, `5` differ, `2` load error. Ids,
  `t_offset_ms`, and `seq` are never compared. Also `diff_cassettes()` and
  `CassetteDiff` as library exports.
- **Lint pattern packs.** `--pattern-pack PATH` loads declarative TOML rules
  with their own ids and severities; `[tool.mcp_cassette.lint]` in
  `pyproject.toml` makes a project's packs, selection, and `fail_on` threshold
  the default for every invocation; `--fail-on warning` and `--no-config`.
  Packs extend the bundled rules and never replace them, and bundled findings
  stay byte-identical. New exports: `PatternRule`, `ProjectLintConfig`.
  `examples/lint-pack.toml` is a starter pack. There is deliberately no Python
  rule-plugin API — `lint` should not execute third-party code on a
  supply-chain-security surface.
- Guide pages: use as a library, replay timing, inspect and diff, lint pattern
  packs. CLI reference, CI recipe, troubleshooting, and redaction pages updated.

### Changed

- `--select` now wins over `--ignore` when a rule id appears in both, and the
  run prints a note naming the id (previously the id was silently dropped).
- Mode validation is shared: the pytest fixture delegates to
  `session.resolve_mode`, so the error message now names its source
  (`env MCP_CASSETTE_MODE`, `marker mode=`, `ini mcp_cassette_mode`, or
  `mode= argument`).

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

[Unreleased]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/cheneeheng/mcp-cassette/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cheneeheng/mcp-cassette/releases/tag/v0.1.0
