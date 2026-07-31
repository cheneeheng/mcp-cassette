# OP-03. CI pipeline

**Audience:** operators who own the pipeline.
**Goal:** cassette-backed tests run offline and deterministically, and no pipeline can
silently record against a live server.

## OP-03.0 The whole pipeline

Three steps. Everything below is the reasoning behind them.

```yaml
- name: Test                          # 1. offline, deterministic, cannot record
  env:
    MCP_CASSETTE_MODE: none
  run: uv run pytest

- name: Lint cassettes                # 2. third-party text headed for a model
  run: uv run mcp-cassette lint tests/cassettes/**/*.mcp.json --format json

- name: Drift gate                    # 3. scheduled job only, against a fresh recording
  run: uv run mcp-cassette diff tests/cassettes/tools.mcp.json fresh.mcp.json --tools-only
```

Give the test job **no MCP server credentials** ([OP-03.2](#op-032-do-not-give-ci-upstream-credentials)).
Step 3 needs a fresh recording, which is a scheduled job with its own credentials — never
the pull-request pipeline ([HT-09.5](../how-to/HT-09-gate-a-drifting-server.md#ht-095-wire-it-into-ci)).

Exit codes you will see in a red build: `3` a replay miss, `4` a lint finding, `5` the
surface drifted, `2` a usage or file error.

## OP-03.1 The one non-negotiable setting

```
MCP_CASSETTE_MODE=none
```

Set it for the whole test job. In `none` mode a missing cassette fails the test instead
of recording it, so a deleted or unmerged cassette surfaces as a red build rather than a
live call with production credentials.

Example, GitHub Actions:

```yaml
- name: Test
  env:
    MCP_CASSETTE_MODE: none
  run: uv run pytest
```

**Verify:** delete a cassette on a scratch branch and push. The job must fail with
`no cassette at <path> and recording is forbidden (mode=none)`.

## OP-03.2 Do not give CI upstream credentials

Replay contacts nothing — no network, no subprocess, no wall-clock reads in the response
path. A cassette-backed test job needs no MCP server credentials at all. Removing them
turns "CI accidentally hit production" from a policy into an impossibility.

Recording runs are a developer activity. If you must record from CI, do it in a separate,
manually triggered job with its own credentials, never in the pull-request pipeline.

## OP-03.3 Lint cassettes before they reach a model

Recorded tool descriptions and results are third-party content headed for a model's
context window. Gate them:

```yaml
- name: Lint cassettes
  run: |
    for f in tests/cassettes/**/*.mcp.json; do
      uv run mcp-cassette lint "$f" --format json
    done
```

Exit `0` means no error-severity findings; exit `4` fails the step. Warnings (`R003`
duplicate tool names, `R004` instruction-shaped results) do not fail the run on their own.

To catch the "rug pull" — a tool whose description quietly changed between recordings —
lint the new cassette against the committed one as a baseline:

```
uv run mcp-cassette lint new.json --baseline tests/cassettes/old.json --format json
```

That enables `R002`, which reports a unified diff of the drifted tool surface.

> Heuristic pattern rules, not a guarantee — a clean lint is the absence of *known*
> smells, nothing more.

### OP-03.3.1 The two-step gate

Lint with the project's own pattern packs, then diff tool surfaces. Each step gates on
its own exit code, so a failure tells you which one fired:

```yaml
- name: Lint cassettes with project packs
  run: uv run mcp-cassette lint tests/cassettes/search.mcp.json --format json
- name: Fail on a changed server surface
  run: uv run mcp-cassette diff tests/cassettes/search.mcp.json fresh.mcp.json --tools-only
```

The first step reads `[tool.mcp_cassette.lint]` from your `pyproject.toml`, so the CI
command stays generic while meaning something project-specific — see
[HT-08. Lint with your own pattern packs](../how-to/HT-08-lint-pattern-packs.md). The second
exits `5` when tool descriptions or schemas moved; see
[HT-06. Inspect and diff cassettes](../how-to/HT-06-inspect-and-diff.md) for how it differs
from `R002`. For a worked end-to-end example — a clean recording, the same server one
version later, and both steps firing — see
[HT-09. Gate a drifting server surface](../how-to/HT-09-gate-a-drifting-server.md).

## OP-03.4 Reviewing cassette changes

Cassettes are JSON with stable key order and two-space indentation, so `git diff` on them
is meaningful. In review, a cassette diff deserves the same scrutiny as a code diff:

- Did a tool `description` change? That is a supply-chain event, not a test fixture edit.
- Did any value that should be `REDACTED` come through in the clear?
- Did the message count change in a way the PR does not explain?

## OP-03.5 Keeping cassettes fresh

Replay hides upstream drift by design — that is the point, and also the risk. Schedule a
job that re-records against the real servers on a cadence you choose and opens a PR with
the diff:

```
MCP_CASSETTE_MODE=all uv run pytest tests/test_agent.py
```

Run it against the *real* servers with real credentials, on a schedule, in its own job.
Review the resulting diff by hand. Note that `all` mode cannot produce a green run for
tests that depend on replay semantics (determinism assertions, `with_faults`); those are
refreshed per-file by deleting the cassette and running in `once` mode.

## OP-03.6 Platform notes

Linux, macOS, and Windows are all supported. Shutdown is signal-driven on both families
and converges on the same behaviour: finalize the cassette, then exit `130`. SIGTERM has
no graceful-finalize semantics on Windows — use CTRL_BREAK there, or `--max-idle` for
unattended runs.

## OP-03.7 Escalation

The runbook stops here when: a test fails on replay with no cassette diff and no code
diff, `serve` exits `2` on a cassette that previously loaded, or `format_version` is
newer than the installed library understands. Those are library-level issues — capture
the cassette, the failing command, and the exact error, and file them at
[github.com/cheneeheng/mcp-cassette](https://github.com/cheneeheng/mcp-cassette).
