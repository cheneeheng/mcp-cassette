# HT-08. Lint with your own pattern packs

**When:** the bundled rules catch generic smells, but you need to catch *yours* — a vendor
name that must never appear in a tool description, an internal hostname that signals a
misconfigured staging server, domain-specific exfiltration phrasing.
**Prerequisites:** a cassette and a TOML file you control.

**There is no Python rule-plugin API, and that is deliberate.** A `Rule` protocol and
`register_rule()` would be a public contract to keep semver-stable forever, and would make
`lint` execute arbitrary third-party code on a supply-chain-security surface — the one
place that is least appropriate. Regex packs cover the per-project need at a fraction of
the API surface.

Linting is an operation on a cassette *file*, so this task has **two doors, not three** —
there is no pytest fixture surface. Write the pack once; both doors read the same file.

| Door | Section | Covers |
|---|---|---|
| CLI | [HT-08.2](#ht-082-with-the-cli) | `lint --pattern-pack`, `--select`/`--ignore`, `--fail-on` |
| library | [HT-08.3](#ht-083-with-the-library) | `lint_cassette()` returning a `LintReport` |

## HT-08.1 Write the pack

Both doors need this file first.

```toml
version = 1                       # pack format version; only 1 is accepted

[[patterns]]
id = "P001"                       # must not start with "R" (reserved for bundled rules)
label = "exfiltrate-env"          # names the smell in the finding message
regex = '\b(?:env|environ|\.env)\b[^.\n]{0,40}\b(?:send|post|upload|exfiltrat\w*)\b'
flags = ["i"]                     # subset of i, m, s, x
severity = "error"                # default: error
surfaces = ["description"]        # default: both description and result
message = "description describes sending environment variables off-host"  # optional
```

| Field | Meaning |
|---|---|
| `id` | Appears verbatim in output, `--select`, and `--ignore`. 1–16 characters, `[A-Za-z][A-Za-z0-9_-]*`, not starting with `R`. |
| `label` | Names the smell in the default message. |
| `regex` | Compiled, never evaluated as code. No code is imported from a pack. |
| `flags` | Any of `i`, `m`, `s`, `x`. |
| `severity` | `error` (default) or `warning`. |
| `surfaces` | `description` (recorded `tools/list`), `result` (recorded `tools/call` text), or both. |
| `message` | Replaces the default wording. |

Catastrophic backtracking in a pack regex is the pack author's risk — your file, your CI
job. There is no per-pattern timeout, because no other rule has one.

## HT-08.2 With the CLI

1. Run the pack against a cassette. `examples/lint-pack.toml` carries the same `P001`
   plus a `P002` for internal hostnames, so this runs from a clone:

   ```
   mcp-cassette lint examples/cassettes/tools-v2.mcp.json --pattern-pack examples/lint-pack.toml
   ```

   ```
   P001 error /messages/4/payload/result/tools/0/description description describes sending environment variables off-host
   R001 error /messages/4/payload/result/tools/0/description tool "echo": description matches injection pattern (override-instructions)
   R001 error /messages/4/payload/result/tools/0/description tool "echo": description matches injection pattern (conceal-from-user)
   R001 error /messages/4/payload/result/tools/0/description tool "echo": description matches injection pattern (hidden-emphasis)
   ```

2. Make it the project default so a bare `lint` means something project-specific:

   ```toml
   # pyproject.toml
   [tool.mcp_cassette.lint]
   pattern_packs = ["lint/packs/team.toml", "lint/packs/security.toml"]
   ignore = ["R003"]
   fail_on = "error"
   ```

   The CI step then stays `mcp-cassette lint cassettes/*.mcp.json`.

3. Tune strictness. `--fail-on` changes only the exit code, never a finding's recorded
   severity, so `--format json` stays a faithful record and two projects can gate the same
   cassette differently.

   ```
   mcp-cassette lint examples/cassettes/tools-v2.mcp.json --pattern-pack examples/lint-pack.toml --fail-on warning
   ```

**Verify:** the step-1 command exits `4` and its first line is the `P001` finding above,
carrying a JSON pointer to the evidence. A pack finding is an ordinary finding whose
`rule` is the pack's id — nothing about parsing lint output changes.

## HT-08.3 With the library

`lint_cassette` is the same engine the CLI calls, returning the report as data instead of
text:

```python
from mcp_cassette import ProjectLintConfig, lint_cassette

report = lint_cassette(
    "examples/cassettes/tools-v2.mcp.json",
    baseline="examples/cassettes/tools.mcp.json",   # optional; enables R002 drift comparison
    ignore=["R003"],
    packs=["examples/lint-pack.toml"],
)
for finding in report.findings:
    print(finding.rule, finding.severity, finding.locator)
```

```
P001 error /messages/4/payload/result/tools/0/description
R001 error /messages/4/payload/result/tools/0/description
R001 error /messages/4/payload/result/tools/0/description
R001 error /messages/4/payload/result/tools/0/description
R002 error /messages/4/payload/result/tools/0/description
R002 error /messages/4/payload/result/tools/0/inputSchema
```

`packs` is additive to anything a `config=ProjectLintConfig(...)` names, matching the CLI's
behaviour. Findings are sorted by locator then rule id, so the report is deterministic and
safe to snapshot.

**Verify:** the same cassette and pack produce the same finding ids you saw from the CLI —
the exit code is the only thing this door does not compute for you.

## HT-08.4 Behaviour shared by both doors

### What a pack can reach, and what it cannot

Lint reads exactly two things, from exactly two recorded methods:

| Extracted | From | Reachable by a pack pattern |
|---|---|---|
| tool `description` | a `tools/list` response | yes — `surfaces = ["description"]` |
| text content of a result | a `tools/call` response | yes — `surfaces = ["result"]` |
| tool `name` | a `tools/list` response | no — `R003` consumes it |
| tool `inputSchema` | a `tools/list` response | no — `R002` and `diff` consume it |

**A cassette can have nothing to lint.** `examples/cassettes/echo_and_add.mcp.json` records
two `tools/call`s and no `tools/list`, so it holds no description to scan and can never
produce an `R001` finding; `tools.mcp.json` is the reverse and can never produce `R004`. A
clean lint may mean "nothing matched" or "nothing to match" — `mcp-cassette inspect` shows
which methods the cassette actually contains.

**`R001`/`R004` are pattern rules; `R002`/`R003` are structural.** A pattern answers "does
this one string look wrong?", which is exactly why a pack can extend it. The other two need
something a regex over a single string cannot have: `R003` compares each tool name against
the ones already seen in the same result, and `R002` compares this cassette against a
baseline — structurally, since `inputSchema` is compared as sorted JSON so reordered keys
are correctly *not* a change.

So the ceiling is: a pack adds patterns, never surfaces and never structure.

| You want to catch | Use |
|---|---|
| A vendor name that must never appear in a description | a pack pattern |
| A base64 blob smuggled through a tool result | a pack pattern, `surfaces = ["result"]` |
| A schema that grew a `callback_url` parameter | `lint --baseline` (`R002`) or `diff --tools-only` — no wording changed, so no pattern can see it |
| A tool that appeared or vanished since the last release | `diff`; `R002` deliberately ignores both, since servers legitimately grow |
| A tool *name* matching `^(exec\|eval\|shell)` | nothing today — `name` is extracted but is not a matchable surface |

Adding a surface (say, `resources/read` results) or a structural rule is a change to
mcp-cassette itself, not something a pack can express.

### Resolution order, pinned

1. Start from the defaults.
2. Unless `--no-config`, overlay `[tool.mcp_cassette.lint]` from the nearest
   `pyproject.toml`, walking up from the current directory. Pack paths in the config
   resolve **relative to that `pyproject.toml`**, so the same CI step works from any
   subdirectory.
3. Overlay CLI flags. `--pattern-pack` is **additive** to config packs — a developer adding
   a personal pack should not lose the team's. `--select`, `--ignore`, and `--fail-on`
   **replace** their config counterparts.
4. `--select` wins over `--ignore` when a rule id appears in both, and the run prints a note
   naming the id. Silently dropping one of two contradictory flags is how a CI gate ends up
   passing for the wrong reason.

### Packs extend, never replace

There is no `--no-bundled` flag. `--select`/`--ignore` already express every combination,
including `--ignore R001 --ignore R004`, and a "disable all built-in security rules" switch
is an attractive nuisance on this surface.

Pack patterns are matched through the same code path that skips redacted surfaces, so a user
pack cannot manufacture findings out of `REDACTED` markers any more than a bundled rule can.

### Every validation error, and what it says

All exit `2`, all naming the file and the offending key:

- malformed TOML, prefixed with the pack path;
- `version` missing or not `1`;
- an unknown top-level key, or an unknown key inside `[[patterns]]` — a typo'd `severty`
  must not silently disable a rule on a security surface;
- an `R`-prefixed or malformed `id`;
- a duplicate `id` across packs (both pack paths are named; the second is rejected rather
  than silently shadowing);
- a regex that will not compile, or an unknown flag letter.

## HT-08.5 Related

- [HT-07. Redact secrets](HT-07-redact-secrets.md) — redaction hides **values** at record
  time; packs detect **phrasing** at lint time. Different jobs, often confused.
- [HT-09. Gate a drifting server surface](HT-09-gate-a-drifting-server.md)
- [OP-03. CI pipeline](../operations/OP-03-ci.md)
