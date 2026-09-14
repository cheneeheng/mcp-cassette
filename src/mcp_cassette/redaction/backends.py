"""Redactor backends: the protocol, the registry, and the two bundled backends.

A text backend answers one question — which ranges of this string are sensitive — and
never rewrites anything itself; :mod:`.spans` owns application. That split is the seam
a model-based backend would occupy later without becoming a rewrite.

Unlike lint, which refuses a plugin API because it evaluates untrusted cassettes,
redaction runs at record time on the developer's own machine against their own server,
so a registry is appropriate here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Literal, Protocol

from ..cassette import RedactionRule, apply_redactions
from .packs import LoadedPack, compile_rule, validate_match
from .spans import Span

Direction = Literal["client", "server"]
"""Which side sent the message being scrubbed."""

STRUCTURAL = "structural"
"""The reserved name of the key-glob/pointer backend."""


class RedactorBackend(Protocol):
    """A text backend: detects the sensitive ranges of one string."""

    def detect(self, text: str, direction: Direction) -> Iterable[Span]:
        """Return the sensitive ranges of ``text``.

        Args:
            text: One JSON string value (or a whole ``raw`` payload).
            direction: Which side sent the message the string belongs to.

        Returns:
            Detected spans, in any order and possibly overlapping.
        """
        ...  # pragma: no cover — protocol body


BackendFactory = Callable[[Sequence[LoadedPack]], RedactorBackend]
"""Builds a backend from the redaction packs in effect."""

_REGISTRY: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    """Register a text backend under ``name``.

    Args:
        name: The name manifests record and ``Redactor(backends=...)`` selects by.
        factory: Called with the loaded packs to build the backend.

    Raises:
        ValueError: If ``name`` is the reserved ``structural``.
    """
    if name == STRUCTURAL:
        raise ValueError("backend name 'structural' is reserved for the key rules")
    _REGISTRY[name] = factory


def backend_factory(name: str) -> BackendFactory:
    """Look up a registered text backend.

    Raises:
        ValueError: If nothing is registered under ``name``.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted([STRUCTURAL, *_REGISTRY]))
        raise ValueError(
            f"unknown redaction backend {name!r} (registered: {known})"
        ) from None


class StructuralBackend:
    """v1's key-glob and JSON-pointer rules, behind the backend seam.

    Its unit is structure, not text, so :meth:`detect` finds nothing and the work
    happens in :meth:`apply`, which runs before any text backend.
    """

    def __init__(self, rules: Sequence[RedactionRule]) -> None:
        """Hold the structural rules in application order."""
        self.rules = list(rules)

    def detect(self, text: str, direction: Direction) -> Iterable[Span]:
        """Structural rules match keys, never ranges of a string."""
        return ()

    def apply(self, payload: dict[str, Any] | str) -> tuple[dict[str, Any] | str, bool]:
        """Apply every rule to a deep copy, reporting whether any matched."""
        return apply_redactions(payload, self.rules)


class RegexPIIBackend:
    """Compiled :class:`~.packs.PIIRule` regexes from every loaded pack."""

    def __init__(self, packs: Sequence[LoadedPack]) -> None:
        """Compile every rule once, qualified by its pack id."""
        self._rules = [
            (f"{loaded.pack.id}/{rule.id}", rule, compile_rule(rule))
            for loaded in packs
            for rule in loaded.pack.rules
        ]

    def detect(self, text: str, direction: Direction) -> Iterator[Span]:
        """Every validated match of every rule that applies to ``direction``."""
        for qualified, rule, pattern in self._rules:
            if direction == "client" and rule.direction != "both":
                continue
            for match in pattern.finditer(text):
                if match.start() == match.end():
                    continue
                if not validate_match(rule.validator, match.group()):
                    continue
                yield Span(
                    start=match.start(),
                    end=match.end(),
                    rule_id=qualified,
                    strategy=rule.effective_strategy,
                    label=rule.label,
                    replacement=rule.replacement,
                )


register_backend("regex-pii", RegexPIIBackend)
