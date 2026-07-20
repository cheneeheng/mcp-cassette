# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mcp-cassette` is "vcrpy for MCP": record real MCP stdio sessions between an agent and an MCP server into cassettes (structured JSON), then replay them as deterministic mock servers so agent test suites stop hitting live servers. See `README.md` for the user-facing surface, and `docs/guide/` for the task-oriented user
and operator guide (keep it in sync when flags, modes, error strings, or exit codes change).

## Commands

Tooling is `uv`. The `mcp` SDK is a dev-only dependency (used by the reference server in tests); it is NOT a runtime dependency and must never become one.

```
uv sync                                  # install deps + dev group
uv run pytest                            # run the whole suite
uv run pytest tests/unit                 # one layer (also: tests/integration, tests/system)
uv run pytest tests/integration/test_replay.py       # one file
uv run pytest tests/integration/test_replay.py::test_name -x   # one test, stop on first failure
uv run ruff check .                      # lint (also: ruff format .)
uv run mypy src                          # type-check (strict mode is on)
uv build                                 # build wheel + sdist
```

Tests are layered: `tests/unit` (in-process, no subprocesses), `tests/integration` (subprocess round-trips against the reference server / proxy / replay server), `tests/system` (pytest-plugin fixture flows, partly via pytester). Shared helpers (`scripted_client.py`, `reference_server/`) live at the `tests/` root, importable from any layer via the `pythonpath` pytest ini setting; test basenames must stay unique across layers (no `__init__.py` files). Integration tests shell out to `sys.executable`, so run them through `uv run` so the subprocess inherits the venv.

Platform note: Linux, macOS, and Windows are supported (CI runs the suite on all three; see ITER_05). Proxy shutdown is signal-driven: on POSIX via `anyio.open_signal_receiver` (SIGINT/SIGTERM); on Windows via a `signal.signal` SIGINT/SIGBREAK handler that `_watch_signals_windows` polls (asyncio has no `add_signal_handler` there). On interrupt, **both** platforms converge on `_interrupt_shutdown`: terminate the child, finalize the cassette, and `os._exit(130)` — they do *not* unwind the task group. The reason is shared: the client stdin read runs in an un-cancellable anyio `FileReadStream` worker thread, so a targeted signal cannot interrupt it (no EINTR on the worker) and a graceful unwind would hang waiting on it. Off the main thread, where no handler can be installed, shutdown degrades to EOF-driven. SIGTERM has no graceful-finalize semantics on Windows — `test_ctrl_break_finalizes_cassette` (CTRL_BREAK_EVENT) is the win32 counterpart to the POSIX-only `test_sigterm_finalizes_cassette`; it needs a real console to deliver the event, so it skips (never hangs) under launchers like `uv run` that run without one. The real interrupt paths `os._exit` (which discards subprocess coverage), so they are covered in-process by `tests/unit/test_proxy_shutdown.py` with `os._exit` mocked.

## Architecture

Everything operates at the transport level: newline-delimited JSON-RPC over stdio. Messages are treated semi-opaquely — captured verbatim whatever the method — so the library works with any MCP client unmodified and never imports the `mcp` SDK at runtime.

Data flow, both directions:

- Record: `cli.py record` -> `record/proxy.py` (`StdioRecordingProxy`) spawns the real server and runs three line pumps (`record/pump.py`) in one anyio task group: client->server, server->client, and server-stderr->our-stderr (stderr is forwarded, never swallowed, to avoid hiding logs and deadlocking on a full pipe). A `SessionRecorder` (`record/recorder.py`) taps each line, classifies it by JSON-RPC shape, timestamps against a monotonic clock, and applies redaction to a deep copy at capture time. On any shutdown path the session is finalized into a `Cassette` and saved atomically. While the session runs, `record/checkpoint.py` writes it periodically to a `<cassette>.partial` sidecar (both transports; `--checkpoint-interval`, default 5s) so a hard kill loses only the tail — never to the cassette path itself, because `session.py` resolves `once` mode by that file's existence and a truncated cassette there would replay as a finished one.
- Replay: `cli.py serve` -> `replay/server.py` (`ReplayServer`) reads client requests from stdin and answers from recorded responses. No network, no subprocess, no wall-clock reads in the response path.

Key modules:

- `cassette.py` — the pydantic v2 schema (`Cassette`, `Message`, `MatchConfig`, `RedactionRule`, `Fault`, `FaultOverlay`, `FaultTarget`) plus atomic load/save and all redaction logic. `FORMAT_VERSION` gates forward-compat; loading a newer cassette raises `UnsupportedFormatVersion`.
- `matching.py` — turns a cassette's flat message list into ordered `Exchange`s (request + its response + notifications anchored between them) and matches incoming requests. Three ordering disciplines via `MatchConfig.ordering`: `per_method` (default), `strict`, `none`.
- `session.py` — `CassetteSession`: resolves the record mode into a concrete action and builds the server command. This is the "command substitution" core: it returns a command list (recording proxy or `mcp-cassette serve`) for the test to plug into its agent config. The fixture never monkeypatches the agent.
- `pytest_plugin.py` — the `mcp_cassette` fixture, `@pytest.mark.mcp_cassette` marker, and ini options. Registered via the `pytest11` entry point. Mode precedence: `MCP_CASSETTE_MODE` env > marker `mode=` > `mcp_cassette_mode` ini > `once`.
- `replay/faults.py` + `replay/new_episodes.py` — fault selection/injection and the `new_episodes` mode (replay known interactions, fall through misses to the real server and append them).
- `_stdio.py` — unbuffered stdio byte streams. Buffering must be off or interactive line framing stalls.
- `report.py` — a tiny JSON sidecar the record/replay subprocess writes and the fixture reads back, because record/replay run in a separate process from the test.

## Invariants to preserve

- No runtime dependency on the `mcp` SDK. Runtime deps are only `anyio` and `pydantic` — keep it that way (it is a library; every dep is imposed on every consumer).
- The JSON-RPC `id` is never matched on and is always re-stamped by the replay server onto the recorded response. Matching is structural over parsed JSON per `MatchConfig`.
- Recorded cassettes are never mutated by faults. Faults live in a separate `FaultOverlay` (in-memory or a `<cassette>.faults.json` sidecar). One recorded cassette drives a whole resilience matrix.
- Redaction happens at capture time on a deep copy; bytes in flight are never altered. Defaults (`*token*`, `*secret*`, `authorization`, etc.) are always on unless disabled.
- Cross-process failure signal: the replay server exits `3` on any unmatched request; the fixture surfaces misses (and empty recordings) as test failures via the report sidecar in `finalize()`.
- CI must set `MCP_CASSETTE_MODE=none` so no pipeline silently records against a live server.
- Server-initiated requests (sampling/elicitation) are recorded generically and replay on both transports (v2): anchored emission with the recorded `msg_id`, accept-anything response handling (the agent's answer is never matched against the recording), and release-on-response gating for messages recorded after the original response. There is deliberately no internal timeout when the agent never answers — pytest's own timeout applies, and the shutdown summary names the pending request.

## Conventions

Style is enforced, not optional: ruff (line length 88, rule set `E,F,I,N,UP,B,W`), mypy `strict = true`. Full type hints and Google-style docstrings on public symbols. The public API is whatever `src/mcp_cassette/__init__.py` exports in `__all__` — update it when adding or renaming exported symbols, and treat signature changes there as semver-relevant. Deliberate `noqa` suppressions carry an inline reason (see `matching.py`, `server.py`).
