"""Cassette schema and file I/O.

Defines the pydantic v2 models that make up a cassette — the structured, diffable,
committable record of an MCP stdio session — plus atomic load/save and the redaction
rules applied at record time. The schema is message-generic: every JSON-RPC message is
captured verbatim whatever its method.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

FORMAT_VERSION = 1
"""Current on-disk cassette format version. Bumped only on breaking schema changes."""

Sender = Literal["client", "server"]
MessageKind = Literal["request", "response", "notification", "raw"]
Ordering = Literal["per_method", "strict", "none"]
FaultType = Literal["delay", "timeout", "error", "malformed", "disconnect"]
MalformedStrategy = Literal["truncate", "not_json", "wrong_id"]


class ServerInfo(BaseModel):
    """Server identity extracted from the ``initialize`` result."""

    name: str
    version: str


class Message(BaseModel):
    """A single JSON-RPC message captured on the wire.

    Attributes:
        seq: Zero-based session-ordering index, strictly increasing.
        t_offset_ms: Milliseconds from proxy start (monotonic clock).
        sender: Which side of the transport emitted the message.
        kind: JSON-RPC shape classification.
        method: The JSON-RPC ``method`` if present.
        msg_id: The JSON-RPC ``id`` if present.
        payload: Verbatim decoded JSON object, or the raw line (``str``) for
            ``kind == "raw"``.
        redacted: Whether any redaction rule altered this message's payload.
    """

    seq: int
    t_offset_ms: int
    sender: Sender
    kind: MessageKind
    method: str | None = None
    msg_id: str | int | None = None
    payload: dict[str, Any] | str
    redacted: bool = False


class Cassette(BaseModel):
    """An ordered recording of one MCP stdio session."""

    format_version: int = FORMAT_VERSION
    recorded_at: datetime
    transport: Literal["stdio"] = "stdio"
    protocol_version: str | None = None
    server_info: ServerInfo | None = None
    messages: list[Message] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Cassette:
        """Parse and validate a cassette file.

        Args:
            path: Path to the JSON cassette file.

        Returns:
            The validated :class:`Cassette`.

        Raises:
            UnsupportedFormatVersion: If the file's ``format_version`` is newer than
                this library understands.
            pydantic.ValidationError: If the file does not match the schema.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        version = data.get("format_version", FORMAT_VERSION)
        if version > FORMAT_VERSION:
            raise UnsupportedFormatVersion(
                f"cassette format_version {version} is newer than supported "
                f"{FORMAT_VERSION}; upgrade mcp-cassette"
            )
        return cls.model_validate(data)

    def save(self, path: str | os.PathLike[str]) -> None:
        """Atomically write the cassette to ``path``.

        Writes to a sibling ``.tmp`` file then ``os.replace`` for atomicity. Uses
        ``indent=2`` with pydantic field order (not ``sort_keys``) so diffs are stable
        and readable.

        Args:
            path: Destination path.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json", exclude_none=False)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp, target)


class MatchConfig(BaseModel):
    """How incoming replay requests are matched against recorded messages."""

    match_on: list[str] = Field(default_factory=lambda: ["method", "params"])
    ignore_params: list[str] = Field(default_factory=list)
    ordering: Ordering = "per_method"
    on_unmatched: Literal["error"] = "error"
    rewrite_protocol_version: bool = False


class RedactionRule(BaseModel):
    """A rule scrubbing sensitive values from payloads at record time.

    ``locator`` is either a key-glob (e.g. ``*token*``) matched case-insensitively
    against every dict key at any depth, or a JSON pointer (e.g.
    ``/result/content/0/text``) addressing one location.
    """

    locator: str
    replacement: str = "REDACTED"

    @property
    def is_pointer(self) -> bool:
        """Whether ``locator`` is a JSON pointer rather than a key-glob."""
        return self.locator.startswith("/")

    def apply(self, payload: dict[str, Any] | str) -> dict[str, Any] | str:
        """Return a redacted deep copy of ``payload``.

        Args:
            payload: The message payload (dict for decoded JSON, str for ``raw``).

        Returns:
            A new payload with matching values replaced. ``raw`` string payloads are
            returned unchanged (structural redaction needs keys).
        """
        redacted, _ = self._apply(payload)
        return redacted

    def _apply(
        self, payload: dict[str, Any] | str
    ) -> tuple[dict[str, Any] | str, bool]:
        if isinstance(payload, str):
            return payload, False
        clone = copy.deepcopy(payload)
        if self.is_pointer:
            changed = _redact_pointer(clone, self.locator, self.replacement)
        else:
            changed = _redact_key_glob(clone, self.locator, self.replacement)
        return clone, changed


def default_redaction_rules() -> list[RedactionRule]:
    """The always-on default redaction rule set (key-globs, case-insensitive)."""
    globs = [
        "*token*",
        "*secret*",
        "*password*",
        "*apikey*",
        "*api_key*",
        "authorization",
    ]
    return [RedactionRule(locator=g) for g in globs]


def apply_redactions(
    payload: dict[str, Any] | str, rules: list[RedactionRule]
) -> tuple[dict[str, Any] | str, bool]:
    """Apply every rule in order, reporting whether any changed the payload.

    Args:
        payload: The message payload.
        rules: Redaction rules to apply in sequence.

    Returns:
        ``(redacted_payload, changed)`` where ``changed`` is True if any rule matched.
    """
    if isinstance(payload, str):
        return payload, False
    current: dict[str, Any] = copy.deepcopy(payload)
    changed_any = False
    for rule in rules:
        if rule.is_pointer:
            changed = _redact_pointer(current, rule.locator, rule.replacement)
        else:
            changed = _redact_key_glob(current, rule.locator, rule.replacement)
        changed_any = changed_any or changed
    return current, changed_any


def _redact_key_glob(value: Any, glob: str, replacement: str) -> bool:
    changed = False
    if isinstance(value, dict):
        for key in list(value.keys()):
            if fnmatch.fnmatch(key.lower(), glob.lower()):
                value[key] = replacement
                changed = True
            else:
                changed = _redact_key_glob(value[key], glob, replacement) or changed
    elif isinstance(value, list):
        for item in value:
            changed = _redact_key_glob(item, glob, replacement) or changed
    return changed


def _redact_pointer(root: Any, pointer: str, replacement: str) -> bool:
    tokens = _parse_pointer(pointer)
    if not tokens:
        return False
    parent = root
    for token in tokens[:-1]:
        parent = _pointer_step(parent, token)
        if parent is _MISSING:
            return False
    last = tokens[-1]
    if isinstance(parent, dict) and last in parent:
        parent[last] = replacement
        return True
    if isinstance(parent, list):
        idx = _as_index(last, len(parent))
        if idx is not None:
            parent[idx] = replacement
            return True
    return False


class _Missing:
    pass


_MISSING = _Missing()


def _parse_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    parts = pointer.split("/")[1:]
    return [p.replace("~1", "/").replace("~0", "~") for p in parts]


def _pointer_step(node: Any, token: str) -> Any:
    if isinstance(node, dict):
        return node.get(token, _MISSING)
    if isinstance(node, list):
        idx = _as_index(token, len(node))
        return node[idx] if idx is not None else _MISSING
    return _MISSING


def _as_index(token: str, length: int) -> int | None:
    try:
        idx = int(token)
    except ValueError:
        return None
    if 0 <= idx < length:
        return idx
    return None


class Fault(BaseModel):
    """A single fault applied to a matched request at replay time."""

    target: FaultTarget
    type: FaultType
    params: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def delay(cls, method: str, ms: int, *, nth: int | None = None) -> Fault:
        """Sleep ``ms`` milliseconds, then respond normally."""
        return cls(
            target=FaultTarget(method=method, nth=nth),
            type="delay",
            params={"ms": ms},
        )

    @classmethod
    def timeout(cls, method: str, *, nth: int | None = None) -> Fault:
        """Never respond to the matched request; keep serving others."""
        return cls(target=FaultTarget(method=method, nth=nth), type="timeout")

    @classmethod
    def error(
        cls,
        method: str,
        *,
        code: int = -32603,
        message: str = "mcp-cassette injected error",
        nth: int | None = None,
    ) -> Fault:
        """Replace the recorded response with a JSON-RPC error object."""
        return cls(
            target=FaultTarget(method=method, nth=nth),
            type="error",
            params={"code": code, "message": message},
        )

    @classmethod
    def malformed(
        cls,
        method: str,
        *,
        strategy: MalformedStrategy = "truncate",
        nth: int | None = None,
    ) -> Fault:
        """Emit a corrupted response line per ``strategy``."""
        return cls(
            target=FaultTarget(method=method, nth=nth),
            type="malformed",
            params={"strategy": strategy},
        )

    @classmethod
    def disconnect(
        cls, method: str, *, after_response: bool = False, nth: int | None = None
    ) -> Fault:
        """Close the pipes and exit, simulating server death."""
        return cls(
            target=FaultTarget(method=method, nth=nth),
            type="disconnect",
            params={"after_response": after_response},
        )


class FaultTarget(BaseModel):
    """Which matched requests a fault applies to."""

    method: str
    nth: int | None = None


class FaultOverlay(BaseModel):
    """A sidecar collection of faults applied to a cassette at replay time.

    Recorded cassettes are never mutated; the overlay is a separate object built in
    test code or loaded from a ``<cassette>.faults.json`` sidecar.
    """

    faults: list[Fault] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> FaultOverlay:
        """Load a fault overlay from a JSON sidecar file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


class UnsupportedFormatVersion(Exception):  # noqa: N818 — public API name per plan
    """Raised when a cassette's format_version is newer than supported."""


class UnsupportedCassetteFeature(Exception):  # noqa: N818 — public API name per plan
    """Raised when a cassette uses a feature replay cannot serve (e.g. server->client
    requests)."""


Fault.model_rebuild()
