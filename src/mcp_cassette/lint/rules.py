"""Lint models and the bundled rules (R001–R007).

``LintFinding``/``LintReport`` are the report schema; the rule functions operate on
surfaces extracted by :mod:`.engine` and return findings with JSON-pointer locators
into the cassette, so a finding is one ``inspect`` or editor-jump away from its
evidence.
"""

from __future__ import annotations

import difflib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .packs import PatternMatch, PatternSet, Surface
from .secrets import MAX_CANDIDATES, EntropyDetector

Severity = Literal["warning", "error"]

RULE_IDS = ("R001", "R002", "R003", "R004", "R005", "R006", "R007")
"""Bundled rule ids: the default selection, and what --select/--ignore accept."""

REDACTED_MARKER = "REDACTED"
"""The default redaction replacement; redacted surfaces are skipped, not matched."""


class LintFinding(BaseModel):
    """One lint finding.

    Attributes:
        rule: Rule id, e.g. ``"R001"``.
        severity: ``error`` findings fail CI (exit 4); ``warning`` alone exits 0.
        message: Human-readable finding; first line is the one-line summary.
        locator: JSON pointer into the cassette naming the evidence.
        tool: The tool name involved, when one applies.
    """

    rule: str
    severity: Severity
    message: str
    locator: str
    tool: str | None = None


class LintReport(BaseModel):
    """The full result of one lint run, serializable for ``--format json``."""

    cassette: Path
    baseline: Path | None = None
    findings: list[LintFinding] = Field(default_factory=list)


@dataclass
class ToolSurface:
    """One tool entry from a recorded ``tools/list`` result."""

    name: str
    description: str | None
    input_schema: Any
    locator_base: str


@dataclass
class ResultText:
    """One text content block from a recorded ``tools/call`` result."""

    tool: str | None
    text: str
    locator: str


@dataclass
class StringSurface:
    """One JSON string value anywhere in a message payload."""

    text: str
    locator: str
    message_index: int


@dataclass
class TextSurface:
    """One piece of tool-definition text beyond the description.

    Attributes:
        tool: The tool name of the recorded occurrence this text belongs to.
        kind: ``name``, ``schema_description``, or ``schema_enum``.
        text: The text.
        locator: Exact JSON pointer into the cassette.
    """

    tool: str
    kind: Surface
    text: str
    locator: str


def rule_r001(
    tools: list[ToolSurface], patterns: PatternSet | None = None
) -> list[LintFinding]:
    """Instruction injection in tool descriptions (error).

    Iterates the :class:`PatternSet` rather than a module-level list, so a pack
    pattern fires here with its own id and severity while a bundled pattern still
    emits ``R001`` with the wording it has always had.
    """
    patterns = patterns or PatternSet()
    findings: list[LintFinding] = []
    for tool in tools:
        if tool.description is None or tool.description == REDACTED_MARKER:
            continue
        for hit in patterns.match(tool.description, "description"):
            findings.append(
                LintFinding(
                    rule=hit.rule_id or "R001",
                    severity=hit.severity or "error",
                    message=hit.message
                    or (
                        f'tool "{tool.name}": description matches injection '
                        f"pattern ({hit.label})"
                    ),
                    locator=f"{tool.locator_base}/description",
                    tool=tool.name,
                )
            )
    return findings


def rule_r002(
    tools: list[ToolSurface], baseline_tools: list[ToolSurface]
) -> list[LintFinding]:
    """Tool description/schema drift vs a baseline cassette — the rug pull (error).

    Every recorded occurrence is compared, not just the last one per name: a
    server that drifts a tool, lets the agent use it, then re-lists the original
    surface must not be able to hide the middle listing behind the reverted one.
    A current occurrence is clean when *some* baseline occurrence of the same name
    matches it on both fields, because a baseline that legitimately re-listed with
    variations recorded all of those variations as trusted.

    New tools appearing relative to the baseline are not flagged (servers
    legitimately grow); only changed descriptions/schemas for the same name are.
    """
    baseline_by_name: dict[str, list[ToolSurface]] = {}
    for tool in baseline_tools:
        baseline_by_name.setdefault(tool.name, []).append(tool)
    findings: list[LintFinding] = []
    for current in tools:
        olds = baseline_by_name.get(current.name)
        if not olds or any(_surface_equal(current, o) for o in olds):
            continue
        name = current.name
        old = olds[-1]  # render the diff against the newest trusted occurrence
        if (current.description or "") != (old.description or ""):
            diff = list(
                difflib.unified_diff(
                    (old.description or "").splitlines(),
                    (current.description or "").splitlines(),
                    fromfile="baseline",
                    tofile="current",
                    lineterm="",
                )
            )
            added = sum(
                1 for d in diff if d.startswith("+") and not d.startswith("+++")
            )
            removed = sum(
                1 for d in diff if d.startswith("-") and not d.startswith("---")
            )
            message = (
                f'tool "{name}": description changed vs baseline '
                f"(+{added} -{removed} lines)\n" + "\n".join(diff)
            )
            findings.append(
                LintFinding(
                    rule="R002",
                    severity="error",
                    message=message,
                    locator=f"{current.locator_base}/description",
                    tool=name,
                )
            )
        if not _schema_equal(current.input_schema, old.input_schema):
            findings.append(
                LintFinding(
                    rule="R002",
                    severity="error",
                    message=f'tool "{name}": inputSchema changed vs baseline',
                    locator=f"{current.locator_base}/inputSchema",
                    tool=name,
                )
            )
    return findings


def _schema_equal(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _surface_equal(a: ToolSurface, b: ToolSurface) -> bool:
    return (a.description or "") == (b.description or "") and _schema_equal(
        a.input_schema, b.input_schema
    )


def rule_r003(tool_lists: list[list[ToolSurface]]) -> list[LintFinding]:
    """Duplicate tool names within one ``tools/list`` result — shadowing (warning)."""
    findings: list[LintFinding] = []
    for tools in tool_lists:
        seen: set[str] = set()
        for tool in tools:
            if tool.name in seen:
                findings.append(
                    LintFinding(
                        rule="R003",
                        severity="warning",
                        message=(
                            f'duplicate tool name "{tool.name}" within one '
                            "tools/list result (shadowing within the recorded "
                            "server)"
                        ),
                        locator=f"{tool.locator_base}/name",
                        tool=tool.name,
                    )
                )
            seen.add(tool.name)
    return findings


def rule_r004(
    results: list[ResultText], patterns: PatternSet | None = None
) -> list[LintFinding]:
    """Instruction-shaped tool result text — data trying to be instructions.

    Warning, not error: result text legitimately quotes such phrases more often
    than descriptions do.
    """
    patterns = patterns or PatternSet()
    findings: list[LintFinding] = []
    for result in results:
        if result.text == REDACTED_MARKER:
            continue
        for hit in patterns.match(result.text, "result"):
            findings.append(
                LintFinding(
                    rule=hit.rule_id or "R004",
                    severity=hit.severity or "warning",
                    message=_result_message(hit, result),
                    locator=result.locator,
                    tool=result.tool,
                )
            )
    return findings


def rule_r005(
    strings: list[StringSurface], detector: EntropyDetector
) -> tuple[list[LintFinding], list[str]]:
    """High-entropy strings that look like secrets redaction missed (warning).

    Secrets hide anywhere, not only in tool surfaces, so this walks every string
    value. The message carries the entropy, the length, and at most six characters of
    the token: a linter that echoes the secret into CI logs has moved the leak rather
    than reported it. Scanning is capped per message; hitting the cap adds a note.

    Returns:
        ``(findings, notes)``.
    """
    findings: list[LintFinding] = []
    notes: list[str] = []
    budgets: dict[int, int] = {}
    for surface in strings:
        index = surface.message_index
        remaining = budgets.get(index, MAX_CANDIDATES)
        result = detector.scan(surface.text, remaining)
        budgets[index] = remaining - result.examined
        note = (
            f"note: R005 stopped after {MAX_CANDIDATES} candidate tokens in "
            f"message {index}; review the rest of it by hand"
        )
        if result.truncated and note not in notes:
            notes.append(note)
        for secret in result.secrets:
            findings.append(
                LintFinding(
                    rule="R005",
                    severity="warning",
                    message=(
                        f"high-entropy string ({secret.bits:.1f} bits/char, "
                        f'{len(secret.token)} chars, "{secret.token[:6]}…")'
                    ),
                    locator=surface.locator,
                )
            )
    return findings, notes


_KIND_WORDS: dict[str, str] = {
    "name": "tool name",
    "schema_description": "schema description",
    "schema_enum": "schema enum value",
}


def rule_r006(
    surfaces: list[TextSurface], patterns: PatternSet | None = None
) -> list[LintFinding]:
    """Injection phrasing in a tool name or ``inputSchema`` text (warning).

    A separate id rather than a widened R001: scanning new surfaces under an
    ``error`` rule would turn an upgrade into a CI break. The message names the
    surface kind, because phrasing in a property description and phrasing in the tool
    description need different responses from a reviewer. Pack rules that target
    these surfaces fire here with their own id and severity.
    """
    patterns = patterns or PatternSet()
    findings: list[LintFinding] = []
    for surface in surfaces:
        if surface.text == REDACTED_MARKER:
            continue
        kind = _KIND_WORDS[surface.kind]
        for hit in patterns.match(surface.text, surface.kind):
            default = (
                f'tool "{surface.tool}": injection phrasing in {kind} ({hit.label})'
                if hit.rule_id is None
                else f'tool "{surface.tool}": {kind} matches pattern ({hit.label})'
            )
            findings.append(
                LintFinding(
                    rule=hit.rule_id or "R006",
                    severity=hit.severity or "warning",
                    message=hit.message or default,
                    locator=surface.locator,
                    tool=surface.tool,
                )
            )
    return findings


def rule_r007(surfaces: list[TextSurface]) -> list[LintFinding]:
    """Mixed-script or non-ASCII tool names — lookalike identifiers (warning).

    Uses script mixing via :mod:`unicodedata` rather than a confusables table (a data
    file). MCP tool names are protocol identifiers with no reason to carry non-ASCII,
    so plain non-ASCII is itself worth a warning. The message names each offending
    character by code point and Unicode name, since the point is that it is visually
    indistinguishable from what a reader assumes is there.
    """
    findings: list[LintFinding] = []
    for surface in surfaces:
        if surface.kind != "name" or surface.text.isascii():
            continue
        offenders = dict.fromkeys(c for c in surface.text if not c.isascii())
        points = ", ".join(
            f"U+{ord(c):04X} {unicodedata.name(c, 'UNNAMED CHARACTER')}"
            for c in offenders
        )
        scripts = sorted({_script(c) for c in surface.text if c.isalpha()})
        if len(scripts) > 1:
            detail = f"mixed-script tool name ({', '.join(scripts)}): {points}"
        else:
            detail = f"non-ASCII tool name: {points}"
        findings.append(
            LintFinding(
                rule="R007",
                severity="warning",
                message=f'tool "{surface.text}": {detail}',
                locator=surface.locator,
                tool=surface.text,
            )
        )
    return findings


def _script(char: str) -> str:
    name = unicodedata.name(char, "")
    return name.split(" ", 1)[0] if name else "UNKNOWN"


def _result_message(hit: PatternMatch, result: ResultText) -> str:
    if hit.message is not None:
        return hit.message
    return f"tools/call result text matches injection pattern ({hit.label})" + (
        f' — tool "{result.tool}"' if result.tool else ""
    )
