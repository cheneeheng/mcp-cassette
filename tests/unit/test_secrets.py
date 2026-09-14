"""Entropy secret detection (ITER_02_v4 §04): shapes, thresholds, allowlist, cap."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.lint import run
from mcp_cassette.lint.packs import EntropyConfig
from mcp_cassette.lint.secrets import MAX_CANDIDATES, EntropyDetector, shannon_bits

TOKEN = "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"  # 40 chars, base64-shaped


def _tokens(text: str, config: EntropyConfig | None = None) -> list[str]:
    detector = EntropyDetector(config or EntropyConfig())
    return [secret.token for secret in detector.scan(text).secrets]


def test_planted_token_fires() -> None:
    assert _tokens(f"deploy key={TOKEN} ok") == [TOKEN]
    assert shannon_bits(TOKEN) > 5.0


@pytest.mark.parametrize(
    "benign",
    [
        "123e4567-e89b-12d3-a456-426614174000",
        "da39a3ee5e6b4b0d3255bfef95601890afd80709",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "2026-09-13T19:32:03.654263Z",
        "the quick brown fox jumps over the extraordinarily lazy dog",
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4",
        '{"nested": "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"}',
        "123456789012345678901234567890",
    ],
)
def test_benign_shapes_do_not_fire(benign: str) -> None:
    assert _tokens(benign) == []


def test_thresholds_shift_the_boundary_both_ways() -> None:
    assert _tokens(TOKEN, EntropyConfig(min_bits=6.0)) == []
    assert _tokens(TOKEN, EntropyConfig(min_length=41)) == []
    short = "a1B2c3D4e5F6g7H8"
    assert _tokens(short) == []
    assert _tokens(short, EntropyConfig(min_length=10)) == [short]


def test_allowlist_is_literal() -> None:
    config = EntropyConfig(allowlist=[TOKEN])
    assert _tokens(TOKEN, config) == []
    near_miss = TOKEN[:-1] + "X"
    assert _tokens(near_miss, config) == [near_miss]


def test_candidate_budget_truncates() -> None:
    many = " ".join(f"{i:04d}{TOKEN[4:]}" for i in range(MAX_CANDIDATES + 50))
    result = EntropyDetector(EntropyConfig()).scan(many)
    assert result.examined == MAX_CANDIDATES
    assert result.truncated


def _cassette(path: Path, text: str) -> Path:
    Cassette(
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        messages=[
            Message(
                seq=0,
                t_offset_ms=0,
                sender="server",
                kind="response",
                msg_id=1,
                payload={"result": {"content": [{"type": "text", "text": text}]}},
            )
        ],
    ).save(path)
    return path


def test_finding_never_contains_the_whole_token(tmp_path: Path) -> None:
    report = run(_cassette(tmp_path / "c.mcp.json", f"key {TOKEN}"))
    (finding,) = report.findings
    assert finding.rule == "R005"
    assert finding.severity == "warning"
    assert finding.tool is None
    assert finding.locator == "/messages/0/payload/result/content/0/text"
    assert TOKEN not in finding.message
    assert f'"{TOKEN[:6]}…"' in finding.message
    assert "40 chars" in finding.message
