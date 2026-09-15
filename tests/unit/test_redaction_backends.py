"""The redaction backend registry and the two bundled backends (ITER_01_v4 §04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_cassette.cassette import RedactionRule
from mcp_cassette.redaction import (
    STRUCTURAL,
    RegexPIIBackend,
    StructuralBackend,
    load_pack_file,
    register_backend,
)
from mcp_cassette.redaction.backends import backend_factory


def test_structural_name_is_reserved() -> None:
    with pytest.raises(ValueError, match="'structural' is reserved"):
        register_backend(STRUCTURAL, RegexPIIBackend)


def test_unknown_backend_lists_what_is_registered() -> None:
    with pytest.raises(ValueError, match="unknown redaction backend 'presidio'") as exc:
        backend_factory("presidio")
    assert "registered: regex-pii, structural" in str(exc.value)
    assert backend_factory("regex-pii") is RegexPIIBackend


def test_structural_backend_detects_no_text_ranges_and_applies_key_rules() -> None:
    backend = StructuralBackend([RedactionRule(locator="*token*")])
    assert list(backend.detect("api_token=abc", "server")) == []
    scrubbed, changed = backend.apply({"api_token": "abc", "q": "x"})
    assert changed
    assert scrubbed == {"api_token": "REDACTED", "q": "x"}


def test_zero_width_matches_are_skipped(tmp_path: Path) -> None:
    pack = tmp_path / "pack.toml"
    pack.write_text(
        'version = 1\nid = "team"\n[[rules]]\nid = "xs"\nlabel = "X"\nregex = "x*"\n',
        encoding="utf-8",
    )
    backend = RegexPIIBackend([load_pack_file(pack)])
    spans = list(backend.detect("axxb", "server"))
    # "x*" matches empty at every position; only the non-empty "xx" becomes a span.
    assert [(s.start, s.end, s.rule_id) for s in spans] == [(1, 3, "team/xs")]
