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

### Entry 7

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-14T00:00:00Z
**Task:** Bump Python floor to 3.12+, add examples, verify pre-commit, add version-parity CI.

**Context:** Verifying the pre-commit hook per the goal required `pre-commit run --all-files`, which reformatted 11 pre-existing src/ and tests/ files. Entry 6 had deliberately left this format drift in place ("reformatting the whole tree is out of scope"). The floor bump also collapsed uv.lock's version-conditional markers (~360 fewer lines).
**Decision:** Kept the repo-wide reformats. The user explicitly asked to check the pre-commit hook works; the hook's correct operation *is* those fixes (pure line-collapsing + lint autofixes, no semantic change — ruff 0.15.21 is now the resolved dev version, so committed code was simply stale). Reverting would leave the very hook being verified failing on `--all-files`. Ran `uv lock` to keep the lockfile consistent with `requires-python = ">=3.12"`. Left planning artifacts (SKELETON.md, earlier DECISION_LOG entries) referencing 3.10 untouched as historical record.
**Impact / Risk:** Diff now touches 11 files beyond the four deliverables, but all are mechanical format/lint autofixes the project's own tooling enforces; suite stays green (43 passed, 2 skipped). Note: `pre-commit run --all-files` only covers git-tracked files, so newly-added untracked examples/scripts were not linted by the hook — caught 4 E501s with `ruff check .` and fixed them.
**Outcome:** Applied. ruff check + format clean, mypy clean, main suite 43 passed/2 skipped, examples 3 passed offline (mode=none), version-check script exits 0.

### Entry 8

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-18T00:00:00+08:00
**Task:** Full-coverage test suite for v1 + test folder reorganization

**Context:** "Full code coverage" required choices the request left open: how to measure coverage when most code runs in subprocesses and as a pre-imported pytest plugin; what to do with unreachable defensive code; what gate threshold survives a 3-OS CI matrix; and how to split tests into unit/integration/system without package __init__ files.
**Decision:** (1) Measure with bare `coverage run -m pytest` + `patch = ["subprocess"]` + `parallel`/`combine` instead of pytest-cov — pytest-cov starts too late to see the pytest11-registered plugin and misses subprocesses; swapped the pytest-cov dev dep for `coverage>=7.10`. (2) Unreachable guards get `# pragma: no cover`/`no branch` with an inline reason rather than deletion (minimal-change bias; the guards document intent). (3) The Windows `os._exit(130)` shutdown path is unmeasurable via subprocess coverage, so it is tested in-process against private methods with a stubbed child process and mocked `os._exit`. (4) `fail_under = 99`, not 100: proxy.py lines 87-88/124-126 are POSIX-only (anyio signal receiver) and unreachable on Windows; 99 holds on every matrix OS. (5) Folder split keeps shared helpers at tests/ root via `pythonpath = ["tests"]`; test basenames stay unique across layers because there are no `__init__.py` files (pytest import-mode constraint); the CLI `--redact` subprocess test moved to integration/test_record.py, session/plugin unit tests split out of the fixture system tests.
**Impact / Risk:** Tests that reach private methods (`_watch_signals_windows`, `_handle_line`, `_replay`) will need updating if those internals are refactored. The 99 gate would mask a small future regression on the platform that already misses the POSIX-only lines.
**Outcome:** 118 passed, 2 skipped; every module 100% except record/proxy.py (94% on Windows, POSIX-only lines); gate passes locally; ruff and mypy --strict clean.

### Entry 9

**Type:** Decision
**Mode:** Autonomous (user-approved)
**Timestamp:** 2026-07-18T05:05:00Z
**Task:** First release v1.0.0 blocked by red CI; fix POSIX shutdown hang.

**Context:** First-ever CI run (PR #1) failed test_sigterm_finalizes_cassette on all
four POSIX jobs (ubuntu/macos x 3.12/3.13); Windows passed. The suite had only run on
the Windows dev box before, so the POSIX SIGTERM path was never actually exercised.
**Decision:** Root cause = a targeted SIGTERM hits only the proxy, not the separately
spawned child; the proxy cancelled its pumps then blocked forever in process.wait() on
a still-live child. Fixed by terminating the child on the interrupt path (as the Windows
watcher already did) and keying the 130 exit off the _signal_received flag instead of
cancelled-exception propagation (a task group absorbs cancellation of its own scope, so
the old `interrupted` flag was never set on POSIX). Also chose v1.0.0 (not 0.1.0) per
user and moved the Development Status classifier Alpha -> Production/Stable to match.
**Impact / Risk:** Terminating the child on interrupt is a behaviour change on the
success-shutdown path only when a signal was received; recorded data is already captured
before terminate. Coverage: new POSIX-only lines stay within the per-OS 99% budget
(Windows 99.12% verified locally; POSIX covers strictly more of proxy.py).
**Outcome:** Windows suite green locally (118 passed); pushed to PR #1; awaiting POSIX CI.

### Entry 10

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-18T09:30:00Z
**Task:** Finish the v2 plan implementation (SKELETON_v2 + ITER_01–04_v2): ITER_03
integration test matrix, stale v1 test cleanup, disconnect regression fix.

**Context:** Three forks the plans left open. (1) The stdio scripted client writes all
requests upfront, so recordings put every client request before the server's work in
`seq` and server-request anchors ("the client exchange it followed in seq",
ITER_03 §04) get misattributed to the last request — the gating tests failed against a
correct implementation. (2) The MCP SDK routes `create_message` requests without
`related_request_id` to the standalone GET stream, so the reference HTTP server's
sampling request never reached a client that held no GET stream — the recording
fixture hung. (3) The CI coverage gate (`fail_under = 99`) reads 94% locally: the
prior session's HTTP transport code has untested error/shutdown branches, and no v2
plan includes a coverage-hardening pass.
**Decision:** (1) Added an opt-in `sequential=True` mode to `run_session` (test infra
only) so the recording resembles real agent traffic; anchor semantics in the library
stay exactly as planned. (2) The reference server's `summarize` now passes
`related_request_id=ctx.request_id`, putting sampling on the triggering POST stream
(the spec's related-stream mode); GET-channel emission is covered by a hand-built
cassette test instead. (3) Left the gate and the code untouched and surfaced the gap
to the user — inventing ~50 unplanned tests or weakening a deliberate v1 gate are both
scope changes the user should call.
**Impact / Risk:** (1) Batched recordings of sampling servers still anchor
pathologically — inherent to the planned seq-based anchor semantics, now documented in
the helper's docstring. (3) CI on this branch will fail the coverage step until the
gap is addressed.
**Outcome:** Full suite 208 passed / 3 skipped on Windows; ruff and mypy --strict
clean; all plan-listed tests for ITER_01–04_v2 present and green.

### Entry 11

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-18T00:00:00+08:00
**Task:** Review codebase against v2 plans (review-against-plan)

**Context:** SKELETON_v2 frontmatter/stack says "Python 3.10+", but the repo (pyproject requires-python, ruff/mypy targets, CI matrix, datetime.UTC usage) is >= 3.12 and shipped v1 that way. Both could be "correct": the plan text vs the established repo floor.
**Decision:** Keep requires-python >= 3.12; treat the plan's "3.10+" as a stale stack line, not a directive. Lowering the floor mid-review would be a semver/support decision with code changes (datetime.UTC, 3.12-only typing) far beyond audit scope.
**Impact / Risk:** None to existing users; the plan text remains inconsistent with the repo until the plan doc is amended.
**Outcome:** Flagged in the plan-compliance report instead of changed.

### Entry 12

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-18T12:00:00+08:00
**Task:** Close the remaining coverage gaps (unit/integration/system, full coverage)

**Context:** After the prior session's edge tests, 12 statement misses and 10 partial
branches remained. Three classes needed a call: (1) two `state is not None` checks in
http/server.py whose False side is provably unreachable (the tracker plans a state for
every server request in the same cassette); (2) the POSIX-only interrupt lines in
record/proxy.py (120-122) that Windows cannot execute, already named in the coverage
config comment; (3) an identical-shape guard in replay/server.py (non-dict server
request payload) that IS reachable via a hand-edited cassette.
**Decision:** (1) annotated with `# pragma: no branch` + reason, matching the repo's
existing pragma convention, rather than writing tests that cannot construct the state;
(2) left uncovered — the documented reason fail_under is 99, covered by the POSIX CI
legs; (3) covered with a real test (hand-built cassette) instead of a pragma, since a
user-edited cassette is a legitimate input. Everything else got targeted tests in the
existing edge-test files. Also fixed a latent cross-test mutation in
test_http_replay_edges.py (protocol rewrite mutated the module-level INIT_RESP dict in
place), surfaced as stray UserWarnings in unrelated tests.
**Impact / Risk:** Two new no-branch pragmas hide those branches from future coverage
reports; if the tracker's planning invariant ever weakens, the guards are silently
untested.
**Outcome:** 262 passed / 3 skipped; every module 100% (statements and branches)
except record/proxy.py 120-122, the documented POSIX-only lines covered by the
POSIX CI legs. ruff and mypy --strict clean.

### Entry 13

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-18T00:00:00Z
**Task:** Update examples for v2 (HTTP transport, sampling replay, lint)

**Context:** While wiring the lint README demo, `mcp-cassette lint --baseline` crashed on Windows (exit 1 traceback): the R002 message uses U+2212 MINUS SIGN, which cp1252 consoles cannot encode. Fixing src/ is outside the literal "examples" scope.
**Decision:** Made the one-character fix in `src/mcp_cassette/lint/rules.py` (U+2212 -> ASCII "-") and updated the matching assertion in `tests/unit/test_lint.py`, because the documented example is broken on Windows without it. Left the broader risk (non-ASCII third-party description text in the R002 diff can still crash cp1252 consoles) unfixed and flagged it to the user.
**Impact / Risk:** Minimal; output-only change. Broader encoding hardening (e.g. stdout reconfigure in cli.py) deliberately not done.
**Outcome:** `lint --baseline` exits 4 as documented on Windows; test_lint.py green.

### Entry 14

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-18T00:00:05Z
**Task:** Lint demo cassettes for examples

**Context:** The lint demo needs a cassette with error-severity findings; no example server has a poisoned description, and adding a dedicated malicious server file felt like scope bloat.
**Decision:** Recorded a clean `tools.mcp.json` via the CLI pipe, then committed `injected.mcp.json` as an edited copy with one deliberately poisoned description (ASCII-only, matching three R001 patterns). README states it is a doctored copy and how to regenerate both. This also gives the R002 baseline-drift demo for free (clean vs poisoned pair).
**Impact / Risk:** The injected cassette is hand-edited, not a genuine recording; documented as such.
**Outcome:** `lint tools.mcp.json` exits 0; `lint injected.mcp.json` exits 4 (3x R001); with `--baseline` adds R002 with a unified diff.

### Entry 15

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-19T00:00:00Z
**Task:** Periodic crash-safety checkpoints during recording

**Context:** Recording buffered the whole session in memory and wrote once on shutdown, so a hard kill lost everything. Two sub-decisions were unspecified: where checkpoints are written, and whether they are on by default.
**Decision:** (a) Checkpoints go to a `<cassette>.partial` sidecar, never the cassette path. `CassetteSession._resolve_action` decides record-vs-replay by cassette file existence under `mode="once"`, so an in-place checkpoint left by a crash would be silently replayed as a complete recording — a correctness regression worse than the data loss it fixes. The sidecar is a valid cassette (inspectable, promotable by `mv`) and is unlinked on finalize. (b) Default ON at 5.0s (`--checkpoint-interval`, 0 disables), because data-loss handling is not something to leave opt-in. (c) HTTP checkpoints are gated on `_upstream_ok`, preserving ITER_01_v2's "no cassette file for a first-contact failure" rule.
**Impact / Risk:** Recording now touches disk periodically (only when new messages arrived). A crashed run leaves a `.partial` file the user must promote by hand — deliberate, so no truncated cassette is ever mistaken for a finished one.
**Outcome:** Verified by hard-killing a stdio recording mid-session: cassette absent, `.partial` holds the traffic. ruff + mypy strict clean; tests/unit/test_proxy_shutdown.py + tests/integration green (61 passed, 3 skipped).
