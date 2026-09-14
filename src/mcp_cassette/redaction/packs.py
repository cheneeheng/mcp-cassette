"""Redaction packs: declarative TOML PII rules applied at record time.

The record-time twin of a lint pattern pack, and deliberately shaped like one — a
developer who has written one has written both. A pack is labelled regexes with a
replacement strategy, a direction, and an optional deterministic checksum validator.
Regexes are compiled, never evaluated as code, and no code is imported from a pack.
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..cassette import PackRef
from .spans import Strategy

PACK_VERSION = 1
"""The only accepted redaction-pack format version."""

BUNDLED_PACK_IDS = ("common",)
"""Packs shipped inside the library, enabled by default beside the structural rules."""

Direction = Literal["both", "server"]
Validator = Literal["none", "luhn", "mod97"]

_FLAG_LETTERS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}
_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


class PIIRule(BaseModel, extra="forbid"):
    """One free-text redaction rule from a pack.

    Attributes:
        id: Rule id, unique within its pack; qualified as ``<pack>/<rule>`` elsewhere.
        label: Names the value kind in ``replace``/``hash`` output, e.g. ``EMAIL``.
        regex: The pattern, compiled but never evaluated as code.
        flags: Subset of ``i``, ``m``, ``s``, ``x``.
        strategy: ``replace``, ``hash``, or ``mask``. Unset means ``hash`` for a
            ``both`` rule (distinct values must stay distinguishable in requests the
            matcher compares) and ``replace`` for a ``server`` rule.
        replacement: Literal replacement for ``replace`` (default ``<LABEL>``).
        direction: ``both`` scrubs client requests and server messages alike, and is
            re-applied to incoming requests at replay; ``server`` scrubs server
            messages only.
        validator: A deterministic post-match check — a card- or IBAN-shaped number
            that fails its checksum is not redacted.
    """

    id: str
    label: str
    regex: str
    flags: list[str] = Field(default_factory=list)
    strategy: Strategy | None = None
    replacement: str | None = None
    direction: Direction = "both"
    validator: Validator = "none"

    @property
    def effective_strategy(self) -> Strategy:
        """The strategy that applies once the direction-dependent default is filled."""
        if self.strategy is not None:
            return self.strategy
        return "hash" if self.direction == "both" else "replace"


class RedactionPack(BaseModel, extra="forbid"):
    """A versioned, identified collection of :class:`PIIRule`."""

    version: int
    id: str
    rules: list[PIIRule] = Field(default_factory=list)


@dataclass(frozen=True)
class LoadedPack:
    """A validated pack together with the provenance a manifest records.

    Attributes:
        pack: The parsed pack.
        path: Where it was loaded from, as given (``builtin:<id>`` when bundled).
        sha256: Hash of the pack file's bytes — the identity replay resolves by.
    """

    pack: RedactionPack
    path: str | None
    sha256: str

    def ref(self) -> PackRef:
        """The manifest entry naming this pack."""
        return PackRef(id=self.pack.id, path=self.path, sha256=self.sha256)


def load_pack(path: str | os.PathLike[str]) -> RedactionPack:
    """Load and validate one TOML redaction pack.

    Args:
        path: Path to the pack file.

    Returns:
        The validated :class:`RedactionPack`.

    Raises:
        ValueError: On any malformed pack; every message names the file and the key.
        OSError: If the file cannot be read.
    """
    return load_pack_file(path).pack


def load_pack_file(path: str | os.PathLike[str]) -> LoadedPack:
    """Like :func:`load_pack`, keeping the path and content hash for the manifest."""
    return parse_pack(Path(path).read_bytes(), source=str(path), path=str(path))


def bundled_packs() -> list[LoadedPack]:
    """The packs shipped with the library, in a stable order."""
    root = resources.files("mcp_cassette.redaction").joinpath("builtin")
    return [
        parse_pack(
            root.joinpath(f"{pack_id}.toml").read_bytes(),
            source=f"builtin:{pack_id}",
            path=f"builtin:{pack_id}",
        )
        for pack_id in BUNDLED_PACK_IDS
    ]


def parse_pack(data: bytes, *, source: str, path: str | None) -> LoadedPack:
    """Validate pack bytes.

    Args:
        data: The TOML file contents.
        source: How error messages name the pack.
        path: The provenance recorded in the manifest.

    Returns:
        The :class:`LoadedPack`.

    Raises:
        ValueError: On bad TOML, an unsupported version, an unknown key, an invalid
            id, a duplicate rule id, or a regex/flag that does not compile.
    """
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"redaction pack {source}: {exc}") from exc
    if raw.get("version") != PACK_VERSION:
        raise ValueError(
            f"redaction pack {source}: unsupported version {raw.get('version')!r} "
            f"(expected {PACK_VERSION})"
        )
    try:
        pack = RedactionPack.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"redaction pack {source}: {exc}") from exc
    _check_id(pack.id, source, "pack id")
    seen: set[str] = set()
    for rule in pack.rules:
        _check_id(rule.id, source, "rule id")
        if rule.id in seen:
            raise ValueError(f"redaction pack {source}: duplicate rule id {rule.id!r}")
        seen.add(rule.id)
        compile_rule(rule, source)
    return LoadedPack(pack=pack, path=path, sha256=hashlib.sha256(data).hexdigest())


def compile_rule(rule: PIIRule, source: str = "<pack>") -> re.Pattern[str]:
    """Compile a rule's regex with its flag letters.

    Raises:
        ValueError: On an unknown flag letter or a regex that does not compile.
    """
    flags = 0
    for letter in rule.flags:
        if letter not in _FLAG_LETTERS:
            raise ValueError(
                f"redaction pack {source}: rule {rule.id!r}: unknown regex flag "
                f"{letter!r} (accepted: {', '.join(sorted(_FLAG_LETTERS))})"
            )
        flags |= _FLAG_LETTERS[letter]
    try:
        return re.compile(rule.regex, flags)
    except re.error as exc:
        raise ValueError(
            f"redaction pack {source}: rule {rule.id!r}: invalid regex ({exc})"
        ) from exc


def validate_match(validator: Validator, text: str) -> bool:
    """Whether a regex match survives the rule's checksum validator."""
    if validator == "luhn":
        return luhn_valid(text)
    if validator == "mod97":
        return mod97_valid(text)
    return True


def luhn_valid(text: str) -> bool:
    """The Luhn checksum over a card-number-shaped string (separators ignored)."""
    digits = [int(c) for c in text if c in "0123456789"]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def mod97_valid(text: str) -> bool:
    """The ISO 13616 mod-97 checksum over an IBAN-shaped string (spaces ignored)."""
    compact = text.replace(" ", "").upper()
    if not (15 <= len(compact) <= 34 and compact.isascii() and compact.isalnum()):
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def _check_id(value: str, source: str, what: str) -> None:
    if not _ID_PATTERN.match(value):
        raise ValueError(
            f"redaction pack {source}: invalid {what} {value!r} (expected 1-32 chars "
            "matching [A-Za-z][A-Za-z0-9_-]*)"
        )
