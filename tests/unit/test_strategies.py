"""Replacement strategies and salt modes (ITER_01_v4 §04 decisions 2-3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mcp_cassette.redaction import (
    SALT_ENV,
    Redactor,
    UnresolvablePackError,
    append_redactor,
    build_redactor,
    load_pack_file,
    replay_transform,
    resolve_packs,
)

EMAIL = "reach alice@example.com"


def _scrub(**kwargs: object) -> str:
    scrubbed, _ = build_redactor(**kwargs).apply_to_payload(  # type: ignore[arg-type]  # test kwargs
        {"text": EMAIL}, "server"
    )
    assert isinstance(scrubbed, dict)
    return str(scrubbed["text"])


def test_stable_hash_is_identical_across_processes() -> None:
    code = (
        "from mcp_cassette.redaction import build_redactor;"
        f"print(build_redactor().apply_to_payload({{'text': {EMAIL!r}}}, 'server')"
        "[0]['text'])"
    )
    other = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert other.stdout.strip() == _scrub()
    assert _scrub().startswith("reach <EMAIL>_")


def test_env_salts_produce_different_pseudonyms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SALT_ENV, "one")
    first = _scrub(salt_mode="env")
    monkeypatch.setenv(SALT_ENV, "two")
    second = _scrub(salt_mode="env")
    assert len({first, second, _scrub()}) == 3


def test_env_salt_mode_without_the_variable_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SALT_ENV, raising=False)
    with pytest.raises(ValueError, match=SALT_ENV):
        build_redactor(salt_mode="env")


def _pack(tmp_path: Path, rules: str) -> Path:
    path = tmp_path / "team.toml"
    path.write_text('version = 1\nid = "team"\n' + rules, encoding="utf-8")
    return path


def test_replace_uses_replacement_or_label(tmp_path: Path) -> None:
    pack = _pack(
        tmp_path,
        '[[rules]]\nid = "ticket"\nlabel = "TICKET"\nregex = "T-\\\\d+"\n'
        'direction = "server"\n'
        '[[rules]]\nid = "host"\nlabel = "HOST"\nregex = "db\\\\d\\\\.corp"\n'
        'direction = "server"\nreplacement = "[internal]"\n',
    )
    redactor = build_redactor(include_defaults=False, pii_packs=[pack])
    scrubbed, changed = redactor.apply_to_payload(
        {"text": "T-42 on db1.corp"}, "server"
    )
    assert changed
    assert scrubbed == {"text": "<TICKET> on [internal]"}


def test_mask_strategy_keeps_last_four(tmp_path: Path) -> None:
    pack = _pack(
        tmp_path,
        '[[rules]]\nid = "acct"\nlabel = "ACCT"\nregex = "\\\\d{11}"\n'
        'strategy = "mask"\n',
    )
    redactor = build_redactor(include_defaults=False, pii_packs=[pack])
    scrubbed, _ = redactor.apply_to_payload({"text": "acct 12345676411"}, "server")
    assert scrubbed == {"text": "acct " + "•" * 7 + "6411"}


def test_server_direction_rule_leaves_client_requests_alone(tmp_path: Path) -> None:
    pack = _pack(
        tmp_path,
        '[[rules]]\nid = "t"\nlabel = "T"\nregex = "secret"\ndirection = "server"\n',
    )
    redactor = build_redactor(include_defaults=False, pii_packs=[pack])
    assert redactor.apply_to_payload({"q": "secret"}, "client") == (
        {"q": "secret"},
        False,
    )
    assert not redactor.has_client_rules


def test_text_only_redactor_scrubs_a_bare_string_payload(tmp_path: Path) -> None:
    pack = _pack(
        tmp_path,
        '[[rules]]\nid = "t"\nlabel = "T"\nregex = "secret"\nstrategy = "mask"\n',
    )
    # No structural backend at all, as replay_transform builds it: the text backend
    # alone must still handle a bare string payload.
    redactor = Redactor((), [load_pack_file(pack)], ["regex-pii"])
    assert redactor.apply_to_payload("raw secret line", "server") == (
        "raw " + "•" * 2 + "cret line",
        True,
    )
    assert redactor.apply_to_payload("nothing here", "server") == (
        "nothing here",
        False,
    )


def test_strings_inside_lists_are_scrubbed() -> None:
    payload = {"to": ["alice@example.com", "ops"], "cc": [["bob@example.com"]]}
    scrubbed, changed = build_redactor().apply_to_payload(payload, "client")
    assert changed
    assert isinstance(scrubbed, dict)
    assert scrubbed["to"][0].startswith("<EMAIL>_")
    assert scrubbed["to"][1] == "ops"
    assert scrubbed["cc"][0][0].startswith("<EMAIL>_")
    assert payload["to"][0] == "alice@example.com"  # a deep copy, never in place


def test_replay_needs_no_transform_for_server_only_rules(tmp_path: Path) -> None:
    server_only = _pack(
        tmp_path,
        '[[rules]]\nid = "t"\nlabel = "T"\nregex = "x"\ndirection = "server"\n',
    )
    manifest = build_redactor(
        include_defaults=False, pii_packs=[server_only]
    ).manifest()
    assert replay_transform(manifest, [server_only]) is None

    both = tmp_path / "both.toml"
    both.write_text(
        'version = 1\nid = "b"\n[[rules]]\nid = "t"\nlabel = "T"\nregex = "x"\n',
        encoding="utf-8",
    )
    manifest = build_redactor(include_defaults=False, pii_packs=[both]).manifest()
    transform = replay_transform(manifest, [both])
    assert transform is not None
    assert transform({"q": "x"})["q"].startswith("<T>_")


def test_append_redactor_is_none_for_a_pre_v4_cassette() -> None:
    assert append_redactor(None) is None
    assert replay_transform(None) is None


def test_edited_pack_at_the_recorded_path_is_not_trusted(tmp_path: Path) -> None:
    pack = _pack(tmp_path, '[[rules]]\nid = "t"\nlabel = "T"\nregex = "x"\n')
    manifest = build_redactor(include_defaults=False, pii_packs=[pack]).manifest()
    (resolved,), _ = resolve_packs(manifest)  # found at its recorded path
    assert resolved.path == str(pack)
    pack.write_text(pack.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    with pytest.raises(UnresolvablePackError, match="'team'"):
        resolve_packs(manifest)


def test_replace_with_both_directions_warns_about_collisions(tmp_path: Path) -> None:
    pack = _pack(
        tmp_path,
        '[[rules]]\nid = "cust"\nlabel = "C"\nregex = "c\\\\d+"\n'
        'strategy = "replace"\n',
    )
    with pytest.warns(UserWarning, match=r"team/cust.*'replace'.*'both'"):
        build_redactor(include_defaults=False, pii_packs=[pack])
