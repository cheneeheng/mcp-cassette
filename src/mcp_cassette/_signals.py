"""Cross-platform interrupt waiting for the long-running HTTP endpoints.

Unlike the stdio proxy there is no un-cancellable stdin worker thread in the HTTP
servers, so an interrupt can be handled by a graceful task-group cancel — no hard
exit needed. POSIX uses ``anyio.open_signal_receiver``; Windows falls back to a
plain ``signal.signal`` handler polled from the loop (asyncio has no
``add_signal_handler`` there).
"""

from __future__ import annotations

import signal
from types import FrameType

import anyio


async def wait_for_interrupt() -> None:
    """Return once SIGINT/SIGTERM (POSIX) or SIGINT/SIGBREAK (Windows) arrives.

    Off the main thread, where no handler can be installed, this waits forever and
    shutdown degrades to owner-driven cancellation.
    """
    try:
        with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
            async for _ in signals:  # pragma: no branch — yields or raises, never ends
                return
    except (NotImplementedError, ValueError, RuntimeError):
        # NotImplementedError: asyncio add_signal_handler on Windows. ValueError /
        # RuntimeError ("set_wakeup_fd only works in main thread"): off the main thread
        # on POSIX. Either way, degrade to the polling/owner-cancel path.
        await _wait_windows()


async def _wait_windows() -> None:
    received = False

    def _handler(signum: int, frame: FrameType | None) -> None:
        nonlocal received
        received = True

    installed = False
    for name in ("SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
            installed = True
        except (ValueError, OSError):
            pass  # not the main thread; can't install a handler here
    if not installed:
        await anyio.sleep_forever()
    while not received:
        await anyio.sleep(0.1)
