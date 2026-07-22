# 9. Lint with your own pattern packs

**When:** the bundled rules catch generic smells, but you need to catch *yours* — a
vendor name that must never appear in a tool description, an internal hostname that
signals a misconfigured staging server, domain-specific exfiltration phrasing.
**Prerequisites:** a cassette and a TOML file you control.

**There is no Python rule-plugin API, and that is deliberate.** A `Rule` protocol and
`register_rule()` would be a public contract to keep semver-stable forever, and would
make `lint` execute arbitrary third-party code on a supply-chain-security surface — the
one place that is least appropriate. Regex packs cover the per-project need at a fraction
of the API surface.

## 9.1 A starter pack

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

```bash
mcp-cassette lint demo.mcp.json --pattern-pack team-rules.toml
```

**Verify:** a cassette containing the phrase exits 4 and prints `P001 error /messages/...`
with the JSON pointer to the evidence.

A pack finding is an ordinary finding whose `rule` is the pack's id — nothing about
parsing lint output changes.

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

## 9.2 Make it the project default

```toml
# pyproject.toml
[tool.mcp_cassette.lint]
pattern_packs = ["lint/packs/team.toml", "lint/packs/security.toml"]
ignore = ["R003"]
fail_on = "error"
```

A CI step stays `mcp-cassette lint cassettes/*.mcp.json` while meaning something
project-specific.

## 9.3 Resolution order, pinned

1. Start from the defaults.
2. Unless `--no-config`, overlay `[tool.mcp_cassette.lint]` from the nearest
   `pyproject.toml`, walking up from the current directory. Pack paths in the config
   resolve **relative to that `pyproject.toml`**, so the same CI step works from any
   subdirectory.
3. Overlay CLI flags. `--pattern-pack` is **additive** to config packs — a developer
   adding a personal pack should not lose the team's. `--select`, `--ignore`, and
   `--fail-on` **replace** their config counterparts.
4. `--select` wins over `--ignore` when a rule id appears in both, and the run prints a
   note naming the id. Silently dropping one of two contradictory flags is how a CI gate
   ends up passing for the wrong reason.

## 9.4 `fail_on` is the strictness knob

```bash
mcp-cassette lint demo.mcp.json --fail-on warning
```

It changes only the exit code (4 when any finding at or above the threshold exists). It
never rewrites a finding's severity, so `--format json` stays a faithful record and two
projects can gate the same cassette differently.

## 9.5 Packs extend, never replace

There is no `--no-bundled` flag. `--select`/`--ignore` already express every combination,
including `--ignore R001 --ignore R004`, and a "disable all built-in security rules"
switch is an attractive nuisance on this surface.

Pack patterns are matched through the same code path that skips redacted surfaces, so a
user pack cannot manufacture findings out of `REDACTED` markers any more than a bundled
rule can.

## 9.6 Redaction is a different job

Redaction hides **values** at record time; packs detect **phrasing** at lint time. They
are often confused. See [8. Redact secrets](08-redact-secrets.md).

## 9.7 Every validation error, and what it says

All exit 2, all naming the file and the offending key:

- malformed TOML, prefixed with the pack path;
- `version` missing or not `1`;
- an unknown top-level key, or an unknown key inside `[[patterns]]` — a typo'd `severty`
  must not silently disable a rule on a security surface;
- an `R`-prefixed or malformed `id`;
- a duplicate `id` across packs (both pack paths are named; the second is rejected rather
  than silently shadowing);
- a regex that will not compile, or an unknown flag letter.

## 9.8 Related

- [8. Redact secrets](08-redact-secrets.md)
- [13. CI pipeline](../operations/13-ci.md)
