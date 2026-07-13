# Decision Log

### Entry 1

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-12T00:00:00Z
**Task:** Implement mcp-cassette from SKELETON + ITER_01..04 plan (full sequence to MVP terminator ITER_04).

**Context:** The plan lists per-iteration pytest suites and a tests/reference_server as deliverables, but my standing instructions default to not writing tests unless asked. This materially changes deliverable scope.
**Decision:** Asked the user; they chose "Build everything incl. test suites". Implementing production code plus the full pytest suites the plan specifies.
**Impact / Risk:** Larger surface; test suites exercise stdio subprocess behavior that is Linux/macOS-targeted (Windows out of MVP scope per SKELETON §03) and may not run on the current win32 host.
**Outcome:** Full suite implemented; 43 passed, 1 skipped on the win32 host. The one skip is the SIGTERM-finalize test (POSIX-only signal handling, Windows out of MVP scope). Record/replay/faults/fixture all run on Windows despite it being out of scope.

### Entry 2

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-12T00:00:00Z
**Task:** Cassette schema closed-set field typing.

**Context:** Architecture invariant prefers Python StrEnum for closed sets, but StrEnum is 3.11+ and the plan floor is Python 3.10.
**Decision:** Use typing.Literal for closed-set fields (sender, kind, transport, ordering, on_unmatched, fault type, malformed strategy). Literal is a closed set, pydantic-native, and 3.10-compatible.
**Impact / Risk:** None material; validation errors are still readable and the set is closed to external callers.
**Outcome:** Applied.

### Entry 3

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-12T00:00:00Z
**Task:** Build strategy across the iteration sequence.

**Context:** implement-from-plan says implement section by section; but the target is the whole sequence to the ITER_04 MVP terminator, and several files (proxy.py, replay/server.py, cli.py, matching.py) are stubbed in SKELETON then made real in later iterations.
**Decision:** Build each module directly to its final ITER_04 state rather than writing a stub and overwriting it repeatedly. Skeleton-only stub semantics (loud failure messages) are preserved only for surfaces that remain stubs at their point in the sequence; since the target is the full MVP, all surfaces reach real implementations.
**Impact / Risk:** Avoids churn; the intermediate loud-stub states are not committed, which is fine because the target is the terminal MVP, not an intermediate iteration.
**Outcome:** Applied.

### Entry 4

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-13T00:00:00Z
**Task:** `new_episodes` record-mode semantics (ITER_03 §04).

**Context:** The plan specifies `new_episodes` as "replay; unmatched requests fall through to the real server and are appended." For a stateful stdio MCP session this is under-specified: a freshly spawned real server has no session context, so forwarding only misses would send a mid-session `tools/call` to an uninitialized server.
**Decision:** In `NewEpisodesProxy`, always forward `initialize` and client notifications live (giving the real server a valid handshake) and intercept only matched non-initialize requests from the cassette; misses fall through and are appended. Documented the ordering caveat (concurrent free-running server notifications during a fall-through are captured but not ordering-guaranteed) in the module docstring.
**Impact / Risk:** Correct for the serial request/response sessions agent suites produce; the caveat is noted. Appended episodes go through the same redaction path as fresh recordings.
**Outcome:** Applied; `test_new_episodes_appends_novel_call` passes.

### Entry 5

**Type:** Note
**Mode:** Autonomous
**Timestamp:** 2026-07-13T00:00:00Z
**Task:** Two transport bugs found and fixed during validation.

**Context:** Integration tests initially hung or dropped the last response.
**Decision:** (1) argparse `REMAINDER` positional swallowed flags placed after the cassette positional in `serve`; replaced with a manual split of argv on the first standalone `--`. (2) anyio `FileReadStream(sys.stdin.buffer)` uses a *buffered* reader whose `read(n)` blocks until n bytes or EOF, stalling an interactive proxy that has received one short line; `FileWriteStream(sys.stdout.buffer)` buffers responses. Fixed by reading/writing unbuffered raw fds (`os.fdopen(..., buffering=0)`) in `src/mcp_cassette/_stdio.py`.
**Impact / Risk:** Both are core to the streaming stdio transport working at all. Covered by the record/replay/fault suites.
**Outcome:** Fixed; suite green.

### Entry 6

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-13T00:00:00Z
**Task:** ITER_05 — Windows support (post-MVP): scope and shutdown design.

**Context:** The goal was "add Windows support." Empirically the suite already passed on Windows (43 passed, 1 skipped); the only real gap was graceful shutdown of the recording proxy on Ctrl+C/Ctrl+Break, because `anyio.open_signal_receiver` is unavailable on Windows and the code fell back to `sleep_forever()`. A first design (poll a `signal.signal` flag, then cancel the task group like POSIX) hung: Windows cannot EINTR-interrupt the worker thread blocked in our own stdin read, so the task-group unwind never completes.
**Decision:** On the Windows interrupt path, do not cancel the group. Instead terminate the child (shielded), call `_finalize()` to write the cassette, then `os._exit(130)`. The un-joinable stdin thread dies with the process; the cassette is already saved. POSIX keeps its clean cancel-based unwind. Left `new_episodes` unchanged (EOF-driven on all platforms already — no new Windows gap). Included a minimal GitHub Actions CI (OS matrix incl. windows-latest) to guard the claim, but dropped the `ruff format --check` step because the repo has pre-existing format drift (never `ruff format`'d, only lint-clean); reformatting the whole tree is out of scope for this task.
**Impact / Risk:** `os._exit` is blunt but correct for a shutdown path with the artifact already persisted; commented in-source. The Ctrl+Break test needs a real Windows console to deliver the event, so it skips (never hangs) under `uv run`/pty launchers — it asserts exit-130/finalize when a console is present (verified from PowerShell) and skips cleanly otherwise.
**Outcome:** Applied. `uv run pytest` → 44 passed, 1 skipped; ruff + mypy clean; Ctrl+Break finalize verified rc 130 from a real console.
