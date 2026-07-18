"""Lint models and the four bundled rules (R001–R004).

``LintFinding``/``LintReport`` are the report schema; the rule functions operate on
surfaces extracted by :mod:`.engine` and return findings with JSON-pointer locators
into the cassette, so a finding is one ``inspect`` or editor-jump away from its
evidence.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .patterns import INJECTION_PATTERNS

Severity = Literal["warning", "error"]

RULE_IDS = ("R001", "R002", "R003", "R004")

_COMPILED = [
    (label, re.compile(pattern, flags)) for label, pattern, flags in INJECTION_PATTERNS
]

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


def match_injection(text: str) -> list[str]:
    """Labels of every injection pattern the text matches."""
    return [label for label, regex in _COMPILED if regex.search(text)]


def rule_r001(tools: list[ToolSurface]) -> list[LintFinding]:
    """Instruction injection in tool descriptions (error)."""
    findings: list[LintFinding] = []
    for tool in tools:
        if tool.description is None or tool.description == REDACTED_MARKER:
            continue
        for label in match_injection(tool.description):
            findings.append(
                LintFinding(
                    rule="R001",
                    severity="error",
                    message=(
                        f'tool "{tool.name}": description matches injection '
                        f"pattern ({label})"
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

    New tools appearing relative to the baseline are not flagged (servers
    legitimately grow); only changed descriptions/schemas for the same name are.
    """
    latest: dict[str, ToolSurface] = {t.name: t for t in tools}
    baseline_latest: dict[str, ToolSurface] = {t.name: t for t in baseline_tools}
    findings: list[LintFinding] = []
    for name, current in latest.items():
        old = baseline_latest.get(name)
        if old is None:
            continue
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
                f"(+{added} −{removed} lines)\n" + "\n".join(diff)
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


def rule_r004(results: list[ResultText]) -> list[LintFinding]:
    """Instruction-shaped tool result text — data trying to be instructions.

    Warning, not error: result text legitimately quotes such phrases more often
    than descriptions do.
    """
    findings: list[LintFinding] = []
    for result in results:
        if result.text == REDACTED_MARKER:
            continue
        for label in match_injection(result.text):
            findings.append(
                LintFinding(
                    rule="R004",
                    severity="warning",
                    message=(
                        f"tools/call result text matches injection pattern ({label})"
                        + (f' — tool "{result.tool}"' if result.tool else "")
                    ),
                    locator=result.locator,
                    tool=result.tool,
                )
            )
    return findings
