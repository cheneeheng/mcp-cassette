"""Span resolution and application (SKELETON_v4 §04): total, stable, offset-safe."""

from __future__ import annotations

import itertools
import re

from mcp_cassette.redaction.spans import (
    MASK_CHAR,
    Span,
    Strategy,
    apply,
    render,
    resolve,
)


def _span(
    start: int,
    end: int,
    rule: str = "p/r",
    strategy: Strategy = "replace",
    label: str = "X",
) -> Span:
    return Span(start, end, rule, strategy, label)


def test_resolution_is_invariant_under_input_order() -> None:
    spans = [
        _span(0, 5, "p/a"),
        _span(0, 10, "p/b"),
        _span(3, 8, "p/c"),
        _span(12, 15, "p/d"),
        _span(12, 15, "p/a"),
    ]
    expected = resolve(spans)
    for permutation in itertools.permutations(spans):
        assert resolve(permutation) == expected
    # widest wins among a shared start; equal ranges tie-break on rule id
    assert expected == [_span(0, 10, "p/b"), _span(12, 15, "p/a")]


def test_partial_overlap_is_dropped_rather_than_nested() -> None:
    assert resolve([_span(2, 6), _span(0, 4)]) == [_span(0, 4)]


def test_apply_reads_every_offset_from_the_original() -> None:
    text = "a@b.io and c@d.io"
    spans = resolve([_span(11, 17, label="EMAIL"), _span(0, 6, label="EMAIL")])
    assert apply(text, spans, b"salt") == "<EMAIL> and <EMAIL>"


def test_replace_prefers_the_rule_replacement() -> None:
    span = Span(0, 3, "p/r", "replace", "X", replacement="[gone]")
    assert render(span, "abc", b"") == "[gone]"


def test_mask_keeps_exactly_the_last_four_characters() -> None:
    assert render(_span(0, 11, strategy="mask"), "4111-1111-1", b"") == (
        MASK_CHAR * 7 + "11-1"
    )
    assert render(_span(0, 3, strategy="mask"), "abc", b"") == MASK_CHAR * 3


def test_hash_is_keyed_per_rule() -> None:
    first = render(_span(0, 5, "p/one", "hash", "EMAIL"), "a@b.io", b"salt")
    second = render(_span(0, 5, "p/two", "hash", "EMAIL"), "a@b.io", b"salt")
    assert re.fullmatch(r"<EMAIL>_[0-9a-f]{12}", first)
    assert first != second
    assert first == render(_span(0, 5, "p/one", "hash", "EMAIL"), "a@b.io", b"salt")
