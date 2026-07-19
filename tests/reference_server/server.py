"""Minimal reference MCP server built on the official SDK (dev dependency).

Exposes two tools (``echo``, ``add``), one resource, and a tool that emits a server
notification. Recorded against by the integration tests. Not part of the shipped
package.

Run directly over stdio::

    python tests/reference_server/server.py [--noisy-stdout]

``--noisy-stdout`` prints one non-JSON line to stdout at startup so the recorder's
``kind="raw"`` handling can be exercised.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent
from pydantic import BaseModel

mcp = FastMCP("reference-server")


class Answer(BaseModel):
    """Elicitation response schema for ``ask_user``."""

    answer: str


@mcp.tool()
def echo(text: str) -> str:
    """Return the given text unchanged."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


_counter = {"n": 0}


@mcp.tool()
def counter() -> int:
    """Return a monotonically increasing count (stateful within one process).

    Lets tests observe per-method queue consumption: two identically-shaped calls
    record two distinct responses, and replay must return them in order.
    """
    _counter["n"] += 1
    return _counter["n"]


@mcp.tool()
async def notify(ctx: Context) -> str:  # type: ignore[type-arg]
    """Emit a server log notification, then return."""
    await ctx.info("reference server notification")
    return "notified"


@mcp.tool()
async def summarize(text: str, ctx: Context) -> str:  # type: ignore[type-arg]
    """Ask the client to sample a summary mid-call (server-initiated request)."""
    result = await ctx.session.create_message(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text=f"Summarize: {text}"),
            )
        ],
        max_tokens=64,
    )
    content = result.content
    answer = content.text if isinstance(content, TextContent) else str(content)
    return f"summary: {answer}"


@mcp.tool()
async def ask_user(question: str, ctx: Context) -> str:  # type: ignore[type-arg]
    """Elicit a structured answer from the user mid-call."""
    result = await ctx.elicit(message=question, schema=Answer)
    data = getattr(result, "data", None)
    if result.action == "accept" and data is not None:
        return f"user said: {data.answer}"
    return f"user action: {result.action}"


@mcp.resource("ref://greeting")
def greeting() -> str:
    """A static greeting resource."""
    return "hello from the reference server"


def main() -> None:
    """Run the reference server over stdio."""
    if "--noisy-stdout" in sys.argv:
        # Misbehaving servers log to stdout in the wild; the recorder keeps this as a
        # kind="raw" message rather than treating it as fatal.
        sys.stdout.write("this line is not JSON-RPC\n")
        sys.stdout.flush()
    mcp.run()


if __name__ == "__main__":
    main()
