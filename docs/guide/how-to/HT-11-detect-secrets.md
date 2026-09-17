# HT-11. Detect secrets that got through

[← Guide index](../index.md)

- **When:** before you commit or merge a cassette, to catch a credential that no redaction
  rule matched.
- **Prerequisites:** a cassette. Detection reads the file and needs no server.

Lint rule **R005** walks every string value in a cassette and reports tokens that look like
encoded secrets. It is on by default at **warning** severity.

R005 is the **last line, not the first**. Structural rules ([HT-07](HT-07-redact-secrets.md))
and redaction packs ([HT-10](HT-10-redact-pii.md)) run at record time and stop a secret
reaching disk. R005 only tells you one already did. Its value is the case both miss: a
generic vendor token under a key no rule names, or inside a line recorded as `raw` or
`unclassified` that structural redaction cannot parse.

## HT-11.1 Run it

```
mcp-cassette lint demo.mcp.json                      # R005 on, warnings only
mcp-cassette lint demo.mcp.json --fail-on warning    # make warnings gate
mcp-cassette lint demo.mcp.json --no-entropy         # skip R005 entirely
```

A finding names the JSON pointer, the entropy, the length, and at most six characters of
the token. A linter that echoed the whole secret into a CI log would move the leak rather
than report it.

```
R005 warning /messages/12/payload/result/content/0/text high-entropy string (4.6 bits/char, 44 chars, "sk_liv…")
```

**Verify:** plant a random 40-character token in a tool result of a scratch cassette and
lint it. Expect one R005 line at that pointer and exit `0`; with `--fail-on warning`,
exit `4`.

## HT-11.2 What counts as a candidate

A string is split on whitespace and punctuation. A token becomes a candidate only when
all of these hold:

1. It is at least `min_length` characters (default `20`).
2. It uses an encoding charset (`A-Za-z0-9+_-`) and contains a digit or mixed case, so a
   long plain word never counts.
3. It is not on the allowlist.
4. It does not match a **shape exclusion**: a UUID, a 40- or 64-character hex digest (git
   object ids, sha256), an ISO-8601 timestamp, or a plain integer.
5. Its Shannon entropy is at least `min_bits` per character (default `3.5`).

Redaction markers (`REDACTED`, `<LABEL>_<12 hex>` pseudonyms, `•` mask runs) are stripped
first, so a scrubbed value never fires. `data:` URIs and strings that are themselves JSON
documents are skipped. At most 200 candidates are scanned per message; past that, lint
prints a note naming the message so you can review the rest by hand.

## HT-11.3 Why warning, and how to escalate

Raw entropy flags UUIDs, digests, and encoded images, all common in MCP traffic and none of
them secret. The shape exclusions remove the usual suspects, but a false-positive rate on
real cassettes has not been measured, so R005 does not fail a build by default. Escalate
per project once you trust it:

```toml
# pyproject.toml
[tool.mcp_cassette.lint]
fail_on = "warning"

[tool.mcp_cassette.lint.entropy]
min_bits = 4.0
min_length = 24
allowlist = ["AKIAIOSFODNN7EXAMPLE"]   # exact tokens, never regexes
```

The allowlist compares whole tokens by equality. A regex allowlist that over-matched would
silently disable a security rule. On the CLI, `--entropy-min-bits`, `--entropy-min-length`,
and `--entropy-allow` (repeatable, and replacing the config's allowlist) override these.

## HT-11.4 R005 and R006 are different questions

R005 is statistical and reads **any string**. R006 is pattern-based and reads **tool
definition text**: names, `inputSchema` property descriptions, and enum values. A secret in
a schema description is R005's; injection phrasing in the same description is R006's. See
[HT-08.4](HT-08-lint-pattern-packs.md#ht-084-behaviour-shared-by-both-doors).

## HT-11.5 Related

- [HT-07. Redact secrets](HT-07-redact-secrets.md) — named fields, at record time.
- [HT-10. Redact PII from free text](HT-10-redact-pii.md) — free text, at record time.
- [OP-04.6 `lint`](../operations/OP-04-cli-reference.md#op-046-lint) — every flag and rule.

---

[← HT-10 Redact PII from free text](HT-10-redact-pii.md) · [Guide index](../index.md)
