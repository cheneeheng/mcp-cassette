"""Span model and overlap resolution shared by every text redaction backend.

Every backend, present and future, reduces to "which character ranges of this string
are sensitive". Detection differs between backends; application must not, which is what
lets two backends run in any order and still produce byte-identical output.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Strategy = Literal["replace", "hash", "mask"]

MASK_CHAR = "•"
"""The character a ``mask`` strategy writes over all but the last four characters."""


@dataclass(frozen=True, order=True)
class Span:
    """One detected sensitive range of a string.

    Attributes:
        start: Start offset (inclusive).
        end: End offset (exclusive).
        rule_id: The qualified rule id (``<pack>/<rule>``) that detected it.
        strategy: How the range is rewritten.
        label: Names the kind of value, e.g. ``EMAIL``; used by ``replace`` and
            ``hash`` output.
        replacement: Literal replacement for ``replace`` (``<LABEL>`` when unset).
    """

    start: int
    end: int
    rule_id: str
    strategy: Strategy
    label: str = "REDACTED"
    replacement: str | None = None


def resolve(spans: Iterable[Span]) -> list[Span]:
    """Sort and de-overlap detected spans into a total, stable ordering.

    Spans are ordered by ``(start asc, end desc, rule_id asc)`` and the first span of
    an overlapping run wins, which makes the *widest* span win among those starting
    together. A narrower or partially overlapping detection is dropped rather than
    nested, because applying both would corrupt offsets. The result is identical for
    any input order.

    Args:
        spans: Detected spans from any number of backends.

    Returns:
        Non-overlapping spans in ascending offset order.
    """
    kept: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end, s.rule_id)):
        if kept and span.start < kept[-1].end:
            continue
        kept.append(span)
    return kept


def render(span: Span, matched: str, salt: bytes) -> str:
    """The replacement text for one matched range.

    Args:
        span: The span being rewritten.
        matched: The original text of the range.
        salt: The HMAC salt for ``hash``.

    Returns:
        ``replace`` -> the rule's replacement or ``<LABEL>``; ``hash`` ->
        ``<LABEL>_<12 hex>``, an HMAC-SHA256 keyed per rule so two rules never share
        a pseudonym space; ``mask`` -> all but the last four characters masked.
    """
    if span.strategy == "replace":
        return span.replacement or f"<{span.label}>"
    if span.strategy == "hash":
        key = hmac.new(salt, span.rule_id.encode("utf-8"), hashlib.sha256).digest()
        digest = hmac.new(key, matched.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"<{span.label}>_{digest[:12]}"
    keep = matched[-4:] if len(matched) > 4 else ""
    return MASK_CHAR * (len(matched) - len(keep)) + keep


def apply(text: str, spans: list[Span], salt: bytes) -> str:
    """Rewrite every span of ``text``, reading offsets from the original string.

    Every offset is read against the untouched original and the output is assembled
    from slices, so a replacement longer or shorter than its range can never shift a
    later span.

    Args:
        text: The original string.
        spans: Resolved, non-overlapping spans (see :func:`resolve`).
        salt: The HMAC salt for ``hash``.

    Returns:
        The redacted string.
    """
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor : span.start])
        pieces.append(render(span, text[span.start : span.end], salt))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)
