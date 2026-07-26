# mcp-cassette Guide

mcp-cassette records real MCP sessions between an agent and an MCP server — local stdio
or remote Streamable HTTP — into **cassettes** (structured JSON files you commit), then
replays those cassettes as deterministic mock MCP servers. Your agent test suite stops
hitting live servers.

Every chapter carries a stable code: `GS` getting started, `HT` how-to, `TS`
troubleshooting, `OP` operations. The code is the filename and the heading prefix, so
`OP-02` is `operations/OP-02-configure.md` and "see §OP-02.4" means section 4 of that
chapter. Numbering runs per section, so a new chapter appends to its own series and
never renumbers the others — a code, once cited, keeps pointing at the same chapter.

The two audiences do not mix:

- **Test authors** (you write tests that exercise an agent): start at
  [GS-01. Getting started](GS-01-getting-started.md), then the `HT` how-to chapters.
- **Operators** (you own the CI pipeline, the recording runs, and the cassette files):
  start at [OP-01. Installation](operations/OP-01-install.md), then
  [OP-03. CI pipeline](operations/OP-03-ci.md).

## Part I — Test authors

- **GS-01** [Getting started](GS-01-getting-started.md) — install, write one test, record
  it, replay it.
- **HT-01** [Record and replay a stdio server](how-to/HT-01-record-and-replay.md) — the
  core loop, record modes, re-recording.
- **HT-02** [Record and replay a remote HTTP server](how-to/HT-02-remote-http.md) —
  `server_url`, the `[http]` extra.
- **HT-03** [Use it as a library](how-to/HT-03-use-as-a-library.md) — `use_cassette` for
  harnesses that are not pytest suites.
- **HT-04** [Inject faults](how-to/HT-04-inject-faults.md) — drive a resilience matrix off
  one recording.
- **HT-05** [Replay timing](how-to/HT-05-replay-timing.md) — replay recorded latency when
  your agent's timeout or retry logic depends on it.
- **HT-06** [Inspect and diff cassettes](how-to/HT-06-inspect-and-diff.md) — read the
  timeline, grep payloads, compare two recordings.
- **HT-07** [Redact secrets](how-to/HT-07-redact-secrets.md) — what is scrubbed by default
  and how to add rules.
- **HT-08** [Lint with your own pattern packs](how-to/HT-08-lint-pattern-packs.md) — extend
  the bundled rules with project-specific regexes.
- **HT-09** [Gate a drifting server surface](how-to/HT-09-gate-a-drifting-server.md) — fail
  the build when a third-party tool description or schema moves under you.
- **TS-01** [Troubleshooting](TS-01-troubleshooting.md) — symptom to fix.

## Part II — Operators

- **OP-01** [Installation](operations/OP-01-install.md) — requirements, extras, health
  check.
- **OP-02** [Configuration](operations/OP-02-configure.md) — every mode, ini option, env
  var, and matching setting.
- **OP-03** [CI pipeline](operations/OP-03-ci.md) — how to wire cassettes into CI so
  nothing hits a live server.
- **OP-04** [CLI reference](operations/OP-04-cli-reference.md) — commands, flags, exit
  codes.
- **OP-05** [Runbook: replay misses and failed recordings](operations/OP-05-runbook-replay-misses.md)
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
