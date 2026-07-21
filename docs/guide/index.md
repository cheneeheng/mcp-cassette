# mcp-cassette Guide

mcp-cassette records real MCP sessions between an agent and an MCP server — local stdio
or remote Streamable HTTP — into **cassettes** (structured JSON files you commit), then
replays those cassettes as deterministic mock MCP servers. Your agent test suite stops
hitting live servers.

This guide has two audiences. Do not mix them up:

- **Test authors** (you write tests that exercise an agent): start at
  [Getting started](getting-started.md), then the [how-to](#how-to) pages.
- **Operators** (you own the CI pipeline, the recording runs, and the cassette files):
  start at [Install](operations/install.md), then [CI pipeline](operations/ci.md).

## Contents

### Test authors

- [Getting started](getting-started.md) — install, write one test, record it, replay it.
- [Record and replay a stdio server](how-to/record-and-replay.md) — the core loop,
  record modes, re-recording.
- [Record and replay a remote HTTP server](how-to/remote-http.md) — `server_url`, the
  `[http]` extra.
- [Use it as a library](how-to/use-as-a-library.md) — `use_cassette` for harnesses that
  are not pytest suites.
- [Inject faults](how-to/inject-faults.md) — drive a resilience matrix off one
  recording.
- [Replay timing](how-to/replay-timing.md) — replay recorded latency when your agent's
  timeout or retry logic depends on it.
- [Inspect and diff cassettes](how-to/inspect-and-diff.md) — read the timeline, grep
  payloads, compare two recordings.
- [Redact secrets](how-to/redact-secrets.md) — what is scrubbed by default and how to
  add rules.
- [Lint with your own pattern packs](how-to/lint-pattern-packs.md) — extend the bundled
  rules with project-specific regexes.
- [Troubleshooting](troubleshooting.md) — symptom to fix.

### Operators

- [Install](operations/install.md) — requirements, extras, health check.
- [Configuration](operations/configure.md) — every mode, ini option, env var, and
  matching setting.
- [CI pipeline](operations/ci.md) — how to wire cassettes into CI so nothing hits a live
  server.
- [CLI reference](operations/cli-reference.md) — commands, flags, exit codes.
- [Runbook: replay misses and failed recordings](operations/runbook-replay-misses.md).

## How it works, in one paragraph

mcp-cassette works at the transport level (newline-delimited JSON-RPC over stdio, or
Streamable HTTP) and treats messages semi-opaquely, so it works with any MCP client
unmodified and never imports the `mcp` SDK at runtime. There are three front doors — the
pytest fixture, the CLI, and `use_cassette` for plain Python — and none of them
monkeypatches your agent: each hands you a **command list** (stdio) or a **URL** (HTTP) to
plug into the agent's MCP server configuration. On the first run that command is a
recording proxy wrapping the real server; on every run after it is a replay server
reading from the cassette.
