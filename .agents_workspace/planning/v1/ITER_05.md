---
artifact: ITER_05
status: ready
created: 2026-07-13
scope: Windows support — make record and replay work on win32, closing the one genuine gap (graceful shutdown of the recording proxy has no SIGTERM on Windows) and dropping the "Windows out of scope" disclaimers now that the suite is green on win32
sections_changed: [03, 04]
sections_unchanged: [01, 02, 05]
depends_on: [SKELETON, ITER_01, ITER_02, ITER_03, ITER_04]
mvp: false
mvp_target: (post-MVP) The MVP terminated at ITER_04. This iteration lifts the platform restriction so a Windows developer gets the same record/replay/fault surface as Linux/macOS.
---

# ITER_05 — Windows support (post-MVP)

## §01 · Concept

> Unchanged — see SKELETON § 01. Nothing about the record/replay model is
> platform-specific; this iteration is about honoring the existing design on a third OS.

## §02 · Architecture

> Unchanged — see SKELETON § 02 and ITER_04 § 02. Same modules, same data flow. The only
> change is one platform branch inside `record/proxy.py::_watch_signals`.

### Starting position (measured, not assumed)

The library was written transport-generic on `anyio` + `pydantic` with no POSIX-only
imports in the hot paths, so most of it already runs on Windows. Measured on Windows 11 /
CPython, before any change:

- `uv run pytest` → **43 passed, 1 skipped**. The single skip is
  `test_sigterm_finalizes_cassette` (guarded `sys.platform == "win32"`).
- `anyio.open_process` works because CPython ≥ 3.8 defaults to the Proactor event loop,
  which supports subprocess pipes; `_stdio.py`'s unbuffered binary fds bypass Windows text
  -mode CRLF translation, so bytes stay verbatim in both directions.

So Windows support is **one functional gap plus a claims/CI cleanup**, not a rewrite.

### The one functional gap: graceful shutdown of the recording proxy

On POSIX the proxy finalizes a cassette on three shutdown paths: server closes stdout
(EOF), client closes stdin (EOF), and an operator SIGINT/SIGTERM. The signal path runs
through `anyio.open_signal_receiver`, which **raises `NotImplementedError` on Windows**
(asyncio has no `add_signal_handler` there). Today the code catches that and falls back to
`sleep_forever()`, meaning on Windows there is *no* graceful-interrupt path at all.

Measured consequence: sending `CTRL_BREAK_EVENT` to a live recording proxy on Windows
terminates it with `0xC000013A` (STATUS_CONTROL_C_EXIT) **before** the `finally:
_finalize()` block runs — the cassette is never written. EOF-driven shutdown still works
(that is why `test_partial_session_still_valid` passes on Windows), but an interactive
`record` session that the operator Ctrl+C's loses its recording.

Fix: give `_watch_signals` a Windows branch. Since asyncio can't register the signal with
the loop, install a plain `signal.signal` handler for `SIGINT` and `SIGBREAK` that sets a
flag, and have the async watcher poll that flag on a short `anyio.sleep`. Installing our own
`SIGBREAK` handler pre-empts the OS default (abrupt termination) so we shut down on our own
terms.

A second Windows constraint shapes the shutdown itself: unlike POSIX, Windows cannot
interrupt (EINTR) the worker thread blocked in our own stdin read, so a clean task-group
unwind would hang waiting on that un-joinable thread. The watcher therefore does not cancel
the group; on the flag it **terminates the child, finalizes the cassette, and hard-exits
(`os._exit(130)`)** — the un-joinable stdin thread dies with the process, and the cassette
is already safely written. Validated end-to-end on Windows from a real console:
`CTRL_BREAK_EVENT` → finalize → exit 130, cassette written.

Delivery caveat (testing only, not a runtime limitation): sending a console control event
programmatically needs a real Windows console shared with the target's process group. Some
launchers — notably `uv run` — run without a console, so a test that sends `CTRL_BREAK_EVENT`
can't reach the proxy there. The test asserts the exit-130/finalize behavior when a console
is present and **skips cleanly (never hangs)** when it isn't. Interactive `Ctrl+C` in a real
terminal is unaffected.

## §03 · Tech Stack

Amends SKELETON § 03, one line:

- **Platforms:** Linux, macOS, **and Windows**. (Was "Windows explicitly out of MVP
  scope.") No new dependencies — the fix is stdlib `signal` + `anyio` primitives already in
  the tree. `requires-python` and the pydantic/anyio floors are unchanged.
- **CI:** the test job runs on an OS matrix (`ubuntu-latest`, `macos-latest`,
  `windows-latest`) so the Windows claim is guarded against regression on every push. Lint
  and type-check (`ruff`, `mypy`) run once on Linux. `MCP_CASSETTE_MODE=none` is set in CI
  per the standing invariant so no pipeline silently records against a live server.

## §04 · Backend

### Changed modules

- `record/proxy.py` — `_watch_signals` (now also handed the child `Process`) gains a
  `_watch_signals_windows` fallback: on the `NotImplementedError`/`ValueError` from
  `open_signal_receiver`, install `signal.signal` handlers for `SIGINT` and (if present)
  `SIGBREAK` that set an instance flag, then poll the flag every ~100 ms. On the flag,
  terminate the child (shielded), call `_finalize()`, and `os._exit(130)` — Windows can't
  cancel the stdin-read worker thread, so a task-group unwind would hang; the hard exit
  sidesteps it after the cassette is written. If no handler can be installed (not the main
  thread — a `ValueError`/`OSError`), degrade to `sleep_forever()` as before, so nothing
  regresses off-main-thread. The interrupt still resolves to exit code **130**, matching
  POSIX.

  Scope note: `replay/new_episodes.py` is a proxy too but shuts down EOF-driven on *all*
  platforms today (it has no signal watcher even on POSIX), so it has no new Windows gap and
  is left unchanged — keeping Windows behavior at parity with POSIX rather than adding a new
  surface.

### Invariants preserved

- No new runtime dependency (still `anyio` + `pydantic`).
- The signal-driven finalize produces the same interrupted exit code (130) and the same
  valid, loadable cassette as the POSIX path.
- The off-main-thread degradation to `sleep_forever()` is retained; the Windows branch only
  activates when the POSIX signal receiver is unavailable.

### Tests for this iteration

- `test_ctrl_break_finalizes_cassette` — Windows-only (`skipif` non-win32). Spawns the
  `record` proxy in a new process group, sends the handshake and `CTRL_BREAK_EVENT`, and
  asserts exit 130 and a loadable cassette with ≥1 message. If the event can't be delivered
  (no console — e.g. under `uv run`), it kills the proxy and skips cleanly within ~6 s
  rather than hanging. The mirror of the POSIX-only `test_sigterm_finalizes_cassette`, which
  stays skipped on Windows (SIGTERM has no graceful-finalize semantics there —
  `TerminateProcess` kills unconditionally). Verified passing from a real console
  (PowerShell) and skipping under `uv run`.
- The rest of the suite is the regression guard: it already passes on Windows and must keep
  passing under the new branch.

### Run locally (Windows)

```
uv sync
uv run pytest
```

Environment variables: unchanged (`MCP_CASSETTE_MODE` only).

## §05 · Frontend / Developer Surface

> No surface change — same CLI, same fixture, same `Fault.*` API. Documentation only:
> README and `CLAUDE.md` drop the "Windows out of scope" / "not validated on Windows"
> language and state Linux/macOS/Windows support; the platform note in `CLAUDE.md` is
> rewritten to describe the Windows graceful-shutdown branch instead of declaring Windows
> unsupported.

## Out of scope

Consciously deferred (unchanged from ITER_04's list, minus Windows which this iteration
delivers):

- HTTP / Streamable-HTTP / SSE transport record and replay
- Specialized replay of server→client requests (sampling/elicitation)
- Security linting of recorded cassettes
- Content-based secret detection (entropy scanning)
- Cassette format migration tooling
- Richer inspect/diff UX
- npm/TypeScript port
- Packaged GitHub Action
- Multi-server orchestration in a single cassette
- Replay honoring recorded timing (`t_offset_ms` pacing)
- Graceful-interrupt finalize for `new_episodes` (no signal watcher on any platform today;
  EOF-driven only — out of scope until it's wanted on POSIX too)
