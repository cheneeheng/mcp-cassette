# OP-06. Parallel test runs

[← Guide index](../index.md)

- **Audience:** operators who run the suite with `pytest -n auto` (pytest-xdist) or open
  several sessions at once.
- **Goal:** parallelism never silently loses or splices a recording.

## OP-06.1 What is safe to share

**Replay is always safe.** A replay server loads the cassette read-only and builds its own
queues, so any number of workers can replay one cassette at once. Nothing is locked and
nothing slows down.

**Recording is guarded.** A session or command that would *write* a cassette first takes a
**claim**: a `<cassette>.claim` file created with an exclusive create, recording the pid,
host, pytest node id, xdist worker, mode, and start time. A second writer on the same path
sees the claim and acts by mode (OP-06.2). The claim is advisory: nothing stops a tool that
ignores it, and every mcp-cassette door honours it.

Two further collisions are fixed without a claim. Each writer saves through its own
uniquely named temp file, so two saves never splice. Under xdist the default report path
gains the worker id (`<cassette>.gw1.report.json`), so one worker's misses never overwrite
another's.

## OP-06.2 What happens on a conflict, by mode

| Mode | Cassette | A live claim already exists |
|---|---|---|
| `once` | absent, so this run would record | wait, then decide again: cassette now present → replay; still absent → record, taking the claim |
| `once` | present | no claim taken — replay is a reader |
| `none` | any | no claim taken — `none` never records |
| `all` | any | fail fast: exit `6`, or `CassetteError` |
| `new_episodes` | any | fail fast, same reasoning: two appenders would interleave |

`all` fails rather than waiting because it means "re-record", and quietly replaying instead
would do something you did not ask for.

The wait is bounded: 30 seconds for the fixture and library doors, and `--claim-wait
SECONDS` (default `0`) for `mcp-cassette record`. The failure names the holder:

```
cassette tests/cassettes/test_agent/test_search.mcp.json is held for writing by gw1 [tests/test_agent.py::test_search] (pid 48213 on ci-runner-3, started 12s ago); waited 30s. Fixes: run with -n0, give each test its own cassette path, or --force to break the claim.
```

### The `once` downgrade, and its zero-message branch

Two workers running the same test against one cassette path is the ordinary consequence of
`-n auto`, and "one records, the rest replay" is what `once` means. So the waiter decides
again once the holder finishes.

The holder may finish without writing a file: a session that captured zero messages writes
no cassette, on purpose. The waiter then records itself. Under `-n auto` that can look like
a stall, because each waiter serves its bounded wait before recording. It is a queue, not a
hang.

## OP-06.3 Stale claims

A crashed recorder cannot wedge a cassette. A claim is reclaimed, with one warning naming
the pid and the age, when either:

- its pid is no longer alive on this host, or
- it is older than 4 hours, whichever host wrote it.

A claim file that cannot be parsed (a writer died mid-write) counts as busy for 5 seconds,
then is reclaimed. `mcp-cassette record --force` breaks a live claim immediately, with a
warning. Use it only when you know the holder is gone.

Every interrupt and server-death path that hard-exits releases the claim first, so `Ctrl+C`
never leaves one behind.

## OP-06.4 Close recording sessions

A `CassetteSession` that records holds its claim until it is finalized or closed. The
fixture and both library doors do that for you. If you construct a `CassetteSession`
directly, call `finalize()` (or `close()`); an unclosed recording session blocks the next
writer until the claim goes stale.

## OP-06.5 Exit code 6 in CI

Exit `6` is the one failure worth retrying, which is why it is not `2`. A usage error never
succeeds on retry; a claim conflict usually does.

```yaml
- name: Record fresh cassettes          # scheduled job only
  run: |
    for attempt in 1 2 3; do
      uv run mcp-cassette record --cassette fresh.mcp.json --claim-wait 60 \
        -- python tools/server.py < requests.jsonl && break
      code=$?
      [ "$code" = 6 ] || exit "$code"
      echo "cassette busy, retrying ($attempt)"
    done
```

**Verify:** `uv run pytest -n 2` on a suite where two tests share one cassette path records
exactly one cassette and replays it for the other.

## OP-06.6 Keep cassettes on a local filesystem

The claim relies on an exclusive create, which older NFS implementations do not honour.
Keep the cassette directory on local disk in CI. A lock that tolerates unreliable exclusive
creates is out of scope for a testing library.

## OP-06.7 Related

- [OP-03. CI pipeline](OP-03-ci.md)
- [OP-04.1 Exit codes](OP-04-cli-reference.md#op-041-exit-codes)
- [HT-03.8 Limits worth knowing](../how-to/HT-03-use-as-a-library.md#ht-038-limits-worth-knowing)

---

[← OP-05 Runbook: replay misses and failed recordings](OP-05-runbook-replay-misses.md) · [Guide index](../index.md) · [OP-07 Pre-commit hooks →](OP-07-pre-commit.md)
