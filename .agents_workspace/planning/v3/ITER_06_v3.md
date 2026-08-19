---
artifact: ITER_06_v3
status: ready
created: 2026-08-19
scope: Two lint fail-open corrections — make R002 compare every recorded tool occurrence against the baseline instead of only the last, and stop the select/ignore conflict note firing (and misreporting) when the caller passed no --select. No rule id, flag, or subcommand added.
patch: true
sections_changed: [04, 05]
sections_unchanged: [01, 02, 03]
depends_on: [ITER_05_v3]
---

# ITER_06_v3 · Lint fail-open corrections

Two defects found by reading `src/mcp_cassette/lint/` against its own stated invariant —
*on a security surface every ambiguity resolves toward noisier and stricter*. Both break
that invariant in the dangerous direction: `lint` prints findings it should not, or omits
findings it should print, and in one case exits `0` on a cassette carrying `error`-severity
drift.

## §01 · Concept

> Unchanged — see ITER_01_v3 § 01. Same three front doors, same MVP target.

## §02 · Architecture

> Unchanged — see SKELETON_v2 § 02 and the v3 deltas in ITER_01_v3 – ITER_04_v3.
> **This is why the change is a patch.** No rule id is added or retired (`RULE_IDS`
> stays `("R001", "R002", "R003", "R004")`), no CLI flag changes, no model field changes.
> `LintFinding`, `LintReport`, `PatternSet`, and the pack schema are all untouched. The
> two edits live inside `rule_r002` and `run_with_notes`, both of which ITER_04_v2
> introduced and ITER_04_v3 last modified.

## §03 · Tech Stack

> Unchanged — see ITER_05_v3 § 03. No dependency added, removed, or re-pinned.

## §04 · Backend

### Defect 1 — R002 compared only the last occurrence of each tool

`rule_r002` opened with `latest = {t.name: t for t in tools}`. A dict cannot hold two
entries under one key, so every occurrence of a tool name except the final one was
discarded before any comparison happened. `engine.run_with_notes` had already flattened
every `tools/list` result into one list, so "final" meant final across the whole session,
not within one listing.

Two ways a real cassette hits this:

| Shape | R002 before | Other signal |
|---|---|---|
| same name twice inside **one** `tools/list` result | blind to the earlier entry | `R003` warns (duplicate name) |
| same name across **two** `tools/list` results (a legal re-list) | blind to the earlier listing | none — `R003` only inspects within one result |

The second row is the security hole. A server may list a tool, have the agent use it, then
re-list with the original surface. The agent was exposed to the intermediate version; the
cassette records it; `R002` compared the reverted copy to the baseline, found them equal,
and reported `clean: no findings` with exit `0`. Verified against a built fixture pair:
the same two listings in the other order exit `4`, so **the ordering of otherwise
identical content decided whether CI passed**.

`inputSchema` drift is the sharpest case because `R002` is the only rule that reads a
schema at all — `R001` and `R004` are regex matchers over description and result text.
When `R002` goes blind, a schema change is unobserved by the whole module.

**The fix.** `rule_r002` iterates every current occurrence and flags one only when it
matches *no* baseline occurrence of the same name:

- Current side: no dedup. Every recorded `ToolSurface` is checked, so an occurrence that
  drifted cannot be hidden by a later one that did not.
- Baseline side: grouped by name into a list, not a dict. A current occurrence is clean if
  it matches **any** baseline occurrence, because a baseline that legitimately re-listed
  with variations recorded all of those variations as trusted. This keeps a re-listing
  server from turning into permanent false positives.
- Unified-diff text is rendered against the last baseline occurrence, preserving the
  existing message wording and the `(+N -M lines)` counts.
- New tools are still not flagged. A name absent from the baseline is skipped exactly as
  before — servers legitimately grow.

`engine.latest_tools()` is **not** touched. It is the shared "what does the agent believe
at the end of the session" definition that `inspect --tools` and `diff` depend on, and
`rule_r002` never called it — the engine passes flattened lists directly. Keeping the two
apart is the point: `inspect` answers *what does the agent end up believing* while `R002`
must answer *what was the agent exposed to*, and those are different questions.

Cost: a tool listed N times with drift on M of them yields M findings rather than 0 or 1,
each with its own locator. That is more output on a security surface, which is the
direction the module's stated invariant points.

### Defect 2 — the select/ignore note read a synthesized value

`run_with_notes` builds `selected` from the user's `--select` list, or, when that is empty,
synthesizes the full rule set as a placeholder meaning "no filter":

```python
selected = list(rules) if rules else [*RULE_IDS, *pattern_set.rule_ids]
```

`enabled` re-checks `rules` and subtracts `ignore` on the synthesized branch. The note did
not re-check — it tested membership against `selected`, so on the ignore-only branch every
ignored id looked like a conflict and printed `note: rule <id> is both selected and
ignored; selection wins`. Nothing was selected, and ignore won. The note stated the
opposite of what the run did:

```
$ mcp-cassette lint c.mcp.json --no-config --ignore R001
note: rule R001 is both selected and ignored; selection wins
EXIT=0            # ...and no R001 finding, on a description matching two R001 patterns
```

**The fix.** The note reads the caller's own `rules` argument rather than the placeholder,
so it fires only when `--select` and `--ignore` genuinely name the same id. The wording and the
`selection wins` semantics are unchanged, and `--select R001 --ignore R001` behaves and
reads exactly as before.

## §05 · Developer Surface

Two visible changes, both in `lint` output. No flag, no exit-code semantics, no JSON schema
change.

1. **`lint --baseline` may report more findings than it did.** A cassette whose server
   re-listed a drifted tool moves from `clean: no findings` / exit `0` to one or more
   `R002 error` lines / exit `4`. This is the fix working; a pipeline that was green on
   such a cassette was green for the wrong reason. Locators point at each drifted
   occurrence individually, so `--format json` gains entries rather than changing shape.
2. **`--ignore <id>` without `--select` no longer prints a note.** The line was wrong on
   that path, and its absence is correct — ignoring a rule with nothing selected is not a
   conflict. The note still prints, unchanged, for a real `--select X --ignore X` clash.

Documentation follow-through: `docs/internals/lint.md` documents both defects as current
behavior in its *Sharp edges* section, with a pointer back to this artifact. That section
must be rewritten to describe the fixed behavior in the same change that implements it, or
it goes stale in the same release.

## Deliberately not in this patch

**Unknown rule ids in `--select` / `--ignore` are still accepted silently.** `--select r001`
(wrong case) or `--select R0001` (typo) matches no rule, enables nothing, and reports
`clean: no findings` with exit `0` on a cassette holding `error` findings — a permanently
green CI step. It is the same fail-open family and arguably the highest-risk of the three,
but it is a *third* defect, was not part of the requested scope, and its fix is a behavior
change that would start rejecting configurations that currently "work" — a changelog-worthy
validation tightening rather than a correction of wrong output. Track it separately.

Also out: extending `R003` to flag cross-listing name reuse, and a new rule for
intra-session drift detected without a baseline. The second adds a rule id and is a feature
iteration, not a patch.

## Verification

Not yet run — this artifact is the plan; the implementation is deliberately not part of it.
When implemented, gate on:

- `uv run ruff check .` and `uv run ruff format --check` on the changed files.
- `uv run mypy src` (strict).
- The existing lint unit tests: `tests/unit/test_lint.py`, `test_lint_packs.py`,
  `test_lint_project_config.py`, `test_lint_regression.py`. All four bundled R002 tests use
  single-occurrence tools, so they pin the unchanged path; the note test
  (`test_select_beats_ignore_and_prints_a_note`) covers the `--select` branch the fix
  preserves.
- Manual: a fixture pair where the current cassette re-lists `echo` with an extra
  `ssh_key` property and then reverts. Today: exit `0`. Expected after the fix: exit `4`.
  Both behaviors were confirmed against a built fixture while writing this artifact.

Whether the fix ships with new regression tests is left to the implementing change; this
artifact adds none.
