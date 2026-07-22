# 15. Runbook: replay misses and failed recordings

**Audience:** operators. The two incidents that actually happen.

## 15.1 Incident 1 — replay had unmatched requests

**Detection.** A test fails at teardown with:

```
CassetteError: replay had 2 unmatched request(s):
  - tools/call params=<digest>
  - resources/read params=<digest>
Re-record with MCP_CASSETTE_MODE=all or delete <cassette path>.
```

Out of pytest, the same condition is the replay process exiting `3`.

**Impact.** Test-only. Nothing was written; the cassette is untouched.

### 15.1.1 Diagnosis

1. Confirm what the cassette actually contains.

   ```
   uv run mcp-cassette inspect tests/cassettes/test_agent/test_agent.mcp.json
   ```

   If the missed method has count `0`, the agent is making a call that was never
   recorded — go to remediation A.

2. If the method *is* present, the mismatch is in the params or the ordering. Check
   whether anything volatile is in the request: a UUID, a timestamp, a nonce, a
   temp-directory path. Go to remediation B.

3. Check whether the agent changed its call *sequence* (a new prompt, a new tool
   selection heuristic) rather than its call *content*. Under `per_method` ordering,
   repeat calls to a method are consumed in recorded order, so a reordered pair of calls
   with different params misses. Go to remediation C.

### 15.1.2 Remediation

**A — the interaction was never recorded.** Extend the cassette instead of rebuilding it:

```
MCP_CASSETTE_MODE=new_episodes uv run pytest tests/test_agent.py::test_agent
```

Known interactions replay; misses go to the real server and are appended. Requires
credentials and network. Review the appended messages before committing.

**B — a volatile field breaks the match.** Exclude it rather than re-recording:

```python
@pytest.mark.mcp_cassette(ignore_params=["/params/arguments/requestId"])
def test_agent(mcp_cassette):
    ...
```

The pointer addresses the field inside the request object. Re-run; the miss should be
gone.

**C — the order changed.** Loosen the discipline for that test:

```python
@pytest.mark.mcp_cassette(ordering="none")
```

`none` lets any matching exchange answer, any number of times, in any order. It is the
weakest guarantee — prefer `per_method`, and use `none` only when the agent legitimately
varies its sequencing.

**Last resort — re-record.** Only when the server's behaviour genuinely changed:

> **Warning:** this overwrites the cassette. Commit or stash first; the previous
> recording is recoverable only from git.

```
rm tests/cassettes/test_agent/test_agent.mcp.json
uv run pytest tests/test_agent.py::test_agent
```

### 15.1.3 Verification

```
MCP_CASSETTE_MODE=none uv run pytest tests/test_agent.py::test_agent
```

Passes offline, with no upstream credentials in the environment.

## 15.2 Incident 2 — a recording produced nothing or died mid-run

**Detection.** One of:

```
CassetteError: recording captured zero messages — agent never spoke to the proxied
server. Is the command wired in? (cassette: <path>)
```

```
CassetteError: recording failed: <upstream error>
```

or the recording process was killed and no cassette file exists.

**Impact.** No cassette written. On a killed run, a `<cassette>.partial` sidecar may hold
everything up to the last checkpoint.

### 15.2.1 Diagnosis and remediation

1. **Zero messages.** The agent never launched the command the fixture handed it. Print
   the command in the test and confirm it reaches the agent's MCP server configuration
   verbatim:

   ```python
   cmd = mcp_cassette.server_command(["python", "tools/server.py"])
   print(cmd)   # must be the list your agent actually launches
   ```

   The first element is `sys.executable` and the command runs
   `-m mcp_cassette record ... -- <your command>`. If your agent config takes a single
   string, join it correctly; if it takes `command` plus `args`, split at index 0.

2. **Recording failed at first contact (HTTP).** The proxy could not reach the upstream
   URL. Verify the endpoint and credentials from the same machine, then re-run.

3. **Killed mid-recording.** Recover the tail from the checkpoint sidecar:

   ```
   uv run mcp-cassette inspect tests/cassettes/test_agent/test_agent.mcp.json.partial
   ```

   If the content is complete enough, promote it:

   ```
   mv tests/cassettes/test_agent/test_agent.mcp.json.partial \
      tests/cassettes/test_agent/test_agent.mcp.json
   ```

   The sidecar holds everything up to the last checkpoint (default every 5 seconds). It
   is a valid cassette. It is never written to the cassette path automatically, because
   `once` mode decides record-vs-replay by that file's existence and a truncated file
   there would silently replay as a finished recording.

4. **Unattended runs that never end.** `record` finishes on client EOF or on a signal. If
   nobody is there to interrupt it, add `--max-idle SECONDS`.

### 15.2.2 Verification

```
uv run mcp-cassette inspect <cassette>
```

Message count is non-zero and the per-method breakdown matches what the test should have
done. Then re-run the test in `none` mode; it must pass offline.

## 15.3 Escalation

If replay misses persist with no cassette diff and no code diff, or `serve` exits `2` on
a cassette that previously loaded, or you hit
`cassette format_version N is newer than supported M`, that is a library-level problem.
Upgrade mcp-cassette first; if it persists, file the cassette, the command, and the exact
error at
[github.com/cheneeheng/mcp-cassette](https://github.com/cheneeheng/mcp-cassette).
