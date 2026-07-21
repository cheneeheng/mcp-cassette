"""Pacing unit tests (ITER_02_v3 §04): gap arithmetic and the disabled fast path."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from pydantic import ValidationError

from mcp_cassette.cassette import Message, PaceConfig
from mcp_cassette.replay.pacing import Pacer


def _msg(seq: int, t_offset_ms: int) -> Message:
    return Message(
        seq=seq,
        t_offset_ms=t_offset_ms,
        sender="server",
        kind="response",
        msg_id=seq,
        payload={"jsonrpc": "2.0", "id": seq, "result": {}},
    )


def _pacer(**kwargs: Any) -> Pacer:
    return Pacer(PaceConfig(mode="recorded", **kwargs))


def test_disabled_returns_zero() -> None:
    assert Pacer().gap_ms(_msg(0, 0), _msg(1, 500)) == 0.0


def test_recorded_gap_is_the_difference() -> None:
    assert _pacer().gap_ms(_msg(0, 100), _msg(1, 400)) == 300.0


def test_scale_multiplies_the_gap() -> None:
    assert _pacer(scale=0.1).gap_ms(_msg(0, 0), _msg(1, 400)) == 40.0


def test_cap_clamps_the_gap() -> None:
    assert _pacer(cap_ms=50).gap_ms(_msg(0, 0), _msg(1, 5000)) == 50.0


def test_cap_zero_is_uncapped() -> None:
    assert _pacer(cap_ms=0).gap_ms(_msg(0, 0), _msg(1, 40_000)) == 40_000.0


def test_negative_gap_is_zero() -> None:
    assert _pacer().gap_ms(_msg(0, 900), _msg(1, 100)) == 0.0


def test_no_predecessor_is_zero() -> None:
    assert _pacer().gap_ms(None, _msg(0, 900)) == 0.0


@pytest.mark.parametrize("scale", [0, -1])
def test_non_positive_scale_is_rejected(scale: float) -> None:
    with pytest.raises(ValidationError, match="scale"):
        PaceConfig(mode="recorded", scale=scale)


def test_disabled_wait_never_reads_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # The v1/v2 invariant — no wall-clock reads in the response path — enforced by
    # test rather than by comment.
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("anyio.sleep called on the disabled pacing path")

    monkeypatch.setattr(anyio, "sleep", explode)
    anyio.run(Pacer().wait, _msg(0, 0), _msg(1, 5000))


def test_enabled_wait_sleeps_the_computed_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Assert on the seconds handed to anyio.sleep, not on measured wall time: any
    # interval short enough to keep a unit test fast is at or below the Windows
    # clock granularity, so the measurement flakes while proving nothing extra.
    # Real elapsed time is proven in the integration layer, with generous bounds.
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(anyio, "sleep", record)
    anyio.run(_pacer(scale=0.5).wait, _msg(0, 100), _msg(1, 500))
    assert slept == [pytest.approx(0.2)]


def test_enabled_wait_with_a_zero_gap_does_not_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("anyio.sleep called for a zero-length gap")

    monkeypatch.setattr(anyio, "sleep", explode)
    anyio.run(_pacer().wait, None, _msg(0, 5000))
