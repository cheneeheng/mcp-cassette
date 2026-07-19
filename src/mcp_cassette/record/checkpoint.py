"""Periodic crash-safety checkpoints for an in-progress recording.

A recording accumulates in memory and is written once, on shutdown (see
:mod:`mcp_cassette.record.proxy`). A hard kill therefore loses the whole session, not
just its tail. The checkpoint loop bounds that loss: every ``interval`` seconds, if new
messages arrived, the session so far is written to a **sidecar** ``<cassette>.partial``.

The sidecar path is the point. Writing checkpoints to the cassette path itself would put
a truncated-but-valid cassette where ``mode="once"`` looks for a finished one (it picks
record-vs-replay by file existence), so a crashed recording would silently replay as if
complete. A ``.partial`` file is inert to every mode: it is recoverable by hand (it is a
valid cassette — ``inspect`` reads it, ``mv`` promotes it) and is removed on the normal
finalize path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import anyio

from ..cassette import Cassette

DEFAULT_CHECKPOINT_INTERVAL = 5.0
"""Seconds between checkpoints when the caller does not choose an interval."""


def partial_path(cassette_path: str | os.PathLike[str]) -> Path:
    """Return the sidecar path checkpoints are written to.

    Args:
        cassette_path: The cassette the recording will finalize into.

    Returns:
        The ``<cassette>.partial`` sibling path.
    """
    target = Path(cassette_path)
    return target.with_name(target.name + ".partial")


def discard(cassette_path: str | os.PathLike[str]) -> None:
    """Remove the checkpoint sidecar, if one exists.

    Called after a successful finalize: the real cassette now holds everything the
    sidecar did.

    Args:
        cassette_path: The cassette that was finalized.
    """
    partial_path(cassette_path).unlink(missing_ok=True)


async def run(
    interval: float | None,
    snapshot: Callable[[], Cassette | None],
    cassette_path: str | os.PathLike[str],
) -> None:
    """Checkpoint the session to its sidecar every ``interval`` seconds until cancelled.

    A checkpoint is skipped when ``snapshot`` declines (returns ``None``) or when no new
    message arrived since the last write, so an idle recording does no disk I/O. The
    write itself runs in a worker thread — uncancellable there, which is what keeps a
    cancel from tearing a checkpoint in half, and it keeps serialization off the event
    loop so the pumps do not stall.

    Args:
        interval: Seconds between checkpoints. ``None`` or non-positive returns
            immediately, so callers can start the task unconditionally.
        snapshot: Builds the session so far, or returns ``None`` to skip this round
            (e.g. nothing recorded yet, or the upstream failed at first contact and no
            cassette file should exist).
        cassette_path: The cassette this recording will finalize into; the sidecar is
            derived from it.
    """
    if interval is None or interval <= 0:
        return
    target = partial_path(cassette_path)
    written = 0
    while True:
        await anyio.sleep(interval)
        cassette = snapshot()
        if cassette is None or len(cassette.messages) == written:
            continue
        written = len(cassette.messages)
        await anyio.to_thread.run_sync(cassette.save, target)
