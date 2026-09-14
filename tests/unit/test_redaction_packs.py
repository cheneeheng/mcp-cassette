"""Bundled PII pack coverage and redaction-pack validation (ITER_01_v4 §04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_cassette.redaction import build_redactor, bundled_packs, load_pack

PLANTED = {
    "email": ("contact alice@example.com today", "contact alice@example today"),
    "phone": ("call +14155550123 now", "call +0123 now"),
    "ipv4": ("host 10.0.0.12 up", "host 10.0.0.256 up"),
    "ipv6": ("addr 2001:db8::8a2e:370:7334 up", "at 12:30:45 up"),
    "ssn": ("ssn 123-45-6789 on file", "ssn 000-45-6789 on file"),
    "iban": ("iban DE89 3704 0044 0532 0130 00 ok", "iban DE00 1234 5678 9012 ok"),
    "card": ("card 4111 1111 1111 1111 ok", "card 4111 1111 1111 1112 ok"),
    "aws": ("key AKIAIOSFODNN7EXAMPLE ok", "key AKIAiosfodnn7example ok"),
    "pem": ("-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PUBLIC KEY-----"),
    "jwt": (
        "bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl",
        "header eyJhbGciOiJIUzI1NiJ9 only",
    ),
}


def _scrub(text: str) -> str:
    scrubbed, _ = build_redactor().apply_to_payload({"text": text}, "server")
    assert isinstance(scrubbed, dict)
    return str(scrubbed["text"])


@pytest.mark.parametrize("kind", sorted(PLANTED))
def test_bundled_rule_fires_on_planted_value(kind: str) -> None:
    planted, _ = PLANTED[kind]
    assert _scrub(planted) != planted


@pytest.mark.parametrize("kind", sorted(PLANTED))
def test_bundled_rule_ignores_near_miss(kind: str) -> None:
    # A Luhn-invalid card and a mod-97-invalid IBAN are shape matches only.
    _, near_miss = PLANTED[kind]
    assert _scrub(near_miss) == near_miss


def test_bundled_pack_is_loadable_and_identified() -> None:
    (common,) = bundled_packs()
    assert common.pack.id == "common"
    assert common.path == "builtin:common"
    assert len(common.sha256) == 64


def _pack(
    tmp_path: Path, body: str, *, header: str = 'version = 1\nid = "team"\n'
) -> Path:
    path = tmp_path / "pack.toml"
    path.write_text(header + body, encoding="utf-8")
    return path


RULE = '[[rules]]\nid = "r"\nlabel = "X"\nregex = "x"\n'


def test_valid_pack_loads(tmp_path: Path) -> None:
    pack = load_pack(_pack(tmp_path, RULE))
    assert [rule.id for rule in pack.rules] == ["r"]
    assert pack.rules[0].effective_strategy == "hash"  # direction "both" default


@pytest.mark.parametrize(
    ("body", "header", "needle"),
    [
        (RULE, 'version = 2\nid = "team"\n', "unsupported version"),
        (RULE + "severty = 1\n", None, "severty"),
        ('[[rules]]\nid = "r"\nlabel = "X"\nregex = "("\n', None, "invalid regex"),
        (RULE + RULE, None, "duplicate rule id"),
        (RULE + 'flags = ["q"]\n', None, "unknown regex flag"),
        (RULE, 'version = 1\nid = "9bad"\n', "invalid pack id"),
        ("not = [valid", None, "pack.toml"),
    ],
)
def test_malformed_pack_names_file_and_key(
    tmp_path: Path, body: str, header: str | None, needle: str
) -> None:
    path = (
        _pack(tmp_path, body, header=header)
        if header is not None
        else _pack(tmp_path, body)
    )
    with pytest.raises(ValueError, match=needle) as excinfo:
        load_pack(path)
    assert str(path) in str(excinfo.value)


def test_duplicate_pack_id_is_rejected(tmp_path: Path) -> None:
    path = _pack(tmp_path, RULE, header='version = 1\nid = "common"\n')
    with pytest.raises(ValueError, match="loaded twice"):
        build_redactor(pii_packs=[path])
