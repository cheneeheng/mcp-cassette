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
from .packs import EntropyConfig, ProjectLintConfig, build_pattern_set
from .rules import (
    REDACTED_MARKER,
    RULE_IDS,
    LintFinding,
    LintReport,
    ResultText,
    StringSurface,
    TextSurface,
    ToolSurface,
    rule_r001,
    rule_r002,
    rule_r003,
    rule_r004,
    rule_r005,
    rule_r006,
    rule_r007,
)
from .secrets import EntropyDetector

MAX_SCHEMA_DEPTH = 12
"""How deep the ``inputSchema`` walk goes; ``$ref`` is never followed."""


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
    pattern-matched so redaction cannot manufacture findings), contradictory rule
    selection, and an R005 scan that hit its per-message cap.

    Raises:
        ValueError: If ``rules`` or ``ignore`` names an id that is neither bundled nor
            defined by a loaded pack — a typo'd ``--select`` would otherwise enable
            nothing and report clean.
    """
    config = config or ProjectLintConfig()
    all_packs: list[str | os.PathLike[str]] = [*config.pattern_packs, *(packs or [])]
    pattern_set = build_pattern_set(all_packs)
    known = [*RULE_IDS, *pattern_set.rule_ids]
    _check_rule_ids(rules or [], known, "--select")
    _check_rule_ids(ignore or [], known, "--ignore")
    selected = list(rules) if rules else known
    ignored = list(ignore or [])
    # Read the caller's own selection, not `selected`: with no --select that is a
    # synthesized "no filter" placeholder, and testing it made every --ignore'd id
    # read as a conflict the run did not have (ignore wins on that branch).
    notes = [
        f"note: rule {rule_id} is both selected and ignored; selection wins"
        for rule_id in (rules or [])
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
    # A second tool-surface extraction for the widened kinds. R002 above keeps the
    # flattened v3 list, so a name surface can never enter the baseline comparison.
    tool_text, schema_notes = extract_tool_text(loaded)
    notes += schema_notes
    findings += rule_r006(tool_text, pattern_set.filtered(enabled, "R006" in enabled))
    if "R007" in enabled:
        findings += rule_r007(tool_text)
    entropy = config.entropy or EntropyConfig()
    if "R005" in enabled and entropy.enabled:
        # A second extraction path, parallel to the tool surfaces: secrets hide in any
        # payload string, and keeping the paths apart lets either widen alone.
        secrets, secret_notes = rule_r005(
            extract_strings(loaded), EntropyDetector(entropy)
        )
        findings += secrets
        notes += secret_notes
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


def extract_tool_text(cassette: Cassette) -> tuple[list[TextSurface], list[str]]:
    """Tool names and ``inputSchema`` text, one set per recorded occurrence.

    Nothing is deduplicated by name: a tool listed with a lookalike name and re-listed
    with the real one must surface both, or the reverted listing would hide the first
    (the shape ITER_06_v3 closed for R002). ``schema_description`` is every
    ``description`` string in the schema at any depth; ``schema_enum`` is every string
    member of any ``enum`` (non-strings are skipped, not stringified). The walk stops at
    :data:`MAX_SCHEMA_DEPTH` and never resolves ``$ref``.

    Args:
        cassette: The loaded cassette.

    Returns:
        ``(surfaces, notes)`` — surfaces in document order, and one note per tool
        occurrence whose schema was cut off at the depth cap.
    """
    tool_lists, _ = extract_surfaces(cassette)
    surfaces: list[TextSurface] = []
    notes: list[str] = []
    for tools in tool_lists:
        for tool in tools:
            surfaces.append(
                TextSurface(tool.name, "name", tool.name, f"{tool.locator_base}/name")
            )
            schema_pointer = f"{tool.locator_base}/inputSchema"
            if _walk_schema(tool.input_schema, schema_pointer, tool.name, 0, surfaces):
                notes.append(
                    f'note: stopped walking the inputSchema of tool "{tool.name}" at '
                    f"depth {MAX_SCHEMA_DEPTH} ({schema_pointer})"
                )
    return surfaces, notes


def _walk_schema(
    node: Any, pointer: str, tool: str, depth: int, out: list[TextSurface]
) -> bool:
    """Collect schema text under ``node``; return whether the depth cap cut it off."""
    if not isinstance(node, dict | list):
        return False
    if depth >= MAX_SCHEMA_DEPTH:
        return bool(node)
    truncated = False
    items = node.items() if isinstance(node, dict) else enumerate(node)
    for key, value in items:
        child = f"{pointer}/{_pointer_token(key)}"
        if key == "description" and isinstance(value, str):
            out.append(TextSurface(tool, "schema_description", value, child))
        elif key == "enum" and isinstance(value, list):
            out.extend(
                TextSurface(tool, "schema_enum", member, f"{child}/{position}")
                for position, member in enumerate(value)
                if isinstance(member, str)
            )
        else:
            truncated = _walk_schema(value, child, tool, depth + 1, out) or truncated
    return truncated


def _pointer_token(key: object) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def extract_strings(cassette: Cassette) -> list[StringSurface]:
    """Every string value in every message payload, with its JSON pointer.

    Walks all message kinds, ``raw`` and ``unclassified`` included — exactly the
    payloads structural redaction can miss. Object keys are structure, not values,
    and are not yielded.

    Args:
        cassette: The loaded cassette.

    Returns:
        String surfaces in document order.
    """
    surfaces: list[StringSurface] = []
    for index, message in enumerate(cassette.messages):
        _walk_strings(message.payload, f"/messages/{index}/payload", index, surfaces)
    return surfaces


def _walk_strings(
    node: Any, pointer: str, index: int, out: list[StringSurface]
) -> None:
    if isinstance(node, str):
        out.append(StringSurface(text=node, locator=pointer, message_index=index))
    elif isinstance(node, dict):
        for key, value in node.items():
            _walk_strings(value, f"{pointer}/{_pointer_token(key)}", index, out)
    elif isinstance(node, list):
        for position, value in enumerate(node):
            _walk_strings(value, f"{pointer}/{position}", index, out)


def _check_rule_ids(ids: list[str], known: list[str], flag: str) -> None:
    unknown = [rule_id for rule_id in ids if rule_id not in known]
    if unknown:
        raise ValueError(
            f"unknown rule id(s) {', '.join(repr(r) for r in unknown)} in {flag} "
            f"(valid: {', '.join(known)})"
        )


def latest_tools(cassette: Cassette) -> dict[str, ToolSurface]:
    """The cassette's tool surfaces by name, last seen winning.

    Shared by ``inspect --tools`` and ``diff`` so the two surfaces can never
    disagree about what the agent ends up believing. ``rule_r002`` deliberately
    does not use it: it must answer what the agent was *exposed to*, which means
    every occurrence, not the last one per name.

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
