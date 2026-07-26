# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Documentation, examples, and CI. No code, flag, or behavior changes.

### Added

- `examples/cassettes/tools-v2.mcp.json`: the example echo server one version
  later, carrying an injected tool description *and* a changed `inputSchema`.
  The existing `injected.mcp.json` stays the R001 rule fixture; this one is the
  drift artifact, and its schema change is the case `lint` cannot see —
  demonstrating why the gate needs both steps.
- `docs/guide/how-to/HT-09-gate-a-drifting-server.md`: the two-step lint + diff
  gate walked end to end against the committed example cassettes, with real
  output and exit codes, plus what each step catches and misses. Closes the gap
  where the gate existed only as prose with placeholder paths in `OP-03`.
- An `examples` CI job that replays the example cassettes offline and asserts
  the gate's exit codes (lint clean `0`, lint drifted `4`, `diff --tools-only`
  `5`). Nothing collected `examples/` before, so its cassettes could rot
  unnoticed, and the numbers quoted in the docs had nothing enforcing them.
- A runnable lint + diff snippet in the README's gating section, pointing at the
  committed example cassettes and at `HT-09`. The section previously offered only
  pattern-pack configuration, which is what led issue #9 to ask for a demo that
  largely already existed.
- README `§2.2` and an `examples/README.md` section naming
  `examples/cassettes/echo_and_add.mcp.json` as the canonical golden cassette and
  stating the contract it stands for — record once locally, commit the cassette,
  CI only replays and lints — with the replay-only proof command for both a single
  file and the whole directory.
- Document that `mcp_cassette_dir` is fixture-only and that `pytest -o
  mcp_cassette_dir=...` overrides it per invocation (`OP-02.2`, README `§2.1`).
  The option read as a gap in the CLI and library doors; it is not one. The
  fixture is the only door that derives a cassette path — from the test node name
  — so a base directory has nothing to join onto elsewhere, and a
  `MCP_CASSETTE_DIR` env var would reach one door of three. `MCP_CASSETTE_MODE` is
  cross-door precisely because all three delegate to `resolve_mode`.
- A CI assertion that `examples/lint-pack.toml` actually fires (`P001` present in
  the output, not merely exit `4`, which the bundled rules produce on their own).
  A starter pack nobody can see match is a poor advertisement for the feature.
- `HT-08.2`, what a pattern pack can and cannot reach, plus shorter statements of
  the same in `OP-04.6` and README `§8.1`. The docs named the `surfaces` field but
  never said what lint reads in total, so two things were invisible: a cassette
  with no `tools/list` and no `tools/call` lints clean because there is nothing to
  scan, not because it is safe; and `name`/`inputSchema` are extracted but never
  pattern-matched, so "flag any tool whose schema grew a parameter" is `R002`'s or
  `diff`'s job and no pack can express it. States the split — `R001`/`R004` are
  pattern rules a pack extends, `R002`/`R003` are structural and it cannot.
  **Subsequent `HT-08` section codes shift by one** (old `HT-08.2` "Make it the
  project default" is now `HT-08.3`, and so on).
- A troubleshooting row for a replay that hangs on `tools/call`: the cassette
  holds a server-initiated request and replay waits for the agent's answer by
  design. Sampling appeared nowhere in the guide, so the deliberate hang had no
  symptom entry to find it by.

### Changed

- Renumber the guide from one global 1–15 sequence to per-section codes — `GS`
  getting started, `HT` how-to, `TS` troubleshooting, `OP` operations — each
  numbered within its own series (`operations/OP-03-ci.md`, section `§OP-03.3.1`).
  Adding a chapter previously renamed every later file and rewrote every inbound
  link; chapters now append to their series and renumber nothing, so a cited code
  keeps pointing at the same chapter. Filenames, headings, section numbers, link
  text, relative paths, GitHub URLs, and heading anchors move together.
  **Links to the old guide filenames break.** Entries below this one keep the
  old paths deliberately: they record what past releases shipped.
- Retitle README section 8 from "Linting your cassettes" to "Gating your
  cassettes" and split it into `8.1` lint and `8.2` diff. The drift gate was a
  paragraph below the lint disclaimer, so it read as an appendix and was invisible
  to anyone scanning headings — `diff` is a peer of `lint`, not a footnote to it.
- Point `tools-v2.mcp.json`'s injected description at a `.env` file rather than an
  SSH key, so it matches the starter pack's `P001` as well as the bundled `R001`
  patterns. The pack's example command previously ran against the *clean* cassette
  and printed `clean: no findings`, demonstrating nothing.
- Record in `CLAUDE.md` why replay's release-on-response gate exists, not just
  what it does: it keeps a cassette from being easier to satisfy than the real
  server, so an agent that ignores a sampling request hangs on replay instead of
  collecting the recorded result and passing green.

## [0.3.3] - 2026-07-25

First PyPI release, gated on a full pre-release audit. Packaging and
discoverability, plus the behavior fixes the audit turned up.

### Added

- Ship the PEP 561 `py.typed` marker in the wheel so consumers' type checkers
  see the library's type hints.
- `.github/workflows/publish.yml`: build once, publish to TestPyPI via manual
  `workflow_dispatch` and to PyPI on GitHub release, both through Trusted
  Publishing (OIDC) — no long-lived tokens.
- `PatternSet` is exported from the top-level `mcp_cassette` namespace. It was
  public in `mcp_cassette.lint` and documented, but absent from the package's
  `__all__` — fixed before a release freezes the surface.

### Fixed

- The `mcp_cassette` fixture crashed with `AttributeError: 'Function' object has
  no attribute 'fspath'` under `-p no:legacypath`, and would have broken outright
  when pytest drops the deprecated `legacypath` plugin. The default cassette path
  now derives the module name from `node.path`.
- A relative `mcp_cassette_dir` ini value resolved against the current working
  directory, so running pytest from a subdirectory looked for cassettes in a
  different place than the documented `<rootpath>/...` default. It now resolves
  against pytest's `rootpath`; an absolute value still wins as written.
- A recording that captured zero messages still wrote an empty cassette file
  (both transports). The runbook already said no cassette is written, and the
  empty file was actively harmful: in `once` mode its existence sent every later
  run down the replay branch, so a mis-wired first run could never re-record
  itself. Nothing is written now; the session report — and therefore the
  fixture's `recording captured zero messages` failure — is unchanged.
- Replay answered a request that matched a recorded one with *no recorded
  response* (a recording cut short mid-call, or a hand-promoted `.partial`) with
  the same JSON-RPC miss error it sends for an outright miss, but recorded no
  miss: the process exited `0` and the fixture passed the test. Both transports
  now count it as a miss, so it exits `3` and fails the test like any other. The
  same hole in the `initialize` handshake, which bypasses the matcher entirely,
  is closed too.
- `record` (and `serve --new-episodes`) hung forever when the wrapped server exited
  while the agent still held the proxy's stdin open — a server crash mid-session
  turned into a hung test rather than a failed one. The task-group cancel that was
  supposed to end the session could not: the client stdin read is parked in an
  anyio worker thread that is both un-cancellable and non-daemon, so the
  interpreter would join it at exit. Server death now converges on the same hard
  exit the interrupt path already used — cassette finalized first, and the process
  leaves with the wrapped server's own exit code.
- `with_faults()` on a session built without an explicit `report_path` wrote its
  generated overlay to `<cassette>.faults.json` — the exact filename the docs tell
  you to hand-write for `--faults` — overwriting a committed overlay and then
  leaving the generated file behind next to the cassette. The overlay now goes to a
  private temporary directory removed by `close()`. The pytest fixture and
  `use_cassette` were unaffected (both already pass a temp `report_path`).
- The replay-pacing integration tests asserted wall-clock floors within ~300 ms of
  the expected signal, while each measurement spans two subprocess startups. Two
  different tests flaked on consecutive local runs; the floors now sit at roughly
  half the signal, which is still unambiguous (instant replay contributes ~0).
- Raise the `anyio` floor to `>=4.2`. The replay and HTTP servers construct
  `anyio.Lock()` / `anyio.Event()` outside a running event loop, and the
  adapters that make that legal only exist from 4.2 — on 4.0/4.1 a consumer got
  `AsyncLibraryNotFoundError` the moment a replay server was built.
- `Cassette.load` raised `AttributeError` (a traceback, exit `1`) on a cassette
  whose JSON top level is not an object, and `TypeError` on a non-integer
  `format_version`. Both are read before validation, so pydantic never saw
  them; both are now `ValueError` and exit `2` like every other malformed
  cassette.
- `record --port` / `--max-idle` with a stdio `-- CMD` silently ignored both
  flags. They belong to `--url` recording and are now a usage error (exit `2`),
  matching how `--pace-scale` without `--pace` is handled.
- The `mcp_cassette` fixture finalized unconditionally, so a test that failed
  *and* hit a replay miss reported both a FAILED and a teardown ERROR, burying
  the real failure. Teardown now only closes the session when the test body
  already failed — the behavior `use_cassette` always had.
- `CassetteSession._peek_transport` caught only `(FileNotFoundError,
  ValueError)`; a directory or permission `OSError`, or an unsupported
  `format_version`, escaped raw out of `server_command()`.
- `with_faults()` sessions are now finalized. The pytest fixture finalizes the
  session it hands the test, but a fault test runs the *derivative* returned by
  `with_faults()`; that derivative was never checked, so replay misses in fault
  tests were silently unreported and an HTTP server started on it outlived the
  test. `with_faults()` now registers the copy on its parent, and the parent's
  `close()`/`finalize()` cover it.
- `serve`, `inspect`, and `diff` printed a raw traceback and exited `1` on a
  malformed or unreadable cassette; `serve --faults` and `inspect --faults` did
  the same on a missing or malformed overlay. All load sites now share one error
  set and report the documented usage error (exit `2`). `inspect` also loads the
  overlay before printing, so a bad overlay no longer fails halfway through a
  report.
- Fill in the Apache-2.0 copyright holder, which was still the license
  template's `[yyyy] [name of copyright owner]` placeholder.
- Restore the CHANGELOG compare links dropped for 0.3.3: `[Unreleased]` pointed
  at `v0.3.2` and the `[0.3.3]` link was missing.
- Gitignore the cassette `examples/library_mode.py` records on its first run, so
  following the examples README no longer dirties the working tree.

### Changed

- `PatternSet.for_surface` is gone, inlined into its one caller
  (`PatternSet.match`). It handed out an internal dataclass, which keeping it
  public would have frozen into the API `PatternSet` now exports.
- README links are absolute GitHub URLs. The README is the PyPI long
  description, where relative links resolve against `pypi.org` and 404.
- `[project.urls]` adds Documentation, Changelog, and Issues for the PyPI
  sidebar.
- `publish.yml` fails the build when a release tag disagrees with the packaged
  version, rather than publishing a filename PyPI will never let us reuse.
- License metadata now uses the PEP 639 SPDX form (`license = "Apache-2.0"`,
  `license-files = ["LICENSE"]`) instead of the deprecated table form;
  hatchling pinned `>=1.27` accordingly.
- The sdist no longer includes internal agent-workspace docs and repo tooling
  (`.agents_workspace/`, `.claude/`, `CLAUDE.md`, `.pre-commit-config.yaml`).
- Unify the public one-liner (PyPI description, README opener) into a single
  canonical description: "Record/replay testing for MCP (Model Context
  Protocol) agents: capture real sessions as cassettes, replay them as
  deterministic mock servers — vcrpy for MCP." — replacing three drifted
  variants.
- Expand `pyproject.toml` keywords (`model-context-protocol`, `agent`) and
  classifiers (`Operating System :: OS Independent`,
  `Topic :: Software Development :: Testing :: Mocking`, `Typing :: Typed`)
  for PyPI search and filtering.

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

[Unreleased]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.3...HEAD
[0.3.3]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/cheneeheng/mcp-cassette/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/cheneeheng/mcp-cassette/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/cheneeheng/mcp-cassette/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cheneeheng/mcp-cassette/releases/tag/v0.1.0
