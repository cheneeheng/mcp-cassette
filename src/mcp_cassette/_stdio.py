"""Unbuffered stdio byte streams for the proxy and replay server.

The stdio transport is a live, bidirectional line stream: a peer keeps the pipe open and
sends one request at a time. A *buffered* reader's ``read(n)`` blocks until ``n`` bytes
or EOF, which would stall a proxy that has only received one short line so far; a
buffered writer holds responses until its buffer fills. Both break interactive framing,
so we wrap the raw file descriptors (``buffering=0``) — each read returns whatever bytes
are available now, and each write hits the pipe immediately.
"""

from __future__ import annotations

import os
import sys

from anyio.streams.file import FileReadStream, FileWriteStream


def stdin_stream() -> FileReadStream:
    """A byte-receive stream over unbuffered stdin (single ``os.read`` per receive)."""
    raw = os.fdopen(sys.stdin.fileno(), "rb", buffering=0, closefd=False)
    return FileReadStream(raw)


def stdout_stream() -> FileWriteStream:
    """A byte-send stream over unbuffered stdout (each send flushes to the pipe)."""
    raw = os.fdopen(sys.stdout.fileno(), "wb", buffering=0, closefd=False)
    return FileWriteStream(raw)


def stderr_stream() -> FileWriteStream:
    """A byte-send stream over unbuffered stderr."""
    raw = os.fdopen(sys.stderr.fileno(), "wb", buffering=0, closefd=False)
    return FileWriteStream(raw)
