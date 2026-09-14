"""Entropy-based secret detection behind lint rule R005.

Structural and pattern redaction run at record time and stop a secret reaching disk;
R005 only tells you one got through. It is a smell, not a verdict: raw entropy flags
UUIDs, git SHAs, digests, and encoded images, all common in MCP traffic and none of them
secret, so the shape exclusions below are the rule's real content.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from .packs import EntropyConfig

MAX_CANDIDATES = 200
"""Candidate tokens scanned per message before the rest is left for a human."""

_SPLIT = re.compile(r"[\s\"'`,;:=&?/.<>()\[\]{}|\\]+")
_ENCODED_CHARSET = re.compile(r"^[A-Za-z0-9+_-]+$")
_REDACTION_MARKERS = re.compile(r"<[A-Z][A-Z0-9_]*>_[0-9a-f]{12}|REDACTED|•+")

# Excluded by shape before entropy is computed. A plain constant, like the bundled lint
# patterns: no data-file machinery for bundled detection.
SHAPE_EXCLUSIONS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
    re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),  # git object ids, sha256 digests
    re.compile(
        r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}(?::?\d{2}(?::?\d{2}(?:\.\d+)?)?)?)?"
        r"(?:Z|[+-]\d{2}:?\d{2})?$"
    ),
    re.compile(r"^[+-]?\d+$"),
)


@dataclass(frozen=True)
class SecretSpan:
    """One high-entropy token found in a string.

    Attributes:
        token: The token itself — callers must never echo it whole.
        bits: Shannon entropy per character.
    """

    token: str
    bits: float


@dataclass(frozen=True)
class ScanResult:
    """The outcome of scanning one string.

    Attributes:
        secrets: High-entropy tokens found.
        examined: Candidate tokens that counted against the budget.
        truncated: Whether candidates remained when the budget ran out.
    """

    secrets: list[SecretSpan]
    examined: int
    truncated: bool


class EntropyDetector:
    """Finds tokens that look like encoded secrets."""

    def __init__(self, config: EntropyConfig) -> None:
        """Hold the resolved R005 configuration."""
        self.config = config
        self._allowlist = frozenset(config.allowlist)

    def scan(self, text: str, budget: int = MAX_CANDIDATES) -> ScanResult:
        """Scan one string value.

        Candidate tokens are tested in order: length, charset, allowlist, shape
        exclusions, entropy.

        Args:
            text: The string.
            budget: How many candidate tokens may still be examined.

        Returns:
            The :class:`ScanResult`.
        """
        if text.startswith("data:") or _is_json_container(text):
            return ScanResult([], 0, False)
        # Redaction markers first: a hash pseudonym is short, mixed-charset, and
        # high-entropy by construction, and redaction must never manufacture findings.
        stripped = _REDACTION_MARKERS.sub(" ", text)
        found: list[SecretSpan] = []
        examined = 0
        for token in _SPLIT.split(stripped):
            if len(token) < self.config.min_length or not _looks_encoded(token):
                continue
            if examined >= budget:
                return ScanResult(found, examined, True)
            examined += 1
            if token in self._allowlist or any(
                shape.match(token) for shape in SHAPE_EXCLUSIONS
            ):
                continue
            bits = shannon_bits(token)
            if bits >= self.config.min_bits:
                found.append(SecretSpan(token=token, bits=bits))
        return ScanResult(found, examined, False)


def shannon_bits(text: str) -> float:
    """Shannon entropy per character of ``text``."""
    length = len(text)
    counts = Counter(text)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _looks_encoded(token: str) -> bool:
    # base64/base64url/hex/mixed alphanumeric, and not a plain word: an encoded token
    # carries a digit or mixes case.
    if not _ENCODED_CHARSET.match(token):
        return False
    has_digit = any(c.isdigit() for c in token)
    mixed_case = token != token.lower() and token != token.upper()
    return has_digit or mixed_case


def _is_json_container(text: str) -> bool:
    if text.lstrip()[:1] not in ("{", "["):
        return False
    try:
        return isinstance(json.loads(text), dict | list)
    except json.JSONDecodeError:
        return False
