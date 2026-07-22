# 5. Inject faults

**When:** you want to test how your agent behaves when the MCP server is slow, errors,
returns garbage, or dies — without breaking a real server.
**Prerequisites:** a recorded cassette for the test. Faults are **replay-only**.

One recorded cassette drives a whole resilience matrix. The cassette is never mutated:
faults live in a separate `FaultOverlay`, either built in test code or loaded from a
`<cassette>.faults.json` sidecar.

## 5.1 Parametrize over faults

```python
import mcp_cassette as mcc
import pytest

@pytest.mark.parametrize("fault", [
    mcc.Fault.timeout("tools/call", nth=1),
    mcc.Fault.error("tools/call", code=-32000, message="rate limited"),
    mcc.Fault.disconnect("tools/call"),
])
def test_agent_survives_tool_trouble(mcp_cassette, fault):
    session = mcp_cassette.with_faults(fault)
    cmd = session.server_command(["python", "tools/github_server.py"])
    result = run_my_agent(mcp_servers={"github": cmd})
    assert result.completed_with_degraded_tools
```

`with_faults()` returns a **new** `CassetteSession`, so parametrized tests never share
state. Pass several faults in one call to combine them.

**Verify:** the agent takes its degraded path and the test still passes offline.

## 5.2 Fault types

| Constructor | Effect |
|---|---|
| `Fault.delay(method, ms, nth=None)` | Sleep `ms` milliseconds, then respond normally. |
| `Fault.timeout(method, nth=None)` | Never respond to the matched request; keep serving others. |
| `Fault.error(method, code=-32603, message="mcp-cassette injected error", nth=None)` | Replace the recorded response with a JSON-RPC error object. |
| `Fault.malformed(method, strategy="truncate", nth=None)` | Emit a corrupted response line. `strategy` is `truncate`, `not_json`, or `wrong_id`. |
| `Fault.disconnect(method, after_response=False, nth=None)` | Close the pipes and exit, simulating server death. |

`method` is the JSON-RPC method the fault targets, e.g. `tools/call`. `nth` restricts the
fault to the nth matching request; omit it to apply to every match.

## 5.3 The one rule that trips people up

**Faults fire after a request matches.** A fault on `tools/call` does nothing unless the
cassette contains a matching `tools/call` exchange for that request. A fault targeting a
method the cassette never recorded is silently inert — check for it with:

```
mcp-cassette inspect demo.json --faults demo.faults.json
```

Expected output:

```
fault overlay dry-run:
  seq 4 tools/call -> error
  WARNING: timeout on resources/read matches nothing
```

The `WARNING` lines are exactly the inert faults.

## 5.4 Faults under a recording mode

`with_faults()` combined with a mode that resolves to recording raises:

```
CassetteError: faults apply to replay only; with_faults cannot run under a recording
mode (resolved action: record)
```

Record the cassette first (or stop forcing `MCP_CASSETTE_MODE=all` for that run), then
add the fault.

## 5.5 From the CLI

Write the overlay to a JSON sidecar and pass it to `serve`:

```json
{
  "faults": [
    {
      "target": { "method": "tools/call", "nth": 1 },
      "type": "error",
      "params": { "code": -32000, "message": "rate limited" }
    }
  ]
}
```

```
mcp-cassette serve demo.json --faults demo.faults.json
```
