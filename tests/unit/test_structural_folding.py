"""Structural key-globs fold separators: ITER_06_v3 F1 (ITER_01_v4 decision 7)."""

from __future__ import annotations

import pytest

from mcp_cassette.cassette import (
    RedactionRule,
    apply_redactions,
    default_redaction_rules,
)


@pytest.mark.parametrize(
    "key", ["X-API-Key", "X-Auth-Token", "Api-Key", "Access-Token"]
)
def test_default_rules_redact_hyphenated_credential_keys(key: str) -> None:
    payload, changed = apply_redactions({key: "live-value"}, default_redaction_rules())
    assert changed
    assert payload == {key: "REDACTED"}


def test_custom_underscore_glob_also_matches_hyphens() -> None:
    # The stated cost of folding: an existing custom glob's match set widens.
    payload, changed = apply_redactions(
        {"api-key": "v", "note": "keep"}, [RedactionRule(locator="*api_key*")]
    )
    assert changed
    assert payload == {"api-key": "REDACTED", "note": "keep"}


def test_key_without_separators_is_unaffected() -> None:
    payload, changed = apply_redactions(
        {"username": "alice"}, default_redaction_rules()
    )
    assert not changed
    assert payload == {"username": "alice"}
