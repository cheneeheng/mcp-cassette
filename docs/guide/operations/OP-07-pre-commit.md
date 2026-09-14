# OP-07. Pre-commit hooks

**Audience:** operators and maintainers of a repository that commits cassettes.
**Goal:** an unscrubbed or unclean cassette is refused before it is ever committed.

mcp-cassette publishes two [pre-commit](https://pre-commit.com) hooks from its repository
root. Both run only on staged `*.mcp.json` files.

| Hook id | Runs | Refuses a commit when |
|---|---|---|
| `mcp-cassette-lint` | `mcp-cassette lint FILES` | any finding is at or above the fail-on severity (default: error) |
| `mcp-cassette-redaction-check` | `mcp-cassette lint --require-redaction PROFILE FILES` | a cassette's redaction manifest is missing or names another profile, or a finding fails as above |

## OP-07.1 Install

1. Add the hooks to `.pre-commit-config.yaml`, pinned to a release tag:

   ```yaml
   repos:
     - repo: https://github.com/cheneeheng/mcp-cassette
       rev: v0.4.0
       hooks:
         - id: mcp-cassette-lint
         - id: mcp-cassette-redaction-check
           args: [team-baseline]
   ```

   `examples/ci/pre-commit-config.yaml` carries the same block.

2. Install the git hook:

   ```
   pre-commit install
   ```

3. Check what is already committed:

   ```
   pre-commit run --all-files
   ```

**Verify:** stage a copy of `examples/cassettes/tools-v2.mcp.json` and commit. The commit
is refused, the output lists three `R001` findings, and pre-commit reports `exit code: 4`.

The redaction check needs its profile as its only arg. Without `args:`, the first staged
filename is taken as the profile name and the command fails as a usage error. The profile
is the one you record with, `record --redact-profile NAME` ([HT-10.2](../how-to/HT-10-redact-pii.md#ht-102-with-the-cli)).

## OP-07.2 The hooks are offline

Both hooks read local files only: no network, no MCP server, no recording. That follows
from `lint` being read-only over cassettes, and it is a property to keep: a commit hook that
could reach a live server would be intolerable. Do not add a hook that records or
re-records.

## OP-07.3 What blocks a commit

A hook fails on exit `4`. The same things that fail `lint` in CI fail it here, because both
read `[tool.mcp_cassette.lint]` from your `pyproject.toml`:

- error-severity findings (`R001`, `R002`, and pack rules at `error`) always block;
- warnings (`R003` to `R007`) block only under `fail_on = "warning"`;
- the redaction check additionally blocks on a missing or mismatched manifest, and says
  which `record` command produces the right one.

A local commit and a CI run therefore agree about the same cassette.

## OP-07.4 Why there is no `diff` hook

`diff` compares two cassettes, and its useful baseline is a git ref: the same cassette on
the target branch. Pre-commit hands a hook a list of changed files and nothing else.
Resolving a ref from inside a hook means shelling out to git, which is where hook
integrations become fragile. Drift gating belongs to CI, where the packaged Action resolves
the merge-base for you ([OP-03](OP-03-ci.md)).

## OP-07.5 Related

- [OP-03. CI pipeline](OP-03-ci.md) — the same checks at merge time.
- [HT-10. Redact PII from free text](../how-to/HT-10-redact-pii.md) — producing a cassette the
  redaction check accepts.
