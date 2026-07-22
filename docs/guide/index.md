# mcp-cassette Guide

mcp-cassette records real MCP sessions between an agent and an MCP server — local stdio
or remote Streamable HTTP — into **cassettes** (structured JSON files you commit), then
replays those cassettes as deterministic mock MCP servers. Your agent test suite stops
hitting live servers.

The guide is numbered in reading order: chapters 1–10 for test authors, chapters 11–15
for operators. File names carry the chapter number, so chapter 12 is
`operations/12-configure.md` and "see §12.4" means section 4 of that chapter.

The two audiences do not mix:

- **Test authors** (you write tests that exercise an agent): start at
  [1. Getting started](01-getting-started.md), then the how-to chapters 2–9.
- **Operators** (you own the CI pipeline, the recording runs, and the cassette files):
  start at [11. Installation](operations/11-install.md), then
  [13. CI pipeline](operations/13-ci.md).

## Part I — Test authors

1. [Getting started](01-getting-started.md) — install, write one test, record it,
   replay it.
2. [Record and replay a stdio server](how-to/02-record-and-replay.md) — the core loop,
   record modes, re-recording.
3. [Record and replay a remote HTTP server](how-to/03-remote-http.md) — `server_url`,
   the `[http]` extra.
4. [Use it as a library](how-to/04-use-as-a-library.md) — `use_cassette` for harnesses
   that are not pytest suites.
5. [Inject faults](how-to/05-inject-faults.md) — drive a resilience matrix off one
   recording.
6. [Replay timing](how-to/06-replay-timing.md) — replay recorded latency when your
   agent's timeout or retry logic depends on it.
7. [Inspect and diff cassettes](how-to/07-inspect-and-diff.md) — read the timeline,
   grep payloads, compare two recordings.
8. [Redact secrets](how-to/08-redact-secrets.md) — what is scrubbed by default and how
   to add rules.
9. [Lint with your own pattern packs](how-to/09-lint-pattern-packs.md) — extend the
   bundled rules with project-specific regexes.
10. [Troubleshooting](10-troubleshooting.md) — symptom to fix.

## Part II — Operators

11. [Installation](operations/11-install.md) — requirements, extras, health check.
12. [Configuration](operations/12-configure.md) — every mode, ini option, env var, and
    matching setting.
13. [CI pipeline](operations/13-ci.md) — how to wire cassettes into CI so nothing hits
    a live server.
14. [CLI reference](operations/14-cli-reference.md) — commands, flags, exit codes.
15. [Runbook: replay misses and failed recordings](operations/15-runbook-replay-misses.md)
    — the two incidents that actually happen.

## How it works, in one paragraph

mcp-cassette works at the transport level (newline-delimited JSON-RPC over stdio, or
Streamable HTTP) and treats messages semi-opaquely, so it works with any MCP client
unmodified and never imports the `mcp` SDK at runtime. There are three front doors — the
pytest fixture, the CLI, and `use_cassette` for plain Python — and none of them
monkeypatches your agent: each hands you a **command list** (stdio) or a **URL** (HTTP) to
plug into the agent's MCP server configuration. On the first run that command is a
recording proxy wrapping the real server; on every run after it is a replay server
reading from the cassette.
