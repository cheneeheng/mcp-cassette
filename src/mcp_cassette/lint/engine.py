"""Lint engine: extract lintable surfaces from a cassette and run enabled rules.

Surfaces are every tool ``name``/``description`` from recorded ``tools/list``
results and every text content block from recorded ``tools/call`` results. Both
cassettes of a baseline pair go through the ordinary format-1|2 loader, so
cross-version comparison (v1 stdio baseline vs v2 http recording of the same
server) works by construction — tool surfaces live in payloads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..cassette import Cassette
from .packs import ProjectLintConfig, build_pattern_set
from .rules import (
    REDACTED_MARKER,
    RULE_IDS,
    LintFinding,
    LintReport,
    ResultText,
    ToolSurface,
    rule_r001,
    rule_r002,
    rule_r003,
    rule_r004,
)


def run(
    cassette: str | os.PathLike[str],
    baseline: str | os.PathLike[str] | None = None,
    rules: list[str] | None = None,
    *,
    ignore: list[str] | None = None,
    packs: list[str | os.PathLike[str]] | None = None,
    config: ProjectLintConfig | None = None,
) -> LintReport:
    """Lint a cassette, returning a deterministic report.

    Args:
        cassette: Path to the cassette to lint.
        baseline: Optional older cassette for drift comparison (enables R002).
        rules: Rule ids to run (default: all bundled rules plus every pack rule).
        ignore: Rule ids to skip.
        packs: Pattern pack files, additive to those named by ``config``.
        config: Resolved project configuration.

    Returns:
        The :class:`LintReport`, findings sorted by locator (then rule id) so
        ``--format json`` output is byte-identical for identical inputs.
    """
    report, _ = run_with_notes(
        cassette, baseline, rules, ignore=ignore, packs=packs, config=config
    )
    return report


def run_with_notes(
    cassette: str | os.PathLike[str],
    baseline: str | os.PathLike[str] | None = None,
    rules: list[str] | None = None,
    *,
    ignore: list[str] | None = None,
    packs: list[str | os.PathLike[str]] | None = None,
    config: ProjectLintConfig | None = None,
) -> tuple[LintReport, list[str]]:
    """Like :func:`run`, also returning note-level lines for text output.

    Notes record skipped surfaces (e.g. redacted descriptions, which are never
    pattern-matched so redaction cannot manufacture findings) and contradictory
    rule selection.
    """
    config = config or ProjectLintConfig()
    all_packs: list[str | os.PathLike[str]] = [*config.pattern_packs, *(packs or [])]
    pattern_set = build_pattern_set(all_packs)
    selected = list(rules) if rules else [*RULE_IDS, *pattern_set.rule_ids]
    ignored = list(ignore or [])
    notes = [
        f"note: rule {rule_id} is both selected and ignored; selection wins"
        for rule_id in selected
        if rule_id in ignored
    ]
    enabled = selected if rules else [r for r in selected if r not in ignored]
    loaded = Cassette.load(cassette)
    tool_lists, results = extract_surfaces(loaded)
    tools = [tool for tools in tool_lists for tool in tools]
    notes += [
        f'note: skipped redacted description of tool "{t.name}" '
        f"({t.locator_base}/description)"
        for t in tools
        if t.description == REDACTED_MARKER
    ]
    findings: list[LintFinding] = []
    # Pack rules live on the same surfaces as the bundled patterns but carry their
    # own ids, so enabling is filtered per surface rather than per rule function.
    findings += rule_r001(tools, pattern_set.filtered(enabled, "R001" in enabled))
    if "R002" in enabled and baseline is not None:
        baseline_lists, _ = extract_surfaces(Cassette.load(baseline))
        baseline_tools = [tool for tools in baseline_lists for tool in tools]
        findings += rule_r002(tools, baseline_tools)
    if "R003" in enabled:
        findings += rule_r003(tool_lists)
    findings += rule_r004(results, pattern_set.filtered(enabled, "R004" in enabled))
    findings.sort(key=lambda f: (f.locator, f.rule))
    report = LintReport(
        cassette=Path(cassette),
        baseline=Path(baseline) if baseline is not None else None,
        findings=findings,
    )
    return report, notes


def extract_surfaces(
    cassette: Cassette,
) -> tuple[list[list[ToolSurface]], list[ResultText]]:
    """Pull the lintable surfaces out of a loaded cassette.

    Returns:
        ``(tool_lists, result_texts)`` where ``tool_lists`` holds one list of
        :class:`ToolSurface` per recorded ``tools/list`` result (R003 needs the
        per-result grouping) and ``result_texts`` holds every text content block
        from ``tools/call`` results.
    """
    request_by_id: dict[str | int, dict[str, Any]] = {}
    for m in cassette.messages:
        if (
            m.sender == "client"
            and m.kind == "request"
            and m.msg_id is not None
            and isinstance(m.payload, dict)
        ):
            request_by_id[m.msg_id] = m.payload

    tool_lists: list[list[ToolSurface]] = []
    results: list[ResultText] = []
    for index, m in enumerate(cassette.messages):
        if not (
            m.sender == "server"
            and m.kind == "response"
            and m.msg_id is not None
            and isinstance(m.payload, dict)
        ):
            continue
        request = request_by_id.get(m.msg_id)
        if request is None:
            continue
        method = request.get("method")
        result = m.payload.get("result")
        if not isinstance(result, dict):
            continue
        if method == "tools/list":
            tools = result.get("tools")
            if isinstance(tools, list):
                tool_lists.append(_extract_tools(tools, index))
        elif method == "tools/call":
            params = request.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            content = result.get("content")
            if isinstance(content, list):
                for j, block in enumerate(content):
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                    ):
                        results.append(
                            ResultText(
                                tool=tool_name if isinstance(tool_name, str) else None,
                                text=block["text"],
                                locator=(
                                    f"/messages/{index}/payload/result/content/{j}/text"
                                ),
                            )
                        )
    return tool_lists, results


def latest_tools(cassette: Cassette) -> dict[str, ToolSurface]:
    """The cassette's tool surfaces by name, last seen winning.

    The dedup rule R002 uses, shared with ``inspect --tools`` and ``diff`` so the
    three surfaces can never disagree about what a tool surface is.

    Args:
        cassette: The loaded cassette.

    Returns:
        Tool surfaces keyed by tool name.
    """
    tool_lists, _ = extract_surfaces(cassette)
    return {tool.name: tool for tools in tool_lists for tool in tools}


def _extract_tools(tools: list[Any], message_index: int) -> list[ToolSurface]:
    surfaces: list[ToolSurface] = []
    for j, tool in enumerate(tools):
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        description = tool.get("description")
        surfaces.append(
            ToolSurface(
                name=tool["name"],
                description=description if isinstance(description, str) else None,
                input_schema=tool.get("inputSchema"),
                locator_base=f"/messages/{message_index}/payload/result/tools/{j}",
            )
        )
    return surfaces
