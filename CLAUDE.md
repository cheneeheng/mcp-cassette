# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mcp-cassette` is "vcrpy for MCP": record real MCP stdio sessions between an agent and an MCP server into cassettes (structured JSON), then replay them as deterministic mock servers so agent test suites stop hitting live servers. See `README.md` for the user-facing surface.

## Commands

Tooling is `uv`. The `mcp` SDK is a dev-only dependency (used by the reference server in tests); it is NOT a runtime dependency and must never become one.

```
uv sync                                  # install deps + dev group
uv run pytest                            # run the whole suite
uv run pytest tests/test_replay.py       # one file
uv run pytest tests/test_replay.py::test_name -x   # one test, stop on first failure
uv run ruff check .                      # lint (also: ruff format .)
uv run mypy src                          # type-check (strict mode is on)
uv build                                 # build wheel + sdist
```

Integration tests spawn subprocesses that run the reference server and the proxy/replay server. They shell out to `sys.executable`, so run them through `uv run` so the subprocess inherits the venv.

Platform note: Linux, macOS, and Windows are supported (CI runs the suite on all three; see ITER_05). Proxy shutdown is signal-driven: on POSIX via `anyio.open_signal_receiver` (SIGINT/SIGTERM) cancelling the task group; on Windows via a `signal.signal` SIGINT/SIGBREAK handler that `_watch_signals_windows` polls (asyncio has no `add_signal_handler` there). The Windows path can't cancel the stdin-read worker thread (no EINTR), so on interrupt it terminates the child, finalizes, and `os._exit(130)` rather than unwinding the task group. Both paths finalize the cassette and exit 130; off the main thread, where no handler can be installed, shutdown degrades to EOF-driven. SIGTERM has no graceful-finalize semantics on Windows — `test_ctrl_break_finalizes_cassette` (CTRL_BREAK_EVENT) is the win32 counterpart to the POSIX-only `test_sigterm_finalizes_cassette`; it needs a real console to deliver the event, so it skips (never hangs) under launchers like `uv run` that run without one.

## Architecture

Everything operates at the transport level: newline-delimited JSON-RPC over stdio. Messages are treated semi-opaquely — captured verbatim whatever the method — so the library works with any MCP client unmodified and never imports the `mcp` SDK at runtime.

Data flow, both directions:

- Record: `cli.py record` -> `record/proxy.py` (`StdioRecordingProxy`) spawns the real server and runs three line pumps (`record/pump.py`) in one anyio task group: client->server, server->client, and server-stderr->our-stderr (stderr is forwarded, never swallowed, to avoid hiding logs and deadlocking on a full pipe). A `SessionRecorder` (`record/recorder.py`) taps each line, classifies it by JSON-RPC shape, timestamps against a monotonic clock, and applies redaction to a deep copy at capture time. On any shutdown path the session is finalized into a `Cassette` and saved atomically.
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
- Server-initiated requests (sampling/elicitation) are recorded generically but not replayable in the MVP; `ReplayServer` refuses such cassettes at load with `UnsupportedCassetteFeature`.

## Conventions

Style is enforced, not optional: ruff (line length 88, rule set `E,F,I,N,UP,B,W`), mypy `strict = true`. Full type hints and Google-style docstrings on public symbols. The public API is whatever `src/mcp_cassette/__init__.py` exports in `__all__` — update it when adding or renaming exported symbols, and treat signature changes there as semver-relevant. Deliberate `noqa` suppressions carry an inline reason (see `matching.py`, `server.py`).
