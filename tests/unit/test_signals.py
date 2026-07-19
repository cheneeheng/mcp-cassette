"""Cross-platform interrupt-wait unit tests for the HTTP endpoints (_signals.py).

The POSIX ``open_signal_receiver`` path needs a real signal on a main thread, so
these tests force the Windows fallback deterministically by patching the receiver
and the stdlib handler installation — OS-independent by construction.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from typing import Any

import anyio
import pytest

from mcp_cassette import _signals
from mcp_cassette._signals import wait_for_interrupt


class _OneShotReceiver:
    """A stand-in for a working POSIX receiver: yields one signal, OS-independent."""

    def __enter__(self) -> Any:
        async def signals() -> Any:
            yield signal.SIGINT

        return signals()

    def __exit__(self, *exc: object) -> None:
        pass


class _NoReceiver:
    def __enter__(self) -> Any:
        raise NotImplementedError("no signal receiver on this loop")

    def __exit__(self, *exc: object) -> None:  # pragma: no cover - never entered
        pass


def _force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _signals.anyio, "open_signal_receiver", lambda *sigs: _NoReceiver()
    )


def test_receiver_path_returns_on_first_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _signals.anyio, "open_signal_receiver", lambda *sigs: _OneShotReceiver()
    )

    async def main() -> None:
        with anyio.fail_after(5):
            await wait_for_interrupt()

    anyio.run(main)


def test_fallback_returns_once_handler_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_fallback(monkeypatch)
    handlers: dict[int, Callable[[int, Any], None]] = {}

    def fake_signal(sig: int, handler: Any) -> None:
        handlers[sig] = handler

    monkeypatch.setattr(_signals.signal, "signal", fake_signal)
    # A platform without SIGBREAK is skipped, not an error.
    monkeypatch.delattr(_signals.signal, "SIGBREAK", raising=False)

    async def main() -> None:
        async with anyio.create_task_group() as tg:

            async def trigger() -> None:
                await anyio.sleep(0.05)
                handlers[signal.SIGINT](signal.SIGINT, None)

            tg.start_soon(trigger)
            with anyio.fail_after(5):
                await wait_for_interrupt()

    anyio.run(main)
    assert set(handlers) == {signal.SIGINT}


def test_fallback_off_main_thread_waits_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_fallback(monkeypatch)

    def refuse(sig: int, handler: Any) -> None:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(_signals.signal, "signal", refuse)

    async def main() -> None:
        with anyio.move_on_after(0.3) as scope:
            await wait_for_interrupt()
        assert scope.cancelled_caught  # degraded to owner-driven cancellation

    anyio.run(main)
