"""Tool names are never redacted (ITER_01_v4 decision 10)."""

from __future__ import annotations

from mcp_cassette.redaction import build_redactor

NAME = "alice@example.com"


def test_tool_name_in_tools_list_result_is_left_intact() -> None:
    result = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": NAME, "description": f"mail {NAME}"}]},
    }
    scrubbed, changed = build_redactor().apply_to_payload(result, "server")
    assert isinstance(scrubbed, dict)
    tool = scrubbed["result"]["tools"][0]
    assert tool["name"] == NAME
    assert NAME not in tool["description"]  # the description is free text
    assert changed


def test_same_string_as_a_tools_call_argument_is_redacted() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": NAME, "arguments": {"to": NAME}},
    }
    scrubbed, _ = build_redactor().apply_to_payload(request, "client")
    assert isinstance(scrubbed, dict)
    assert scrubbed["params"]["name"] != NAME
    assert scrubbed["params"]["arguments"]["to"] != NAME
