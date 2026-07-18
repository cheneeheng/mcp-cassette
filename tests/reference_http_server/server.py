"""Reference MCP server over Streamable HTTP (dev-dep SDK; not shipped).

The same echo/add/counter/notify surface as the stdio reference server, exposed over
Streamable HTTP, plus: ``broadcast`` (sends an unrelated notification, which the SDK
routes to the GET listening stream), ``summarize`` (issues a ``sampling/createMessage``
request mid-call), and ``ask_user`` (elicitation). Recorded against by the
integration tests. Not part of the shipped package.

Run directly::

    python tests/reference_http_server/server.py --port 8931 [--json-response]

``--json-response`` switches the SDK's response mode from SSE streams to single JSON
bodies, so both Streamable HTTP response modes can be exercised.
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent
from pydantic import BaseModel


class Answer(BaseModel):
    """Elicitation response schema for ``ask_user``."""

    answer: str


def build_server(port: int, json_response: bool) -> FastMCP:
    """Construct the reference server bound to ``127.0.0.1:port``."""
    mcp = FastMCP(
        "reference-http-server",
        host="127.0.0.1",
        port=port,
        json_response=json_response,
        log_level="WARNING",
    )

    @mcp.tool()
    def echo(text: str) -> str:
        """Return the given text unchanged."""
        return text

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Return the sum of two integers."""
        return a + b

    counter_state = {"n": 0}

    @mcp.tool()
    def counter() -> int:
        """Return a monotonically increasing count (stateful within one process)."""
        counter_state["n"] += 1
        return counter_state["n"]

    @mcp.tool()
    async def notify(ctx: Context) -> str:  # type: ignore[type-arg]
        """Emit a request-related log notification (rides the POST stream)."""
        await ctx.info("reference server notification")
        return "notified"

    @mcp.tool()
    async def broadcast(ctx: Context) -> str:  # type: ignore[type-arg]
        """Emit an unrelated notification (the SDK routes it to the GET stream)."""
        await ctx.session.send_tool_list_changed()
        return "broadcast"

    @mcp.tool()
    async def summarize(text: str, ctx: Context) -> str:  # type: ignore[type-arg]
        """Ask the client to sample a summary mid-call (server-initiated request)."""
        # related_request_id routes the request onto the triggering POST's SSE
        # stream (the spec's related-stream mode); without it the SDK sends it to
        # the standalone GET stream, which the recording client may not hold open.
        result = await ctx.session.create_message(
            messages=[
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=f"Summarize: {text}"),
                )
            ],
            max_tokens=64,
            related_request_id=ctx.request_id,
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

    return mcp


def main() -> None:
    """Run the reference server over Streamable HTTP."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--json-response", action="store_true")
    args = parser.parse_args()
    build_server(args.port, args.json_response).run(transport="streamable-http")


if __name__ == "__main__":
    main()
