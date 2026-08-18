---
artifact: ITER_05_v3
status: ready
created: 2026-08-18
scope: Post-MVP patch series 0.3.1 – 0.3.5 — pre-release audit fixes, PyPI packaging, one CLI usage-error correction, and the guide/examples documentation overhaul. No entity, route, subcommand, or flag added.
patch: true
sections_changed: [03, 04, 05]
sections_unchanged: [01, 02]
depends_on: [ITER_04_v3]
---

# ITER_05_v3 — Post-MVP patch series (0.3.1 – 0.3.5)

**Retroactive.** This artifact records work that already shipped, so the plan family stops
lying about where the code is. It is written from `git log v0.3.0..HEAD` and the
`CHANGELOG.md` entries for 0.3.1 – 0.3.5. Nothing here is pending implementation.

| Release | Date | Character |
|---|---|---|
| 0.3.1 | 2026-07-22 | docs only — guide numbered into 15 chapters |
| 0.3.2 | 2026-07-22 | docs only — the recording unit is the session |
| 0.3.3 | 2026-07-25 | first PyPI release + the pre-release audit's behaviour fixes |
| 0.3.4 | 2026-07-28 | docs, examples, CI, plus one CLI usage-error fix |
| 0.3.5 | 2026-08-01 | docs only — guide aligned to the user/operator standard |

## §01 · Concept

> Unchanged — see ITER_01_v3 § 01. The three front doors and the v3 MVP target are
> exactly as ITER_04_v3 left them.

## §02 · Architecture

> Unchanged — see SKELETON_v2 § 02 and the v3 deltas in ITER_01_v3 – ITER_04_v3.
> **This is the reason the range is a patch and not an iteration.** Across 0.3.1 – 0.3.5
> no entity was added, no CLI subcommand was added, and no flag was added or removed
> (`git diff v0.3.0..HEAD -- src/mcp_cassette/cli.py` shows no `add_argument` change).
> The one `__all__` movement — `PatternSet` — publishes a class that already existed and
> was already documented public in `mcp_cassette.lint`; it corrects an export omission
> rather than adding a surface, and is recorded in § 04 below.

## §03 · Tech Stack

Three pins moved; the dependency *set* did not. Runtime deps stay `anyio` + `pydantic`,
and `httpx`/`h11` stay the optional `[http]` extra.

- **`anyio >= 4.2`** (was `>= 4.0`). The replay and HTTP servers construct `anyio.Lock()`
  and `anyio.Event()` outside a running event loop; the adapters that make that legal
  arrived in 4.2, so on 4.0/4.1 a consumer hit `AsyncLibraryNotFoundError` the moment a
  replay server was built.
- **`hatchling >= 1.27`** — required for the PEP 639 SPDX license form
  (`license = "Apache-2.0"`, `license-files = ["LICENSE"]`) that replaced the deprecated
  table form.
- **`py.typed`** now ships in the wheel (PEP 561), so a consumer's type checker sees the
  library's hints. The file is empty; it is packaging, not code.

## §04 · Backend

No module was added or removed. Every change below is a behaviour correction inside a
module ITER_01_v3 – ITER_04_v3 already introduced.

### Failure signals that were silently green

- **Zero-message recording writes no file** (both transports). The empty file was worse
  than useless: in `once` mode its existence sent every later run down the replay branch,
  so a mis-wired first run could never re-record itself.
- **A request that matches a recorded one carrying no recorded response now counts as a
  miss** — exit `3` instead of exit `0` with a JSON-RPC error and no report entry. Covers
  the truncated-recording and hand-promoted-`.partial` cases on both transports, plus the
  `initialize` handshake, which bypasses the matcher entirely.
- **`with_faults()` sessions are finalized.** The fixture finalized the session it handed
  the test, never the derivative the fault test actually ran, so replay misses in fault
  tests went unreported and an HTTP server started on the derivative outlived the test.
  The derivative now registers on its parent.
- **The fixture no longer double-reports.** A test that failed *and* hit a replay miss
  produced a FAILED plus a teardown ERROR that buried it; teardown now skips the report
  checks when the body already failed — the behaviour `use_cassette` always had.

### Hangs turned into exits

- **Server death during `record` / `serve --new-episodes`** no longer hangs forever when
  the wrapped server exits while the agent still holds the proxy's stdin. The task-group
  cancel could never work: the client stdin read parks in an un-cancellable, non-daemon
  anyio worker thread the interpreter joins at exit. Server death now converges on the
  same hard exit the interrupt path already used — cassette finalized first, then exit
  with the wrapped server's own code.

### One error set, exit 2

Malformed input used to pick between a traceback (exit `1`) and a usage error depending on
which door you came through. `serve`, `inspect`, and `diff` now share one error set:

- `Cassette.load` raises `ValueError` (exit `2`) for a non-object JSON top level and a
  non-integer `format_version`, both of which are read before pydantic sees them.
- `serve --faults` / `inspect --faults` treat a missing or malformed overlay the same way,
  and `inspect` loads the overlay *before* printing so a bad one cannot fail a report
  halfway through.
- `record --port` / `--max-idle` with a stdio `-- CMD` is a usage error rather than two
  silently ignored flags.
- `CassetteSession._peek_transport` widened from `(FileNotFoundError, ValueError)`, which
  let a directory/permission `OSError` and `UnsupportedFormatVersion` escape raw out of
  `server_command()`.

### Path resolution in the fixture

- Default cassette paths derive from `node.path`, not `node.fspath` — the latter crashed
  under `-p no:legacypath` and would break outright when pytest drops the plugin.
- A relative `mcp_cassette_dir` resolves against pytest's `rootpath`, not the cwd, so
  running pytest from a subdirectory no longer looks somewhere other than the documented
  `<rootpath>/...` default.

### Surface hygiene before the PyPI freeze

- `PatternSet` added to `__all__` — public and documented since ITER_04_v3, missing from
  the export list. Fixed before the first release froze the surface.
- `PatternSet.for_surface` removed, inlined into its one caller `PatternSet.match`. It
  handed out an internal dataclass that exporting `PatternSet` would have frozen.
- `__version__` tracks the releases: `0.3.0` becomes `0.3.5`.

## §05 · Developer Surface

### The one user-visible behaviour change in the range

`mcp-cassette serve --new-episodes --faults` now exits `2` with *"--faults applies to
replay only; --new-episodes records novel exchanges live"*, checked before the cassette
loads. It previously behaved per transport: the http path honoured the overlay while the
stdio path built `NewEpisodesProxy` without an injector and ignored it. The programmatic
doors already enforced the rule, so http was the outlier. **An http caller combining the
two flags now gets a usage error where the run previously started.** Text output shape is
otherwise unchanged everywhere — no flag, subcommand, or exit code was added.

### Documentation

- Guide renumbered from one global 1–15 sequence to per-series codes (`GS`, `HT`, `TS`,
  `OP`), then `GS-01`/`TS-01` renamed to `getting-started.md`/`troubleshooting.md` since
  each series had exactly one member. **Old guide filenames are dead links.**
- Every how-to chapter now shows its task through all three front doors, with the
  door-independent behaviour stated once per chapter.
- New: `HT-09` (the two-step drift gate), `HT-08.4` (what a pattern pack can and cannot
  reach), `OP-03.6` (monitoring), PowerShell variants for the operator entry points, and
  a getting-started that walks all three doors instead of pytest alone.
- How-to commands run against the bundled `examples/cassettes/` fixtures, so every quoted
  output comes from a real run and every command pastes into a shell from a clone.
- The two cross-links raised in issue #9: `HT-09.3` separates replay determinism from
  drift detection, and `examples/README.md` states that a clean lint may mean "nothing to
  match". Both are unreleased as of this artifact.

### Examples and CI

- `examples/cassettes/tools-v2.mcp.json` — the drift artifact: injected description *and*
  a changed `inputSchema`, the half `lint` cannot see.
- An `examples` CI job replays the example cassettes offline and asserts the gate's exit
  codes (`0` clean, `4` drifted lint, `5` `diff --tools-only`) plus a `P001` line proving
  the starter pack fires. Nothing collected `examples/` before, so its cassettes could rot
  unnoticed and the numbers quoted in the docs had nothing enforcing them.

### Packaging

First PyPI release (0.3.3) via Trusted Publishing (OIDC, no long-lived tokens), with the
tag-vs-version check failing the build rather than burning a filename; absolute README
links (PyPI resolves relative ones against `pypi.org`); status badges; SPDX license
metadata; keyword and classifier pass; and an sdist that excludes `.agents_workspace/`,
`.claude/`, `CLAUDE.md`, and `.pre-commit-config.yaml`.

## Verification

Each release was gated by the CI suite of its day: lint, mypy strict, the layered pytest
suite on Linux/macOS/Windows across 3.12/3.13, and coverage `fail_under = 99`. From 0.3.4
the `examples` job asserts the documented exit codes. This artifact adds no code and
therefore ran no new checks.
