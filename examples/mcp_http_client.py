"""A minimal Streamable HTTP JSON-RPC client used by the HTTP examples.

POSTs each message to an ``/mcp`` endpoint (the real server, mcp-cassette's recording
proxy, or its replay server — interchangeable, which is the point) and collects the
JSON objects that come back. It tracks the ``Mcp-Session-Id`` the server issues on
``initialize`` and echoes it on every later request, as the Streamable HTTP spec
requires. Pure standard library; JSON response mode only — plenty for these examples,
whose server never streams SSE.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any


def run(url: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """POST every message in order and return the JSON objects the server sent.

    Args:
        url: The ``/mcp`` endpoint to talk to.
        messages: JSON-RPC objects to send, in order.

    Returns:
        The decoded response objects, in arrival order (notifications and client
        responses produce a bodyless ``202`` and contribute nothing).
    """
    objects: list[dict[str, Any]] = []
    session_id: str | None = None
    for message in messages:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id is not None:
            headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(
            url, data=json.dumps(message).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            session_id = response.headers.get("mcp-session-id") or session_id
            body = response.read()
        if body.strip():
            objects.append(json.loads(body))
    return objects
