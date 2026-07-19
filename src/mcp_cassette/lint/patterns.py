"""Bundled heuristic pattern data for the lint rules.

Plain Python constants — no data-file loading machinery. Each entry is
``(label, regex, flags)``; the label names the smell in the finding message.
"""

from __future__ import annotations

import re

INJECTION_PATTERNS: list[tuple[str, str, int]] = [
    (
        "override-instructions",
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,60}"
        r"\b(?:previous|prior|above|earlier|all|any|system)\b[^.\n]{0,60}"
        r"\b(?:instructions?|prompts?|rules?|directives?|guidelines?)\b",
        re.IGNORECASE,
    ),
    (
        "conceal-from-user",
        r"\b(?:do\s*n[o']t|do\s+not|never|without)\b[^.\n]{0,40}"
        r"\b(?:tell(?:ing)?|inform(?:ing)?|mention(?:ing)?|reveal(?:ing)?|"
        r"alert(?:ing)?|notify(?:ing)?|show(?:ing)?)\b[^.\n]{0,40}\buser\b",
        re.IGNORECASE,
    ),
    (
        "model-addressed-imperative",
        r"\b(?:you|the\s+assistant|the\s+model|the\s+ai|the\s+agent)\s+"
        r"(?:must|should|shall|have\s+to|are\s+required\s+to|are\s+to)\b",
        re.IGNORECASE,
    ),
    (
        "hidden-emphasis",
        r"<\s*/?\s*(?:important|system|secret|hidden)\s*>|(?:^|\n|\s)IMPORTANT\s*:",
        0,
    ),
]
