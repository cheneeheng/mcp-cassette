---
artifact: ITER_06_v3
status: ready
created: 2026-08-19
scope: Two lint fail-open corrections — make R002 compare every recorded tool occurrence against the baseline instead of only the last, and stop the select/ignore conflict note firing (and misreporting) when the caller passed no --select. No rule id, flag, or subcommand added. Also carries a findings register (F1-F6) for defects outside lint/ that are recorded, not implemented here.
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

## Findings register — defects outside `lint/`, not in this patch

Recorded here because they were found while writing `docs/internals/` and need an owner;
**none of them is implemented by this artifact**, which stays the two-defect `lint` patch
its frontmatter describes. Each needs its own iteration — F1 and F2 together, F3 and F4
together, F5 and F6 together would be three sensible patches.

They belong in the same register because four of the six are the same failure family this
patch exists to correct: *a security or correctness check that resolves toward quieter and
looser*. F1 and F2 are redaction fail-open; F3 and F4 are validation skipped on exactly
the path where it matters.

| id | Defect | Module | Class | Severity |
|---|---|---|---|---|
| F1 | default redaction globs miss hyphenated key names | `cassette.py` | fail-open | high |
| F2 | any line the JSON-RPC classifier rejects bypasses redaction | `cassette.py` + `record/recorder.py` | fail-open | high |
| F3 | `resolve_mode` skips validating `mode=` when the env var is set | `session.py` | fail-silent | medium |
| F4 | `server_command`'s HTTP guard is skipped under `mode=all` | `session.py` | data loss | medium |
| F5 | `diff --tools-only` claims "no structural differences" | `cli.py` | wrong output | low |
| F6 | `inspect` output-mode flags silently shadow each other | `cli.py` | accepted-and-ignored | low |

### F1 — default redaction globs miss hyphenated key names

`default_redaction_rules()` ships `*apikey*` and `*api_key*`. Neither glob matches a
hyphen, so the conventional HTTP spelling passes through untouched:

```
{'X-API-Key': 'k', 'ApiKey': 'k'}  ->  {'X-API-Key': 'k', 'ApiKey': 'REDACTED'}
```

`X-Auth-Token`, `Api-Key`, and `Access-Token` fall the same way. This bites payloads
carrying a header dict — an agent passing credentials through `tools/call` arguments, or a
recorded proxy configuration — and the result is a committed cassette holding a live
credential while `redacted` reports the message was scrubbed.

**Fix shape.** Add hyphen variants to the default set, or normalize separators before
matching (`fnmatch` the key with `-` and `_` folded together). The second is fewer rules
and covers spellings nobody enumerated, but it changes what an existing *custom* glob
matches, so it needs its own regression test. Either way this is a widening of what gets
scrubbed, which is the safe direction, but it is still a behavior change: a cassette
re-recorded after the fix will differ from its committed predecessor.

### F2 — any line the classifier rejects bypasses redaction

`apply_redactions` returns a `str` payload untouched, because structural redaction needs
keys. The trap is which lines arrive as strings. `SessionRecorder.on_message` stores the
original **text** on three routes, discarding a successful decode where it had one
(`record/recorder.py:100`, `:113`):

| Route to `kind: "raw"` | Warning? |
|---|---|
| the line does not parse at all | once per session, latched |
| valid JSON that is not an object (an array, a number) | none |
| a valid JSON **object** with no `method` and no `id` | none |

Verified end to end through `SessionRecorder` with the defaults on:

```
warnings: 1
kind=raw   redacted=False  payload='["Bearer sk-live-42"]'
kind=raw   redacted=False  payload='{"authorization": "sk-live-9", oops'
kind=raw   redacted=False  payload='{"authorization": "sk-live-7"}'
```

The third line is the one that makes this more than a malformed-input edge case. It is
well-formed JSON, it decoded cleanly, and its key matches the always-on `authorization`
default — but the classifier could not call it a request, response, or notification, so it
fell through to `raw` and the decoded object was thrown away. No warning fires on that
route or the array route.

`lint` is not a backstop: its four rules cover injection phrasing, description drift,
duplicate names, and instruction-shaped results, and `engine.py:91` *skips* text already
marked redacted rather than hunting for text that was not.

**Fix shape.** Two independent halves, and the first is the cheap one.
*(a)* Redact the decoded object on route 3 before falling back to raw — the recorder
already holds `obj`, so this is passing it to `_append` instead of `text`, and it closes
the widest route without touching the redaction engine.
*(b)* Warn on routes 2 and 3, and drop the one-shot latch on route 1 (or count and report
at finalize) so "how many lines did this affect" is answerable. Note that the latch is
itself already documented as a sharp edge in `docs/internals/record.md`, so the two should
be fixed together.
Neither half redacts genuinely unparseable text; that is not solvable structurally and
should stay a documented limitation with the `"kind": "raw"` grep as the mitigation.

### F3 — `resolve_mode` skips validating `mode=` when the env var is set

The env branch returns before `explicit` is ever checked (`session.py:64`):

```
no env   + mode=nope -> ValueError: invalid mcp_cassette mode 'nope' from mode= argument
env=none + mode=nope -> 'none'   (no error raised)
```

The resolved value is always correct, so nothing runs wrong. What breaks is where the typo
surfaces: caught on a developer's machine, where no env var is set, and swallowed in CI,
where `MCP_CASSETTE_MODE=none` is the standing invariant. A test that meant to say
something about its mode ships saying nothing, and the one environment that would have
told you is the one that stays quiet.

**Fix shape.** Validate `explicit` before consulting the environment, then apply the
existing precedence. Two lines, and it cannot change any resolved mode — only which
invalid inputs raise. Worth pairing with the `--select`/`--ignore` unknown-id defect
deferred above: same "invalid input accepted silently" family, different module.

### F4 — `server_command`'s HTTP guard is skipped under `mode=all`

The guard reads `if action != "record" and self._peek_transport() == "http"`
(`session.py:230`), so it is bypassed in exactly the action that overwrites the file.
Verified against a real `transport: "http"` cassette:

```
mode=once -> CassetteError: cassette http_echo_and_add.mcp.json was recorded over
             Streamable HTTP; use mcp_cassette.server_url(real_url) instead ...
mode=all  -> -m mcp_cassette record --cassette http_echo_and_add.mcp.json ... -- python server.py
```

Under `once` the mistake is refused. Under `all` the same mistake returns a stdio
recording command aimed at the HTTP cassette's path, and a successful run replaces a
committed HTTP recording with a stdio one. `mode=all` is what a developer sets to
re-record deliberately, so the destructive path is the one with no guard.

**Fix shape.** Run the check unconditionally. `_peek_transport` (`session.py:436`) already
returns `"stdio"` for a missing or unreadable cassette, so the no-cassette case the guard
was avoiding is handled. `server_url` carries the mirror-image guard with the same shape
and needs the same edit.

### F5 — `diff --tools-only` claims "no structural differences"

`_cmd_diff` blanks three collections and recomputes `identical` from `tools` alone
(`cli.py:686`), then `_print_diff` renders the generic sentence:

```
$ mcp-cassette diff echo_and_add.mcp.json deterministic.mcp.json
exit=5

$ mcp-cassette diff echo_and_add.mcp.json deterministic.mcp.json --tools-only
identical: no structural differences
exit=0
```

The exit code is right — the flag asked about tools and the tools match. The sentence is
not: it makes a claim about the whole cassette, and never mentions the flag that narrowed
the question. A CI log carrying that line misleads whoever reads it later.

**Fix shape.** Text only, no exit-code change: say `identical: no tool surface
differences` when `--tools-only` is set. `--format json` is already unambiguous, since the
empty `metadata`/`methods`/`sequence` arrays are visible in the document.

### F6 — `inspect` output-mode flags silently shadow each other

`_cmd_inspect` checks `--format json`, then `--timeline`, then `--tools`, returning at the
first hit. `--tools --format json` ignores `--tools` (harmless — the JSON document always
carries a `tools` array), but `--timeline --tools` drops `--tools` outright and the user
does not get what they asked for. Nothing complains, because these are four independent
flags rather than a mutually exclusive group.

Adjacent, same command, worth deciding in one pass: `--method`/`--grep` filter the message
list but not the tool list, and `timing span` is computed over the filtered messages
(`cli.py:674`) under a label that does not say so.

**Fix shape.** Put `--timeline` and `--tools` in an argparse mutually exclusive group so
the combination is rejected rather than silently resolved. The filter/label asymmetry is a
separate call — either label the span as filtered, or compute it over the whole cassette —
and should not be bundled in without deciding which.

### Documentation follow-through

All six are documented as *current, unfixed* behavior in `docs/internals/`:
F1, F2 in `cassette.md`; F3, F4 in `session.md`; F5, F6 in `cli.md`. F2's entry in
`cassette.md` links back to this artifact. Each *Sharp edges* entry must be rewritten in
the same change that implements its fix, or it goes stale in the same release — the same
rule §05 states for `lint.md`.

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
