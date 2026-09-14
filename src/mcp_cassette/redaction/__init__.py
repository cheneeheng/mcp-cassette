"""Record-time redaction: structural rules plus deterministic free-text PII packs.

Structural rules (``*token*``, ``authorization``) hide credentials sitting in a named
field. Redaction packs hide free text — the customer email inside a ``tools/call``
argument, the phone number in a result. Both run on a deep copy at capture time; bytes
in flight are never altered. What ran is stamped on the cassette as a
:class:`~mcp_cassette.cassette.RedactionManifest`.
"""

from __future__ import annotations

from .backends import (
    STRUCTURAL,
    RedactorBackend,
    RegexPIIBackend,
    StructuralBackend,
    register_backend,
)
from .packs import (
    LoadedPack,
    PIIRule,
    RedactionPack,
    bundled_packs,
    load_pack,
    load_pack_file,
)
from .redactor import SALT_ENV, Redactor, build_redactor
from .replay import (
    UnresolvablePackError,
    append_redactor,
    replay_transform,
    resolve_packs,
)
from .spans import Span

__all__ = [
    "SALT_ENV",
    "STRUCTURAL",
    "LoadedPack",
    "PIIRule",
    "RedactionPack",
    "Redactor",
    "RedactorBackend",
    "RegexPIIBackend",
    "Span",
    "StructuralBackend",
    "UnresolvablePackError",
    "append_redactor",
    "build_redactor",
    "bundled_packs",
    "load_pack",
    "load_pack_file",
    "register_backend",
    "replay_transform",
    "resolve_packs",
]
