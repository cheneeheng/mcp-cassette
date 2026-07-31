# HT-07. Redact secrets from cassettes

**When:** before you commit a cassette recorded against a real server. Cassettes are
verbatim transcripts; anything the server said is in the file.
**Prerequisites:** a recording run. Redaction happens at capture time and cannot be applied
retroactively.

Redaction is applied to a **deep copy** at capture time. The bytes in flight are never
altered, so the agent under test sees the real values while the cassette gets the scrubbed
ones. Each affected message is flagged with `"redacted": true`.

**Defaults are always on, through every door.** These key-globs are matched
case-insensitively against every dict key at any depth, and the matching value is replaced
with `REDACTED`:

```
*token*   *secret*   *password*   *apikey*   *api_key*   authorization
```

Adding your *own* rules has **two doors, not three**. The pytest fixture and
`use_cassette` do not plumb redaction through — see
[HT-07.3](#ht-073-the-gap-in-the-fixture-and-use_cassette) before you plan around it.

| Door | Section | Covers |
|---|---|---|
| CLI | [HT-07.1](#ht-071-with-the-cli) | `--redact`, `--no-default-redactions` |
| library | [HT-07.2](#ht-072-with-the-library) | `StdioRecordingProxy(redaction=[...])` |

## HT-07.1 With the CLI

A rule locator is either a **key-glob** (anything not starting with `/`) or a **JSON
pointer** (starting with `/`) addressing exactly one location. Repeat `--redact` for each
rule; append `=REPLACEMENT` to override the default `REDACTED`.

1. Add your rules to the recording command:

   ```
   mcp-cassette record --cassette demo.mcp.json \
     --redact "*email*" \
     --redact "/result/content/0/text=<scrubbed body>" \
     -- python tools/server.py
   ```

2. Run the agent against it to capture the session.

3. Search the cassette for the sensitive value:

   ```
   grep -c "ghp_" demo.mcp.json
   ```

**Verify:** expected output is `0`, and the message that carried the value now has
`"redacted": true`.

To turn the defaults off — only with a reason:

```
mcp-cassette record --cassette demo.mcp.json --no-default-redactions -- python tools/server.py
```

## HT-07.2 With the library

The recording proxy is exported, and takes the same two knobs as the CLI. Rules are
`RedactionRule` objects:

```python
from mcp_cassette import RedactionRule, StdioRecordingProxy

proxy = StdioRecordingProxy(
    server_cmd=["python", "tools/server.py"],
    cassette_path="demo.mcp.json",
    redaction=[
        RedactionRule(locator="*email*"),
        RedactionRule(locator="/result/content/0/text", replacement="<scrubbed>"),
    ],
    include_default_redactions=True,
)
exit_code = proxy.run()
```

> **Note:** `run()` is the *whole process*, not a helper you call inside a test. Like
> `mcp-cassette record`, the proxy forwards its own stdin to the wrapped server, so it does
> nothing until a client drives it. This is the API the CLI itself calls — use it when you
> are building your own recording entry point, not to record from inside a test body.

The Streamable HTTP twin takes the identical `redaction` and `include_default_redactions`
arguments:

```python
from mcp_cassette.transports.http import RecordingProxy
```

**Verify:** same as the CLI — grep the written cassette for the secret and expect no hits.

## HT-07.3 The gap in the fixture and `use_cassette`

Neither the `mcp_cassette` fixture nor `use_cassette` accepts redaction rules. Sessions
opened through those two doors record with **the default rule set only**.

That is safe by default — the defaults are never off unless you turn them off — but it
means a project-specific rule (`*email*`, a pointer into a known response body) cannot be
expressed from a pytest suite today. If you need one, your options are:

1. Record that cassette once through the CLI with `--redact`, commit it, and let the suite
   replay it. Recording is a first-run activity, so this costs you nothing per test run.
2. Check the recording before committing, and treat lint as the backstop
   ([HT-08](HT-08-lint-pattern-packs.md)).

## HT-07.4 Limits you must know about

- Redaction is **structural** — it needs JSON keys. A message captured as `raw` (a line
  that did not parse as JSON) is stored unchanged and never redacted.
- A secret embedded inside a value whose key does not match any rule survives. A token
  pasted into `/result/content/0/text` is only removed by a pointer rule aimed at it.
- The recording proxy forwards the real server's stderr to your stderr and does not capture
  it, so nothing the server logs reaches the cassette.
- Over HTTP, request headers (including `Authorization`) are forwarded upstream but never
  written to the cassette at all.

> **Warning:** treat "no rule matched" as "not checked", not "clean". Read a new cassette
> before its first commit. Once it is pushed, rotating the leaked credential is the only
> real remedy.

## HT-07.5 Related

Redaction protects *your* secrets. Linting checks the *other* direction — whether recorded
tool descriptions and results carry prompt-injection smells before they reach a model.
Redaction hides **values** at record time; a pattern pack detects **phrasing** at lint
time. Different jobs.

- [HT-08. Lint with your own pattern packs](HT-08-lint-pattern-packs.md)
- [OP-03. CI pipeline](../operations/OP-03-ci.md#op-033-lint-cassettes-before-they-reach-a-model)
