"""Replay pacing: turn recorded ``t_offset_ms`` gaps into replay-time sleeps.

Every recorded message has carried a monotonic offset since v1 and replay has always
ignored it — instant replay is fast and deterministic, and stays the default. Pacing is
the opt-in that trades that determinism for recorded-latency fidelity, so an agent's
timeout handling, progress-stream UX, and retry/backoff logic meet the latency the real
server actually exhibited.

Gaps, never absolute offsets: each sleep is the distance between two adjacent recorded
messages, applied as the later one is emitted. That composes correctly whether the live
client is faster or slower than the recorded one.
"""

from __future__ import annotations

import anyio

from ..cassette import Message, PaceConfig


class Pacer:
    """Translates recorded ``t_offset_ms`` gaps into replay-time sleeps."""

    def __init__(self, config: PaceConfig | None = None) -> None:
        """Initialize the pacer.

        Args:
            config: Pacing configuration; ``None`` (the default everywhere) means
                disabled, and the disabled path neither sleeps nor reads a clock.
        """
        self.config = config or PaceConfig()

    @property
    def enabled(self) -> bool:
        """Whether recorded gaps are replayed at all."""
        return self.config.mode == "recorded"

    def gap_ms(self, previous: Message | None, current: Message) -> float:
        """The scaled, clamped delay to apply before emitting ``current``.

        Args:
            previous: The message the recorded stream showed immediately before
                ``current``, or ``None`` when there is no predecessor.
            current: The message about to be emitted.

        Returns:
            ``0.0`` when pacing is disabled, when ``previous`` is ``None``, or when
            the recorded gap is negative (clock skew, or concurrent HTTP exchanges
            whose capture interleaved) — zero means "as fast as possible", which is
            the pre-pacing behavior for that pair. Otherwise the recorded gap times
            ``scale``, clamped to ``cap_ms`` when that is non-zero.
        """
        if not self.enabled or previous is None:
            return 0.0
        raw = (current.t_offset_ms - previous.t_offset_ms) * self.config.scale
        if raw <= 0:
            return 0.0
        if self.config.cap_ms:
            return min(raw, float(self.config.cap_ms))
        return raw

    async def wait(self, previous: Message | None, current: Message) -> None:
        """Sleep :meth:`gap_ms` milliseconds before ``current`` is emitted.

        A no-op — no sleep, no clock read — when pacing is disabled.

        Args:
            previous: The recorded predecessor of ``current``.
            current: The message about to be emitted.
        """
        if not self.enabled:
            return
        delay = self.gap_ms(previous, current)
        if delay > 0:
            await anyio.sleep(delay / 1000)
