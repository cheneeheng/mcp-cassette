"""Declarative pattern packs and per-project lint configuration.

The bundled rules catch generic smells; a pattern pack catches *yours* — the vendor
name that must never appear in a tool description, the internal hostname that signals a
misconfigured staging server. Packs are TOML: labelled regexes with their own rule ids
and severities, extending the bundled set and never replacing it.

Deliberately declarative. A Python rule-plugin API would be a public contract to keep
semver-stable forever, and would make ``lint`` execute arbitrary third-party code on a
supply-chain-security surface — the one place that is least appropriate. Pack regexes
are compiled, never ``eval``'d, and no code is imported from a pack.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .patterns import INJECTION_PATTERNS

Severity = Literal["warning", "error"]
Surface = Literal["description", "result"]

PACK_VERSION = 1
"""The only accepted pattern-pack format version."""

_FLAG_LETTERS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}
_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
_PACK_KEYS = {"version", "patterns"}


class PatternRule(BaseModel, extra="forbid"):
    """One user-supplied pattern from a pack file.

    Attributes:
        id: Finding rule id; must not start with ``R`` (reserved for bundled rules)
            and appears verbatim in output, ``--select``, and ``--ignore``.
        label: Names the smell in the finding message.
        regex: The pattern, compiled but never evaluated as code.
        flags: Subset of ``i``, ``m``, ``s``, ``x``.
        severity: Finding severity (default ``error``).
        surfaces: Which lintable surfaces the pattern applies to.
        message: Optional replacement for the default finding wording.
    """

    id: str
    label: str
    regex: str
    flags: list[str] = Field(default_factory=list)
    severity: Severity = "error"
    surfaces: list[Surface] = Field(
        default_factory=lambda: ["description", "result"]  # type: ignore[arg-type]
    )
    message: str | None = None


class ProjectLintConfig(BaseModel, extra="forbid"):
    """The ``[tool.mcp_cassette.lint]`` block of a project's ``pyproject.toml``.

    Attributes:
        pattern_packs: Pack files to load (resolved relative to the pyproject.toml).
        select: Rule ids to run exclusively.
        ignore: Rule ids to skip.
        fail_on: Lowest severity that makes the run exit 4. Changes only the exit
            code — a finding's own severity is never rewritten, so JSON output stays
            a faithful record and two projects can gate the same cassette differently.
    """

    pattern_packs: list[Path] = Field(default_factory=list)
    select: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    fail_on: Severity = "error"


@dataclass(frozen=True)
class PatternMatch:
    """One pattern hit against a surface.

    ``rule_id`` and ``severity`` are ``None`` for a bundled pattern: the calling rule
    supplies its own id and severity, so bundled findings stay byte-identical to what
    shipped before packs existed.
    """

    rule_id: str | None
    label: str
    severity: Severity | None
    message: str | None


@dataclass(frozen=True)
class _Compiled:
    rule_id: str | None
    label: str
    regex: re.Pattern[str]
    severity: Severity | None
    surfaces: tuple[Surface, ...]
    message: str | None


class PatternSet:
    """The bundled patterns plus every loaded pack rule, compiled once."""

    def __init__(self, rules: list[PatternRule] | None = None) -> None:
        """Compile the bundled patterns followed by the given pack rules.

        Args:
            rules: Pack rules, in load order.

        Raises:
            ValueError: If a pack rule's regex or flags do not compile.
        """
        compiled: list[_Compiled] = [
            _Compiled(
                rule_id=None,
                label=label,
                regex=re.compile(pattern, flags),
                severity=None,
                surfaces=("description", "result"),
                message=None,
            )
            for label, pattern, flags in INJECTION_PATTERNS
        ]
        for rule in rules or []:
            compiled.append(
                _Compiled(
                    rule_id=rule.id,
                    label=rule.label,
                    regex=_compile(rule),
                    severity=rule.severity,
                    surfaces=tuple(rule.surfaces),
                    message=rule.message,
                )
            )
        self._compiled = compiled

    @classmethod
    def _from_compiled(cls, compiled: list[_Compiled]) -> PatternSet:
        instance = cls()
        instance._compiled = compiled
        return instance

    def filtered(self, pack_ids: Sequence[str], include_bundled: bool) -> PatternSet:
        """Restrict to the enabled rules.

        Args:
            pack_ids: Pack rule ids that survive ``--select``/``--ignore``.
            include_bundled: Whether the bundled patterns are enabled for the
                calling rule (``R001`` for descriptions, ``R004`` for results).

        Returns:
            A new set holding only the enabled patterns.
        """
        return self._from_compiled(
            [
                c
                for c in self._compiled
                if (c.rule_id is None and include_bundled)
                or (c.rule_id is not None and c.rule_id in pack_ids)
            ]
        )

    @property
    def rule_ids(self) -> list[str]:
        """Pack rule ids in load order (bundled ids belong to the bundled rules)."""
        return [c.rule_id for c in self._compiled if c.rule_id is not None]

    def for_surface(self, surface: Surface) -> list[_Compiled]:
        """Every compiled pattern applying to this surface, in iteration order."""
        return [c for c in self._compiled if surface in c.surfaces]

    def match(self, text: str, surface: Surface) -> list[PatternMatch]:
        """Every pattern this text matches on the given surface.

        Args:
            text: The surface text (already known not to be a redaction marker).
            surface: ``description`` or ``result``.

        Returns:
            Matches in iteration order: bundled patterns first, then packs in load
            order. Output order is the engine's ``(locator, rule)`` sort, so pack
            order cannot perturb ``--format json`` bytes.
        """
        return [
            PatternMatch(
                rule_id=c.rule_id,
                label=c.label,
                severity=c.severity,
                message=c.message,
            )
            for c in self.for_surface(surface)
            if c.regex.search(text)
        ]


def _compile(rule: PatternRule) -> re.Pattern[str]:
    flags = 0
    for letter in rule.flags:
        if letter not in _FLAG_LETTERS:
            raise ValueError(
                f"rule {rule.id!r}: unknown regex flag {letter!r} "
                f"(accepted: {', '.join(sorted(_FLAG_LETTERS))})"
            )
        flags |= _FLAG_LETTERS[letter]
    try:
        return re.compile(rule.regex, flags)
    except re.error as exc:
        raise ValueError(f"rule {rule.id!r}: invalid regex ({exc})") from exc


def load_pack(path: str | os.PathLike[str]) -> list[PatternRule]:
    """Load and validate one TOML pattern pack.

    Args:
        path: Path to the pack file.

    Returns:
        The pack's rules, in file order.

    Raises:
        ValueError: On any malformed pack — every message names the file and the
            offending key, because a typo'd ``severty`` must not silently disable a
            rule on a security surface.
        FileNotFoundError: If the pack file does not exist.
    """
    pack_path = Path(path)
    try:
        data = tomllib.loads(pack_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"pattern pack {pack_path}: {exc}") from exc
    unknown = sorted(set(data) - _PACK_KEYS)
    if unknown:
        raise ValueError(
            f"pattern pack {pack_path}: unknown top-level key(s) "
            f"{', '.join(unknown)} (expected: {', '.join(sorted(_PACK_KEYS))})"
        )
    version = data.get("version")
    if version != PACK_VERSION:
        raise ValueError(
            f"pattern pack {pack_path}: unsupported version {version} "
            f"(expected {PACK_VERSION})"
        )
    rules: list[PatternRule] = []
    for entry in data.get("patterns", []):
        try:
            rule = PatternRule.model_validate(entry)
        except ValidationError as exc:
            raise ValueError(f"pattern pack {pack_path}: {exc}") from exc
        _check_id(rule.id, pack_path)
        try:
            _compile(rule)
        except ValueError as exc:
            raise ValueError(f"pattern pack {pack_path}: {exc}") from exc
        rules.append(rule)
    return rules


def _check_id(rule_id: str, pack_path: Path) -> None:
    if rule_id.startswith("R"):
        raise ValueError(
            f"pattern pack {pack_path}: rule id {rule_id!r} is reserved for bundled "
            "rules; use another prefix"
        )
    if not _ID_PATTERN.match(rule_id):
        raise ValueError(
            f"pattern pack {pack_path}: invalid rule id {rule_id!r} (expected "
            "1-16 chars matching [A-Za-z][A-Za-z0-9_-]*)"
        )


def build_pattern_set(
    packs: list[str | os.PathLike[str]] | None = None,
) -> PatternSet:
    """Compile the bundled patterns plus every rule from the given packs.

    Args:
        packs: Pack file paths, in load order. Packs compose; a duplicate rule id
            across packs is rejected rather than silently shadowing.

    Returns:
        The assembled :class:`PatternSet`.

    Raises:
        ValueError: On a malformed pack or a duplicate rule id.
    """
    rules: list[PatternRule] = []
    origin: dict[str, Path] = {}
    for pack in packs or []:
        pack_path = Path(pack)
        for rule in load_pack(pack_path):
            if rule.id in origin:
                raise ValueError(
                    f"pattern pack {pack_path}: duplicate rule id {rule.id!r} "
                    f"(already defined by {origin[rule.id]})"
                )
            origin[rule.id] = pack_path
            rules.append(rule)
    return PatternSet(rules)


def discover_config(start: str | os.PathLike[str] | None = None) -> ProjectLintConfig:
    """Find the nearest ``pyproject.toml`` and read its lint configuration.

    Walks parent directories from ``start``. An absent file, or a file without a
    ``[tool.mcp_cassette.lint]`` table, yields defaults — never an error. Pack paths
    are resolved relative to the ``pyproject.toml`` that declared them, so the same CI
    step works from any subdirectory. The filesystem is read on every call and nothing
    is cached.

    Args:
        start: Directory to search from (default: the current working directory).

    Returns:
        The resolved :class:`ProjectLintConfig`.

    Raises:
        ValueError: If the table exists but is malformed.
    """
    here = Path(start) if start is not None else Path.cwd()
    for directory in [here, *here.parents]:
        candidate = directory / "pyproject.toml"
        if not candidate.is_file():
            continue
        try:
            data: dict[str, Any] = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            continue  # not ours to validate; a project's own tooling will complain
        table = data.get("tool", {}).get("mcp_cassette", {}).get("lint")
        if table is None:
            continue
        try:
            config = ProjectLintConfig.model_validate(table)
        except ValidationError as exc:
            raise ValueError(f"{candidate}: [tool.mcp_cassette.lint] {exc}") from exc
        return config.model_copy(
            update={"pattern_packs": [directory / p for p in config.pattern_packs]}
        )
    return ProjectLintConfig()
