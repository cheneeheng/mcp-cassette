"""Replay-side redaction: re-apply a recording's client rules to live requests.

A pack rule with ``direction = "both"`` scrubbed the recorded client requests, so a
live agent's real request can only match if it is scrubbed the same way before the
matcher sees it. ``hash`` under a stable salt is deterministic, so the real value and
the recorded pseudonym land on the same bytes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any

from ..cassette import PackRef, RedactionManifest, default_redaction_rules
from .backends import STRUCTURAL
from .packs import LoadedPack, bundled_packs, load_pack_file
from .redactor import Redactor

RequestTransform = Callable[[dict[str, Any]], dict[str, Any]]
"""Maps a live client request to the form its recorded counterpart was stored in."""


class UnresolvablePackError(ValueError):
    """A manifest names a redaction pack no source can supply."""


def resolve_packs(
    manifest: RedactionManifest,
    supplied: Sequence[str | os.PathLike[str]] = (),
) -> tuple[list[LoadedPack], list[str]]:
    """Find every pack a manifest names, by content hash.

    Sources, in order: the supplied pack files, the manifest's recorded ``path``, then
    the bundled packs. Guessing, or silently skipping the transform, would turn a
    configuration error into a wall of unmatched-request failures, so a pack no
    source supplies is an error.

    Args:
        manifest: The cassette's redaction manifest.
        supplied: Pack files given by the caller (``--pii-pack`` / ``pii_packs=``).

    Returns:
        ``(packs, unused)`` — the resolved packs in manifest order, and the supplied
        pack paths the manifest does not name (accepted, so one command can serve
        several cassettes).

    Raises:
        UnresolvablePackError: If a named pack cannot be found by its hash.
        ValueError: If a supplied pack file is malformed.
        OSError: If a supplied pack file cannot be read.
    """
    offered = [load_pack_file(path) for path in supplied]
    by_hash = {loaded.sha256: loaded for loaded in offered}
    wanted = {ref.sha256 for ref in manifest.packs}
    unused = [str(loaded.path) for loaded in offered if loaded.sha256 not in wanted]
    bundled = {loaded.sha256: loaded for loaded in bundled_packs()}
    resolved: list[LoadedPack] = []
    for ref in manifest.packs:
        found = (
            by_hash.get(ref.sha256)
            or _from_recorded_path(ref)
            or bundled.get(ref.sha256)
        )
        if found is None:
            raise UnresolvablePackError(
                f"redaction pack {ref.id!r} (sha256 {ref.sha256}) named by the "
                "cassette's redaction manifest was not found among the supplied "
                f"packs, at its recorded path {ref.path!r}, or in the bundled "
                "packs. Fixes: supply it with --pii-pack PATH (pii_packs= from "
                "Python), or re-record the cassette."
            )
        resolved.append(found)
    return resolved, unused


def replay_transform(
    manifest: RedactionManifest | None,
    packs: Sequence[str | os.PathLike[str]] = (),
) -> RequestTransform | None:
    """Reconstruct a recording's client-direction scrubbing for replay.

    Args:
        manifest: The cassette's manifest; ``None`` (a pre-v4 cassette) means no
            transform at all, so nothing about its replay changes.
        packs: Pack files to resolve the manifest's packs from.

    Returns:
        A transform applied to each incoming client request before matching, or
        ``None`` when no recorded rule touched client requests.

    Raises:
        UnresolvablePackError: If a named pack cannot be found.
        ValueError: On a malformed pack, or an ``env`` salt that is unset.
    """
    if manifest is None:
        return None
    resolved, _ = resolve_packs(manifest, packs)
    redactor = Redactor(
        (),
        resolved,
        [name for name in manifest.backends if name != STRUCTURAL],
        salt_mode=manifest.salt_mode,
    )
    if not redactor.has_client_rules:
        return None

    def transform(request: dict[str, Any]) -> dict[str, Any]:
        scrubbed, _ = redactor.apply_to_payload(request, "client")
        assert isinstance(scrubbed, dict)  # a dict payload stays a dict
        return scrubbed

    return transform


def append_redactor(
    manifest: RedactionManifest | None,
    packs: Sequence[str | os.PathLike[str]] = (),
) -> Redactor | None:
    """The redactor for exchanges ``new_episodes`` appends to an existing cassette.

    Appended messages must be scrubbed the way the recording was, or the merged
    cassette would mix pseudonyms with plain values under one manifest.

    Args:
        manifest: The existing cassette's manifest; ``None`` keeps the pre-v4
            structural-only behaviour.
        packs: Pack files to resolve the manifest's packs from.

    Returns:
        The redactor, or ``None`` for a cassette without a manifest.
    """
    if manifest is None:
        return None
    resolved, _ = resolve_packs(manifest, packs)
    rules = default_redaction_rules() if STRUCTURAL in manifest.backends else []
    return Redactor(
        rules,
        resolved,
        manifest.backends,
        salt_mode=manifest.salt_mode,
        profile=manifest.profile,
    )


def _from_recorded_path(ref: PackRef) -> LoadedPack | None:
    if ref.path is None or ref.path.startswith("builtin:"):
        return None
    try:
        loaded = load_pack_file(ref.path)
    except (OSError, ValueError):
        return None
    return loaded if loaded.sha256 == ref.sha256 else None
