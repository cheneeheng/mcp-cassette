# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-18

First stable release. `mcp-cassette` is "vcrpy for MCP": record real MCP stdio
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

[Unreleased]: https://github.com/cheneeheng/mcp-cassette/compare/v1.0.0...HEAD
[0.1.0]: https://github.com/cheneeheng/mcp-cassette/releases/tag/v0.1.0
