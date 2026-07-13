"""Bidirectional newline-delimited line pumping over anyio byte streams.

Framing is newline-delimited JSON-RPC per the MCP stdio transport spec. The pump moves
one line at a time and offers an optional ``tap`` so a recorder can observe traffic
without altering bytes in flight.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from anyio.abc import ByteReceiveStream, ByteSendStream

Tap = Callable[[bytes], None]


async def buffered_lines(stream: ByteReceiveStream) -> AsyncIterator[bytes]:
    """Yield complete newline-terminated lines (including the trailing ``\\n``).

    Buffers partial reads until a newline is seen. A final unterminated fragment at EOF
    is yielded as a complete line (some servers omit the last newline).

    Args:
        stream: The byte stream to read from.

    Yields:
        Each line as ``bytes``, newline included where present.
    """
    buffer = b""
    async for chunk in _iter_chunks(stream):
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line + b"\n"
    if buffer:
        yield buffer


async def _iter_chunks(stream: ByteReceiveStream) -> AsyncIterator[bytes]:
    from anyio import EndOfStream

    while True:
        try:
            chunk = await stream.receive()
        except EndOfStream:
            return
        if not chunk:
            return
        yield chunk


async def pump_lines(
    receive_stream: ByteReceiveStream,
    send_stream: ByteSendStream,
    tap: Tap | None = None,
) -> None:
    """Forward newline-delimited lines from ``receive_stream`` to ``send_stream``.

    Args:
        receive_stream: Source byte stream.
        send_stream: Destination byte stream.
        tap: Optional observer called with each line before it is forwarded. The tap
            must not mutate or block on the bytes.
    """
    async for line in buffered_lines(receive_stream):
        if tap is not None:
            tap(line)
        await send_stream.send(line)
