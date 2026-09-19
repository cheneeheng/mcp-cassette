"""The write-time scrubber: structural rules first, then every text backend.

Output must be deterministic — a redactor whose bytes depended on anything but the
input value, the rule, and the salt would make re-recordings differ and turn ``diff``
and lint R002 into noise generators.
"""

from __future__ import annotations

import copy
import os
import warnings
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ..cassette import (
    RedactionManifest,
    RedactionRule,
    SaltMode,
    default_redaction_rules,
)
from .backends import STRUCTURAL, Direction, StructuralBackend, backend_factory
from .packs import LoadedPack, bundled_packs, load_pack_file
from .spans import apply, resolve

SALT_ENV = "MCP_CASSETTE_REDACT_SALT"
"""The environment variable ``salt_mode="env"`` keys pseudonyms with."""

_STABLE_SALT = b"mcp-cassette/redaction/stable/v1"
# A package constant: the same value yields the same pseudonym on every machine and
# every re-record, which keeps cassettes diffable. The HMAC is further keyed by the
# qualified rule id (pack id + rule id), so two rules never share a pseudonym space.
# The cost, stated in the guide: a stable pseudonym of a low-entropy value is
# dictionary-attackable. It hides the value from a casual reader only.


class Redactor:
    """Scrubs one payload at a time; holds no cross-message state."""

    def __init__(
        self,
        rules: Sequence[RedactionRule] = (),
        packs: Sequence[LoadedPack] = (),
        backends: Sequence[str] = (STRUCTURAL, "regex-pii"),
        *,
        salt_mode: SaltMode = "stable",
        profile: str | None = None,
    ) -> None:
        """Build the backends.

        Args:
            rules: Structural (key-glob / JSON-pointer) rules.
            packs: Redaction packs for the text backends.
            backends: Backend names to run; ``structural`` always runs first.
            salt_mode: ``stable`` or ``env`` (reads ``MCP_CASSETTE_REDACT_SALT``).
            profile: The profile name stamped on the manifest.

        Raises:
            ValueError: On an unknown backend name, or ``salt_mode="env"`` with the
                salt variable unset.
        """
        self.backends = list(backends)
        self.packs = list(packs)
        self.salt_mode: SaltMode = salt_mode
        self.profile = profile
        self._structural = (
            StructuralBackend(rules) if STRUCTURAL in self.backends else None
        )
        # A value a structural rule already replaced is never re-scanned, so no text
        # rule can match inside a redaction marker and produce nested nonsense.
        self._markers = frozenset(rule.replacement for rule in rules)
        self._text = [
            backend_factory(name)(self.packs)
            for name in self.backends
            if name != STRUCTURAL
        ]
        self._salt = _resolve_salt(salt_mode)

    @property
    def rule_ids(self) -> list[str]:
        """Every pack rule id, qualified as ``<pack>/<rule>``."""
        return [
            f"{loaded.pack.id}/{rule.id}"
            for loaded in self.packs
            for rule in loaded.pack.rules
        ]

    @property
    def has_client_rules(self) -> bool:
        """Whether any text rule scrubs client requests (and so must run at replay)."""
        return bool(self._text) and any(
            rule.direction == "both"
            for loaded in self.packs
            for rule in loaded.pack.rules
        )

    def manifest(self) -> RedactionManifest:
        """The evidence stamped on a recording: what ran, never what was found."""
        return RedactionManifest(
            profile=self.profile,
            backends=self.backends,
            packs=[loaded.ref() for loaded in self.packs],
            rule_ids=self.rule_ids,
            salt_mode=self.salt_mode,
            applied_at=datetime.now(UTC),
        )

    def apply_to_payload(
        self, payload: dict[str, Any] | str, direction: Direction
    ) -> tuple[dict[str, Any] | str, bool]:
        """Scrub a deep copy of one message payload.

        Structural rules run first. Text backends then scan every JSON string
        *value* recursively — object keys are structure and are never rewritten — and
        a bare ``str`` payload is scanned as one string. Tool ``name`` fields inside a
        ``tools/list`` result are skipped: a protocol identifier is not free text, and
        masking one would manufacture an R007 lint finding.

        Args:
            payload: The message payload.
            direction: Which side sent the message.

        Returns:
            ``(scrubbed_payload, changed)``.
        """
        changed = False
        if self._structural is not None:
            payload, changed = self._structural.apply(payload)
        elif isinstance(payload, dict):
            payload = copy.deepcopy(payload)
        if not self._text:
            return payload, changed
        if isinstance(payload, str):
            scrubbed = self._scrub(payload, direction)
            return scrubbed, changed or scrubbed != payload
        return payload, self._walk(payload, direction, False, False) or changed

    def _walk(
        self, node: Any, direction: Direction, in_result: bool, tool_entry: bool
    ) -> bool:
        changed = False
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    if tool_entry and key == "name":
                        continue
                    scrubbed = self._scrub(value, direction)
                    if scrubbed != value:
                        node[key] = scrubbed
                        changed = True
                elif key == "tools" and in_result and isinstance(value, list):
                    for item in value:
                        changed = self._walk(item, direction, False, True) or changed
                else:
                    child_in_result = key == "result" and isinstance(value, dict)
                    changed = (
                        self._walk(value, direction, child_in_result, False) or changed
                    )
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str):
                    scrubbed = self._scrub(value, direction)
                    if scrubbed != value:
                        node[index] = scrubbed
                        changed = True
                else:
                    changed = self._walk(value, direction, False, False) or changed
        return changed

    def _scrub(self, text: str, direction: Direction) -> str:
        if text in self._markers:
            return text
        spans = resolve(
            span for backend in self._text for span in backend.detect(text, direction)
        )
        return apply(text, spans, self._salt) if spans else text


def build_redactor(
    rules: Sequence[RedactionRule] = (),
    *,
    include_defaults: bool = True,
    pii_packs: Sequence[str | os.PathLike[str]] = (),
    profile: str | None = None,
    salt_mode: SaltMode = "stable",
) -> Redactor:
    """The redactor every recording front door uses.

    Args:
        rules: Extra structural rules (``--redact``).
        include_defaults: Include the default structural rules *and* the bundled PII
            packs — ``--no-default-redactions`` suppresses both.
        pii_packs: Extra redaction pack files (``--pii-pack``).
        profile: Profile name stamped on the manifest (``--redact-profile``).
        salt_mode: ``stable`` or ``env`` (``--redact-salt-env``).

    Returns:
        The configured :class:`Redactor`.

    Raises:
        ValueError: On a malformed or duplicate pack, or a missing env salt.
        OSError: If a pack file cannot be read.
    """
    structural = [*(default_redaction_rules() if include_defaults else []), *rules]
    packs = [
        *(bundled_packs() if include_defaults else []),
        *(load_pack_file(path) for path in pii_packs),
    ]
    seen: dict[str, str | None] = {}
    for loaded in packs:
        if loaded.pack.id in seen:
            raise ValueError(
                f"redaction pack id {loaded.pack.id!r} is loaded twice "
                f"({seen[loaded.pack.id]} and {loaded.path})"
            )
        seen[loaded.pack.id] = loaded.path
    _warn_collisions(packs)
    backends = [STRUCTURAL, "regex-pii"] if packs else [STRUCTURAL]
    return Redactor(structural, packs, backends, salt_mode=salt_mode, profile=profile)


def _warn_collisions(packs: Sequence[LoadedPack]) -> None:
    for loaded in packs:
        for rule in loaded.pack.rules:
            if rule.strategy == "replace" and rule.direction == "both":
                warnings.warn(
                    f"mcp-cassette: redaction rule {loaded.pack.id}/{rule.id} uses "
                    "strategy 'replace' with direction 'both': distinct values in "
                    "client requests collapse to one replacement, so replay can hand "
                    'back the wrong recorded response. Fix: set strategy = "hash", '
                    'or narrow to direction = "server".',
                    stacklevel=3,
                )


def _resolve_salt(mode: SaltMode) -> bytes:
    if mode == "stable":
        return _STABLE_SALT
    value = os.environ.get(SALT_ENV)
    if not value:
        raise ValueError(
            f"{SALT_ENV} is not set, but --redact-salt-env (salt_mode='env') needs "
            "it to key the pseudonyms; set it, or drop --redact-salt-env"
        )
    return value.encode("utf-8")
