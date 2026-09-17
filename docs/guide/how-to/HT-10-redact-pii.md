# HT-10. Redact PII from free text

[← Guide index](../index.md)

- **When:** the server you record puts personal data or credentials *inside text* — an email
  in a search result, a phone number in a ticket body, a customer address in a tool argument.
- **Prerequisites:** a recording run. Like every redaction, this happens at capture time.

[HT-07](HT-07-redact-secrets.md) structural rules hide **named fields**: they need a JSON key
such as `authorization`. A value inside a sentence has no key of its own, so structural
rules never see it. A **redaction pack** hides **free text**: labelled regexes scanned over
every string value in the session. The two run together, structural first, and a value the
structural rules already replaced is never re-scanned.

**A bundled pack is on by default.** `common` covers email, E.164 phone numbers, IPv4,
IPv6, US SSNs, IBANs (checked with mod-97), payment card numbers (checked with Luhn), AWS
access key ids, PEM private-key headers, and JWTs. `--no-default-redactions` turns it off
along with the structural defaults. Your own packs are added beside it.

Every recording made this way carries a **redaction manifest** in the cassette: the profile
name, the backends that ran, and each pack's id, path, and sha256. It never records a found
value or the salt. The manifest is what [HT-10.5](#ht-105-replay-re-applies-the-same-scrubbing)
replays against and what `lint --require-redaction` gates on.

| Door | Section | Covers |
|---|---|---|
| CLI | [HT-10.2](#ht-102-with-the-cli) | `record --pii-pack`, `--redact-profile`, `--redact-salt-env`; `serve --pii-pack` |
| pytest fixture | [HT-10.3](#ht-103-with-the-fixture-and-the-library) | `@pytest.mark.mcp_cassette(pii_packs=[...])` |
| library, sync and async | [HT-10.3](#ht-103-with-the-fixture-and-the-library) | `use_cassette(..., pii_packs=[...])`, `use_cassette_async(..., pii_packs=[...])` |

## HT-10.1 Write the pack

`examples/pii-pack.toml` is a runnable starter. The format, field by field:

```toml
version = 1                        # pack format version; only 1 is accepted
id = "team"                        # names the pack in the manifest and in warnings

[[rules]]
id = "employee-id"                 # unique within the pack; qualified as team/employee-id
label = "EMPLOYEE"                 # names the value kind in the output
regex = '\bEMP-\d{6}\b'
flags = ["i"]                      # subset of i, m, s, x
strategy = "hash"                  # replace | hash | mask; default depends on direction
direction = "both"                 # both (default) | server
validator = "none"                 # none (default) | luhn | mod97
```

| Field | Meaning |
|---|---|
| `id` | 1–32 characters, `[A-Za-z][A-Za-z0-9_-]*`, unique within the pack. |
| `label` | Appears in `replace` and `hash` output, e.g. `<EMPLOYEE>`. |
| `regex` | Compiled, never evaluated as code. No code is imported from a pack. |
| `strategy` | See [HT-10.4](#ht-104-pick-a-strategy). Unset means `hash` for a `both` rule and `replace` for a `server` rule. |
| `replacement` | Literal text for `replace`; defaults to `<LABEL>`. |
| `direction` | `both` scrubs client requests and server messages, and is re-applied to live requests at replay. `server` scrubs only what the server sent. |
| `validator` | A checksum a match must pass. A card-shaped number that fails Luhn is left alone. |

A bad version, an unknown key, a duplicate or malformed id, an unknown flag letter, or a
regex that does not compile each exits `2`, naming the pack file and the key.

## HT-10.2 With the CLI

1. Record with your pack and a profile name:

   ```
   mcp-cassette record --cassette demo.mcp.json \
     --pii-pack examples/pii-pack.toml \
     --redact-profile team-baseline \
     -- python tools/server.py
   ```

2. Drive the session with your agent, then search the cassette for a planted value:

   ```
   grep -c "alice@example.com" demo.mcp.json
   ```

3. Replay it. The pack is found again from the manifest (see HT-10.5), so `--pii-pack` is
   needed only when the recorded path is gone:

   ```
   mcp-cassette serve demo.mcp.json --pii-pack examples/pii-pack.toml
   ```

**Verify:** step 2 prints `0`, and `mcp-cassette lint demo.mcp.json --require-redaction
team-baseline` exits `0`. The same command on a cassette recorded without the profile exits
`4` and says what to run:

```
mcp-cassette lint: examples/cassettes/tools.mcp.json carries no redaction manifest; re-record it with 'mcp-cassette record --redact-profile team-baseline --cassette examples/cassettes/tools.mcp.json -- CMD'
```

## HT-10.3 With the fixture and the library

Every programmatic door takes the same `pii_packs` list. It applies the packs when the
session records and resolves the manifest's packs when it replays.

```python
@pytest.mark.mcp_cassette(pii_packs=["tests/redaction/team.toml"])
def test_agent_reads_tickets(mcp_cassette):
    cmd = mcp_cassette.server_command(["python", "tools/tickets.py"])
    ...
```

```python
from mcp_cassette import use_cassette, use_cassette_async

with use_cassette("cassettes/tickets.mcp.json", pii_packs=["team.toml"]) as session:
    cmd = session.server_command(["python", "tools/tickets.py"])

async with use_cassette_async("cassettes/remote.mcp.json", pii_packs=["team.toml"]) as s:
    url = s.server_url("https://mcp.example.com/mcp")
```

`--redact-profile` and `--redact-salt-env` are CLI-only. A cassette recorded through these
doors carries a manifest with no profile, so gate those cassettes with a lint run that does
not require one, or record the committed copy through the CLI.

**Verify:** the same grep as HT-10.2 finds no planted value.

## HT-10.4 Pick a strategy

| `strategy` | Output for a match | Use it for |
|---|---|---|
| `replace` | `replacement`, or `<LABEL>` | maximum scrub, when every match may collapse to one value |
| `hash` | `<LABEL>_<12 hex>`, an HMAC of the matched text | pseudonyms: stable, distinguishable, not reversible |
| `mask` | all but the last four characters as `•` | human-readable evidence, e.g. `•••••••6411` |

**`replace` with `direction = "both"` is a matching hazard.** Two different emails in two
`tools/call` requests both become `<EMAIL>`, the recorded requests become identical, and
replay can hand back the wrong recorded response. That is why a `both` rule defaults to
`hash`. A pack that sets `replace` on a `both` rule still records, but warns:

```
mcp-cassette: redaction rule team/email uses strategy 'replace' with direction 'both': distinct values in client requests collapse to one replacement, so replay can hand back the wrong recorded response. Fix: set strategy = "hash", or narrow to direction = "server".
```

## HT-10.5 Replay re-applies the same scrubbing

The cassette holds the *scrubbed* request, but your agent sends the *real* one at replay.
For every `both` rule, the replay server scrubs each incoming request with the same pack
before matching. Under the default salt, the live email and the recorded pseudonym land on
the same value, so the match succeeds.

Replay finds each pack by its **sha256**, trying in order: the packs you pass
(`--pii-pack` or `pii_packs=`), the path recorded in the manifest, then the bundled packs.
A moved pack still resolves, and an *edited* pack deliberately does not. When nothing
matches, `serve` exits `2` and the other doors raise `CassetteError`:

```
redaction pack 'team' (sha256 …) named by the cassette's redaction manifest was not found among the supplied packs, at its recorded path '…', or in the bundled packs. Fixes: supply it with --pii-pack PATH (pii_packs= from Python), or re-record the cassette.
```

A pack you pass that the manifest does not name is accepted and ignored with a note, so one
command can serve several cassettes. `serve --pii-pack` on a cassette with no manifest at
all exits `2`, because there is nothing to resolve it against.

## HT-10.6 The salt, and what a pseudonym does not protect

`hash` pseudonyms are keyed by a salt, in one of two modes:

| Mode | Flag | Pseudonyms | Cost |
|---|---|---|---|
| `stable` (default) | — | identical on every machine and every re-record | a low-entropy value can be recovered by hashing guesses |
| `env` | `record --redact-salt-env` | keyed by `MCP_CASSETTE_REDACT_SALT` | re-records produce different bytes, so `diff` and lint `R002` see noise |

The default chooses diffable cassettes. A stable pseudonym hides an email from a casual
reader of the repository. It does **not** hide it from someone who guesses the email,
because they can compute the same pseudonym. When that matters, record with
`--redact-salt-env` and keep the variable secret. Without the variable set, `record` exits
`2` naming it.

## HT-10.7 Limits you must know about

- Packs are regexes. A format no rule describes survives, exactly as with lint. Read a new
  cassette before its first commit.
- Tool **names** in a `tools/list` result are never redacted, because a name is a protocol
  identifier and rewriting it would break the agent. The same string as a `tools/call`
  argument is scanned like any other value.
- Replay scrubs requests only for `both` rules. A `server` rule never touches what the agent
  sends.
- `record --pii-pack` with `--no-default-redactions` is accepted: the flag drops the
  bundled rules, not packs you name.

> **Warning:** redaction is the first line and [HT-11](HT-11-detect-secrets.md) is the last.
> R005 tells you a secret got through; only a rule stops it reaching disk.

## HT-10.8 Related

- [HT-07. Redact secrets](HT-07-redact-secrets.md) — structural rules for named fields.
- [HT-11. Detect secrets that got through](HT-11-detect-secrets.md) — entropy, at lint time.
- [OP-04. CLI reference](../operations/OP-04-cli-reference.md#op-042-record) — every flag
  and the combination table.
- [OP-07. Pre-commit hooks](../operations/OP-07-pre-commit.md) — refuse an unscrubbed
  cassette before it is committed.

---

[← HT-09 Gate a drifting server surface](HT-09-gate-a-drifting-server.md) · [Guide index](../index.md) · [HT-11 Detect secrets that got through →](HT-11-detect-secrets.md)
