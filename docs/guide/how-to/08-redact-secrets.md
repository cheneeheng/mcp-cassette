# 8. Redact secrets from cassettes

**When:** before you commit a cassette recorded against a real server. Cassettes are
verbatim transcripts; anything the server said is in the file.
**Prerequisites:** a recording run (redaction happens at capture time — it cannot be
applied retroactively by the library).

Redaction is applied to a **deep copy** at capture time. The bytes in flight are never
altered, so the agent under test sees the real values while the cassette gets the
scrubbed ones. Each affected message is flagged with `"redacted": true`.

## 8.1 Defaults are always on

These key-globs are matched case-insensitively against every dict key at any depth, and
the matching value is replaced with `REDACTED`:

```
*token*   *secret*   *password*   *apikey*   *api_key*   authorization
```

Disable them only if you have a reason:

```
mcp-cassette record --cassette demo.json --no-default-redactions -- python tools/server.py
```

## 8.2 Add your own rules

A rule locator is either a **key-glob** (anything not starting with `/`) or a **JSON
pointer** (starting with `/`) addressing exactly one location. Repeat `--redact` for
each rule; append `=REPLACEMENT` to override the default `REDACTED`.

```
mcp-cassette record --cassette demo.json \
  --redact "*email*" \
  --redact "/result/content/0/text=<scrubbed body>" \
  -- python tools/server.py
```

**Verify:** open the cassette and search for the sensitive value. It should not appear,
and the containing message should carry `"redacted": true`.

```
grep -c "ghp_" tests/cassettes/test_agent/test_agent.mcp.json
```

Expected output: `0`.

In code, the same rules are `RedactionRule` objects:

```python
import mcp_cassette as mcc

rules = [
    mcc.RedactionRule(locator="*email*"),
    mcc.RedactionRule(locator="/result/content/0/text", replacement="<scrubbed>"),
]
```

## 8.3 Limits you must know about

- Redaction is **structural** — it needs JSON keys. A message captured as `raw` (a line
  that did not parse as JSON) is stored unchanged and never redacted.
- A secret embedded inside a value whose key does not match any rule survives. A token
  pasted into `/result/content/0/text` is only removed by a pointer rule aimed at it.
- The recording proxy forwards the real server's stderr to your stderr and does not
  capture it, so nothing the server logs reaches the cassette.
- Over HTTP, request headers (including `Authorization`) are forwarded upstream but never
  written to the cassette at all.

> **Warning:** treat "no rule matched" as "not checked", not "clean". Read a new cassette
> before its first commit. Once it is pushed, rotating the leaked credential is the only
> real remedy.

## 8.4 Then lint it

Redaction protects *your* secrets. Linting checks the *other* direction — whether the
recorded tool descriptions and results carry prompt-injection smells before they reach a
model. See [13. CI pipeline](../operations/13-ci.md#133-lint-cassettes-before-they-reach-a-model).

Redaction and pattern packs are often confused: redaction hides **values** at record
time, while a pack detects **phrasing** at lint time. Different jobs — see
[9. Lint with your own pattern packs](09-lint-pattern-packs.md).
