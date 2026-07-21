"""Structural comparison of two cassettes.

Re-recording after a server upgrade produces a new cassette whose interesting content
is the delta — new methods, a changed tool description, a reordered exchange sequence.
``git diff`` on the raw JSON drowns that in re-stamped ids and shifted offsets, so this
module compares only what replay itself cares about.

Deliberately overlapping with, and deliberately different from, lint's R002: R002 is a
*gate* (error severity, tool surfaces only, exit 4); this is *descriptive* (everything
that changed, no severity, exit 5 as a signal a human reads).
"""

from __future__ import annotations

import difflib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from .cassette import Cassette
from .lint.engine import latest_tools

ToolChangeKind = Literal["added", "removed", "description", "input_schema"]


class FieldChange(BaseModel):
    """One changed metadata field.

    Attributes:
        field: Dotted field name, e.g. ``transport`` or ``server_info.version``.
        old: The old value rendered as a string (``None`` when absent).
        new: The new value rendered as a string (``None`` when absent).
    """

    field: str
    old: str | None = None
    new: str | None = None


class MethodDelta(BaseModel):
    """A method whose recorded message count changed."""

    method: str
    old_count: int
    new_count: int


class ToolChange(BaseModel):
    """One change to a recorded tool surface.

    Attributes:
        tool: The tool name.
        change: What changed about it.
        diff: Unified-diff lines (empty for ``added``/``removed``).
        locator: JSON pointer into the new cassette (the old one for ``removed``).
    """

    tool: str
    change: ToolChangeKind
    diff: list[str] = Field(default_factory=list)
    locator: str


class CassetteDiff(BaseModel):
    """The structural delta between two cassettes.

    Attributes:
        old: Path to the baseline cassette.
        new: Path to the current cassette.
        metadata: Changed provenance fields.
        methods: Methods whose counts differ, sorted by name.
        tools: Tool surface changes, sorted by name then change kind.
        sequence: Unified-diff lines over the ordered exchange method sequence.
        identical: Whether every collection above is empty.
    """

    old: Path
    new: Path
    metadata: list[FieldChange] = Field(default_factory=list)
    methods: list[MethodDelta] = Field(default_factory=list)
    tools: list[ToolChange] = Field(default_factory=list)
    sequence: list[str] = Field(default_factory=list)
    identical: bool = True


def diff_cassettes(
    old: str | os.PathLike[str], new: str | os.PathLike[str]
) -> CassetteDiff:
    """Compare two cassettes structurally.

    JSON-RPC ids, ``t_offset_ms``, and ``Message.seq`` are never compared — they are
    re-stamped or clock-derived, so including them would make every re-recording
    differ. This mirrors the standing invariant that ids are never matched on.

    Args:
        old: Path to the baseline cassette.
        new: Path to the current cassette.

    Returns:
        The :class:`CassetteDiff`; ``identical`` is True when nothing changed.

    Raises:
        UnsupportedFormatVersion: If either file is a newer format version.
        FileNotFoundError: If either path does not exist.
    """
    old_cassette = Cassette.load(old)
    new_cassette = Cassette.load(new)
    metadata = _diff_metadata(old_cassette, new_cassette)
    methods = _diff_methods(old_cassette, new_cassette)
    tools = _diff_tools(old_cassette, new_cassette)
    sequence = _diff_sequence(old_cassette, new_cassette)
    return CassetteDiff(
        old=Path(old),
        new=Path(new),
        metadata=metadata,
        methods=methods,
        tools=tools,
        sequence=sequence,
        identical=not (metadata or methods or tools or sequence),
    )


def _diff_metadata(old: Cassette, new: Cassette) -> list[FieldChange]:
    pairs: list[tuple[str, str | None, str | None]] = [
        ("transport", old.transport, new.transport),
        ("protocol_version", old.protocol_version, new.protocol_version),
        (
            "server_info.name",
            old.server_info.name if old.server_info else None,
            new.server_info.name if new.server_info else None,
        ),
        (
            "server_info.version",
            old.server_info.version if old.server_info else None,
            new.server_info.version if new.server_info else None,
        ),
    ]
    if old.transport == "http" or new.transport == "http":
        # Host only, never the full URL — the same policy `inspect` applies, so a
        # diff cannot leak a query-string token.
        pairs.append(("server_host", _host(old.server_url), _host(new.server_url)))
    return [
        FieldChange(field=field, old=before, new=after)
        for field, before, after in pairs
        if before != after
    ]


def _host(url: str | None) -> str | None:
    return urlsplit(url).netloc if url else None


def _diff_methods(old: Cassette, new: Cassette) -> list[MethodDelta]:
    old_counts = _method_counts(old)
    new_counts = _method_counts(new)
    return [
        MethodDelta(
            method=name,
            old_count=old_counts.get(name, 0),
            new_count=new_counts.get(name, 0),
        )
        for name in sorted(set(old_counts) | set(new_counts))
        if old_counts.get(name, 0) != new_counts.get(name, 0)
    ]


def _method_counts(cassette: Cassette) -> Counter[str]:
    return Counter(m.method or f"<{m.kind}>" for m in cassette.messages)


def _diff_tools(old: Cassette, new: Cassette) -> list[ToolChange]:
    old_tools = latest_tools(old)
    new_tools = latest_tools(new)
    changes: list[ToolChange] = []
    for name in sorted(set(old_tools) | set(new_tools)):
        before = old_tools.get(name)
        after = new_tools.get(name)
        if before is None:
            assert after is not None
            changes.append(
                ToolChange(
                    tool=name, change="added", locator=f"{after.locator_base}/name"
                )
            )
            continue
        if after is None:
            changes.append(
                ToolChange(
                    tool=name, change="removed", locator=f"{before.locator_base}/name"
                )
            )
            continue
        if (before.description or "") != (after.description or ""):
            changes.append(
                ToolChange(
                    tool=name,
                    change="description",
                    diff=list(
                        difflib.unified_diff(
                            (before.description or "").splitlines(),
                            (after.description or "").splitlines(),
                            fromfile="baseline",
                            tofile="current",
                            lineterm="",
                        )
                    ),
                    locator=f"{after.locator_base}/description",
                )
            )
        if not _schema_equal(before.input_schema, after.input_schema):
            changes.append(
                ToolChange(
                    tool=name,
                    change="input_schema",
                    locator=f"{after.locator_base}/inputSchema",
                )
            )
    return changes


def _schema_equal(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _diff_sequence(old: Cassette, new: Cassette) -> list[str]:
    return list(
        difflib.unified_diff(
            _request_methods(old),
            _request_methods(new),
            fromfile="baseline",
            tofile="current",
            lineterm="",
            n=1,
        )
    )


def _request_methods(cassette: Cassette) -> list[str]:
    return [
        m.method or "<request>"
        for m in cassette.messages
        if m.sender == "client" and m.kind == "request"
    ]
