# 7. Inspect and diff cassettes

**When:** a replay missed and you need to see what was actually recorded, or you
re-recorded after a server upgrade and need the delta.
**Prerequisites:** a cassette file. Both commands are read-only — a cassette is never
mutated or annotated.

## 7.1 Read the timeline when a replay misses

```bash
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

**Verify:** the request your replay reported as unmatched either is not in the timeline,
or is there with different params.

## 7.2 Grep the payloads

```bash
mcp-cassette inspect demo.mcp.json --timeline --grep 'tools/call'
mcp-cassette inspect demo.mcp.json --grep 'rate.?limit' --method tools/call
```

`--grep` is a Python regex matched against each message's compact JSON payload, and
composes with `--method` (both must match). An invalid regex exits 2 naming the pattern
and the `re` error.

## 7.3 List the recorded tools

```bash
mcp-cassette inspect demo.mcp.json --tools
```

One line per tool, deduplicated by name with last-seen winning — the same rule lint's
R002 uses.

## 7.4 Machine-readable output

```bash
mcp-cassette inspect demo.mcp.json --format json > summary.json
mcp-cassette inspect demo.mcp.json --format json --timeline | jq '.timeline[-1]'
```

Keys are sorted and the document is byte-stable for a given input, so it diffs cleanly as
a CI artifact.

## 7.5 Compare two recordings

```bash
mcp-cassette diff old.mcp.json new.mcp.json
mcp-cassette diff old.mcp.json new.mcp.json --tools-only
mcp-cassette diff old.mcp.json new.mcp.json --format json
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

`diff` ignores what replay ignores: JSON-RPC ids, `t_offset_ms`, and `seq` are never
compared, because they are re-stamped or clock-derived and comparing them would make
every re-recording differ.

Two cassettes of the *same* server recorded from different agent runs will differ in
exchange sequence. That is a true difference, not a false positive — `--tools-only` is
the flag for "I only care whether the server's surface changed", which is the common CI
use.

## 7.6 `diff` versus lint's R002

They overlap deliberately and differ deliberately: **R002 is a gate** (error severity,
tool descriptions and schemas only, exit 4 for CI) while **`diff` is descriptive**
(everything that changed, no severity, exit 5 as a signal a human reads). Neither
replaces the other.

## 7.7 No pager, no color, no TUI

Output is plain lines on stdout, `grep`-able and `less`-able with the tools you already
have. Adding rendering machinery to a library whose entire pitch is two runtime
dependencies would be the wrong trade.

## 7.8 Related

- [15. Runbook: replay misses and failed recordings](../operations/15-runbook-replay-misses.md)
- [14. CLI reference](../operations/14-cli-reference.md)
