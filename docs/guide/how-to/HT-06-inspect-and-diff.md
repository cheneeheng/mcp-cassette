# HT-06. Inspect and diff cassettes

**When:** a replay missed and you need to see what was actually recorded, or you
re-recorded after a server upgrade and need the delta.
**Prerequisites:** a cassette file. Everything here is read-only — a cassette is never
mutated or annotated.

This task has **two doors, not three**. Inspecting and diffing are operations on a
cassette *file*, so there is no pytest fixture surface: the fixture hands you a running
session, not a report. Reach for the CLI by default; reach for the library when you want
the result as data.

| Door | Section | Covers |
|---|---|---|
| CLI | [HT-06.1](#ht-061-with-the-cli) | `inspect` and `diff`, in full |
| library | [HT-06.2](#ht-062-with-the-library) | `Cassette.load()` and `diff_cassettes()` — no `inspect` equivalent |

## HT-06.1 With the CLI

### Read the timeline when a replay misses

```
mcp-cassette inspect demo.mcp.json --timeline
```

```
seq   t_offset_ms  dir  kind          method                   id       bytes
0               0  ->   request       initialize               1          214
1              37  <-   response      -                        1          486
2              38  ->   notification  notifications/initialized -          62
```

`dir` is `->` for client-to-server and `<-` for the other way. `id` is the recorded
JSON-RPC id (`-` when absent). `bytes` is the serialized payload length — the cheap proxy
for "this response was huge" that a summary hides. HTTP cassettes get two extra columns,
`exch` and `chan`; they are always empty for stdio, so they are omitted there.

**Verify:** the request your replay reported as unmatched either is not in the timeline, or
is there with different params.

### Grep the payloads

```
mcp-cassette inspect demo.mcp.json --timeline --grep 'tools/call'
mcp-cassette inspect demo.mcp.json --grep 'rate.?limit' --method tools/call
```

`--grep` is a Python regex matched against each message's compact JSON payload, and
composes with `--method` (both must match). An invalid regex exits `2` naming the pattern
and the `re` error.

### List the recorded tools

```
mcp-cassette inspect demo.mcp.json --tools
```

One line per tool, deduplicated by name with last-seen winning — the same rule lint's R002
uses.

### Compare two recordings

```
mcp-cassette diff old.mcp.json new.mcp.json
mcp-cassette diff old.mcp.json new.mcp.json --tools-only
```

```
metadata:
  server_info.version: 1.4.0 -> 1.5.0
methods:
  tools/call: 3 -> 4
tools:
  search: description changed (+2 -1 lines)
    --- baseline
    +++ current
sequence:
  @@ -3,4 +3,5 @@
  +tools/call
```

Exit codes: **0** identical, **5** they differ, **2** a file would not load.

### Machine-readable output

```
mcp-cassette inspect demo.mcp.json --format json > summary.json
mcp-cassette inspect demo.mcp.json --format json --timeline | jq '.timeline[-1]'
mcp-cassette diff old.mcp.json new.mcp.json --format json
```

Keys are sorted and the document is byte-stable for a given input, so it diffs cleanly as a
CI artifact.

## HT-06.2 With the library

`inspect` has **no public library equivalent** — its rendering lives in the CLI module and
is private. What is exported is the cassette model itself, which is what you would build a
report from:

```python
from collections import Counter

from mcp_cassette import Cassette

cassette = Cassette.load("demo.mcp.json")
print(cassette.transport, len(cassette.messages))
print(Counter(m.method for m in cassette.messages if m.method))
```

`diff` does have a first-class equivalent, returning the same structure the CLI renders:

```python
from mcp_cassette import diff_cassettes

result = diff_cassettes("old.mcp.json", "new.mcp.json")
if not result.identical:
    for tool in result.tools:
        print(tool)
```

`CassetteDiff` carries `metadata`, `methods`, `tools`, `sequence`, and `identical`. It is a
pydantic model, so `result.model_dump_json()` gives you the same JSON the CLI's
`--format json` prints.

**Verify:** `diff_cassettes(p, p)` on the same path returns `identical=True`.

## HT-06.3 What both doors ignore

`diff` ignores exactly what replay ignores: JSON-RPC ids, `t_offset_ms`, and `seq` are
never compared, because they are re-stamped or clock-derived and comparing them would make
every re-recording differ.

Two cassettes of the *same* server recorded from different agent runs will differ in
exchange sequence. That is a true difference, not a false positive — `--tools-only` is the
flag for "I only care whether the server's surface changed", which is the common CI use.

### `diff` versus lint's R002

They overlap deliberately and differ deliberately: **R002 is a gate** (error severity, tool
descriptions and schemas only, exit `4` for CI) while **`diff` is descriptive** (everything
that changed, no severity, exit `5` as a signal a human reads). Neither replaces the other.

### No pager, no color, no TUI

Output is plain lines on stdout, `grep`-able and `less`-able with the tools you already
have. Adding rendering machinery to a library whose entire pitch is two runtime
dependencies would be the wrong trade.

## HT-06.4 Related

- [HT-09. Gate a drifting server surface](HT-09-gate-a-drifting-server.md) — both commands
  wired into a CI gate.
- [OP-05. Runbook: replay misses and failed recordings](../operations/OP-05-runbook-replay-misses.md)
- [OP-04. CLI reference](../operations/OP-04-cli-reference.md)
