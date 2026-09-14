# OP-03. CI pipeline

**Audience:** operators who own the pipeline.
**Goal:** cassette-backed tests run offline and deterministically, no pipeline can
silently record against a live server, and every committed cassette is linted and
drift-checked against its base branch.

## OP-03.0 The whole pipeline

Two jobs. Everything below is the reasoning behind them.

```yaml
jobs:
  test:                                 # offline, deterministic, cannot record
    runs-on: ubuntu-latest
    env:
      MCP_CASSETTE_MODE: none
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run pytest

  cassettes:                            # lint + drift vs the pull request's base branch
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0                # required: see OP-03.3.1
      - uses: cheneeheng/mcp-cassette@v0.4.0
        with:
          checks: lint,diff
```

Give the test job **no MCP server credentials** ([OP-03.2](#op-032-do-not-give-ci-upstream-credentials)).
A drift check against a *fresh* recording needs credentials, so it is a scheduled job of
its own, never the pull-request pipeline ([OP-03.3.2](#op-0332-the-scheduled-drift-job)).

Exit codes you will see in a red build: `2` a usage or file error (including a missing
merge-base), `3` a replay miss, `4` a lint finding or a missing redaction profile, `5` the
tool surface drifted, `6` a cassette held for writing by another process.

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
context window. The packaged Action gates them on every pull request. It is a composite
action shipped from this repository's root, so `uses: cheneeheng/mcp-cassette@v0.4.0` runs
exactly mcp-cassette 0.4.0: the Action and the library come from one tag and cannot drift.

| Input | Default | Purpose |
|---|---|---|
| `cassettes` | `tests/cassettes/**/*.mcp.json` | glob of cassettes to check |
| `checks` | `lint` | `lint`, `diff`, or `lint,diff` |
| `baseline-ref` | the pull request's base SHA | what `R002` and `diff` compare against |
| `pattern-packs` | *(empty)* | pack paths passed to `lint`, one per line |
| `require-redaction` | *(empty)* | profile every cassette's manifest must name |
| `fail-on` | `error` | passed to `lint --fail-on` |
| `version` | the Action's own tag | the mcp-cassette version installed |

Outputs: `findings`, `report-path`, `exit-code`.

For each cassette the Action takes the same file as it exists at the merge-base of
`baseline-ref` and `HEAD`, lints the current file against it (enabling `R002`, the "rug
pull" check), and with `diff` also compares tool surfaces. Findings appear three ways:
annotations on the pull request's changed files, a table in the job summary, and the JSON
reports uploaded as a workflow artifact.

A glob that matches nothing fails the job (exit `2`) rather than passing it, because a
misconfigured gate that reports success is worse than none.

> Heuristic pattern rules, not a guarantee — a clean lint is the absence of *known*
> smells, nothing more.

### OP-03.3.1 Why `fetch-depth: 0` is required

`actions/checkout` fetches one commit by default, which leaves no merge-base to resolve.
Without a baseline, `R002` would quietly stop gating, so the Action fails instead:

```
ci_check.sh: cannot resolve a merge-base between <sha> and HEAD: this checkout lacks the shared history. Set 'fetch-depth: 0' on actions/checkout.
```

A cassette that is **new** on the branch is a different case: there is nothing it could
have drifted from, so its baseline checks are skipped with a note and it is still linted.

**Verify:** remove `fetch-depth: 0` on a scratch branch. The `cassettes` job must exit `2`
with the message above.

The Action pins the version it installs. Floating to the latest release would let a library
release change a pipeline's verdict with no change to your repository. On a ref that is
not a version tag (a branch or a SHA), set the `version` input explicitly.

### OP-03.3.2 The scheduled drift job

The merge-base check compares two *committed* cassettes. Whether the real server still
matches the committed cassette needs a *fresh* recording, which needs real credentials and
network, so it is a scheduled job of its own, never the pull-request pipeline, which must
stay offline ([OP-03.2](#op-032-do-not-give-ci-upstream-credentials)).

```yaml
- name: Record a fresh surface        # scheduled job, real credentials
  run: |
    printf '%s\n' \
      '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"ci","version":"1.0"}}}' \
      '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
      '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
      | uv run mcp-cassette record --cassette fresh.mcp.json -- python tools/server.py

- name: Content gate
  run: uv run mcp-cassette lint fresh.mcp.json --format json

- name: Drift gate
  run: uv run mcp-cassette diff tests/cassettes/tools.mcp.json fresh.mcp.json --tools-only
```

**Verify:** the record step exits `0` and writes `fresh.mcp.json` with a non-zero
`messages` count and no `unanswered requests:` line
(`uv run mcp-cassette inspect fresh.mcp.json`). A zero count means the piped requests
never reached the server, and both gates below it would then pass on an empty file.
An `unanswered requests:` line means the server never answered — usually it failed to
launch — and the gates would then pass on a broken file, which a message count alone
cannot distinguish from a short one. `record` returns the wrapped server's own exit
code, so a non-zero step is the earliest signal.

**If it fails:** a red drift gate is a diff to read, not a failure to re-record away —
[HT-09.5](../how-to/HT-09-gate-a-drifting-server.md#ht-095-when-the-gate-goes-red).

### OP-03.3.3 Wiring it yourself

The Action runs `scripts/ci_check.sh` from the same tag; outside GitHub, call the commands
directly. Each step gates on its own exit code, so a failure tells you which one fired:

```yaml
- name: Lint cassettes with project packs
  run: uv run mcp-cassette lint tests/cassettes/**/*.mcp.json --format json
- name: Lint against the committed baseline
  run: |
    git show origin/main:tests/cassettes/search.mcp.json > baseline.mcp.json
    uv run mcp-cassette lint tests/cassettes/search.mcp.json --baseline baseline.mcp.json
- name: Fail on a changed server surface
  run: uv run mcp-cassette diff baseline.mcp.json tests/cassettes/search.mcp.json --tools-only
```

`lint` reads `[tool.mcp_cassette.lint]` from your `pyproject.toml`, so the command stays
generic while meaning something project-specific — see
[HT-08. Lint with your own pattern packs](../how-to/HT-08-lint-pattern-packs.md). `diff`
exits `5` when tool descriptions or schemas moved; see
[HT-06. Inspect and diff cassettes](../how-to/HT-06-inspect-and-diff.md) for how it differs
from `R002`, and [HT-09. Gate a drifting server surface](../how-to/HT-09-gate-a-drifting-server.md)
for a worked example with both steps firing. Add `--annotate github` to `lint` to get the
same pull-request annotations the Action emits.

**Verify:** run the gate against the bundled fixtures from a clone; the two commands must
exit `4` and `5` respectively, with no server and no network.

```
uv run mcp-cassette lint examples/cassettes/tools-v2.mcp.json
uv run mcp-cassette diff examples/cassettes/tools.mcp.json \
                         examples/cassettes/tools-v2.mcp.json --tools-only
```

To stop an unclean cassette before it is even committed, use the pre-commit hooks
([OP-07](OP-07-pre-commit.md)).

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
refreshed per-file by deleting the cassette and running in `once` mode. Running that job
with `pytest -n auto` is safe: see [OP-06](OP-06-parallel-test-runs.md).

## OP-03.6 Monitoring

There is no long-lived process to monitor. Every mcp-cassette process is scoped to one
test session or one recording run and exits when it ends, so there is no daemon, no
health endpoint, and no metrics to scrape. What replaces monitoring is exit codes, the
Action's report artifact, and two periodic jobs:

| Watch | Healthy | Unhealthy means |
|---|---|---|
| Test job exit code | `0` | `3` — a replay miss; go to [OP-05.1](OP-05-runbook-replay-misses.md#op-051-incident-1--replay-had-unmatched-requests) |
| `cassettes` job exit code | `0` | `4` — a finding or a missing redaction profile; `5` — a surface drifted from the base branch; `2` — misconfigured (see OP-03.3.1) |
| The Action's `mcp-cassette-report-*` artifact | JSON reports with no error findings | the per-cassette findings behind a red `cassettes` job, kept after the log scrolls away |
| Scheduled drift job exit code | `0` | `5` — the upstream tool surface moved ([OP-03.3.2](#op-0332-the-scheduled-drift-job)) |
| `MCP_CASSETTE_MODE` in the test job | `none` | anything else — the pipeline can record against a live server |
| Age of the newest cassette | within your refresh cadence | replay is drifting further from the real server ([OP-03.5](#op-035-keeping-cassettes-fresh)) |

The last two are the ones nothing fails on by itself. A pipeline that quietly lost
`MCP_CASSETTE_MODE=none` stays green until the day it records production traffic into a
cassette, so assert it rather than trusting it:

```yaml
- name: Assert recording is forbidden
  run: test "$MCP_CASSETTE_MODE" = none
```

**Verify:** unset the variable on a scratch branch; the step must fail the job.

## OP-03.7 Platform notes

Linux, macOS, and Windows are all supported. Shutdown is signal-driven on both families
and converges on the same behaviour: finalize the cassette, then exit `130`. SIGTERM has
no graceful-finalize semantics on Windows — use CTRL_BREAK there, or `--max-idle` for
unattended runs. The Action's steps run under `bash` on every runner, Windows included.

## OP-03.8 Escalation

The runbook stops here when: a test fails on replay with no cassette diff and no code
diff, `serve` exits `2` on a cassette that previously loaded, or `format_version` is
newer than the installed library understands. Those are library-level issues — capture
the cassette, the failing command, and the exact error, and file them at
[github.com/cheneeheng/mcp-cassette](https://github.com/cheneeheng/mcp-cassette).
