"""Redaction never manufactures R005 findings (ITER_02_v4 decision 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.lint import run
from mcp_cassette.redaction import build_redactor

TOKEN = "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"


def _lint_text(tmp_path: Path, *texts: str) -> list[str]:
    messages = [
        Message(
            seq=i,
            t_offset_ms=i,
            sender="server",
            kind="notification",
            method="notifications/message",
            payload={"params": {"data": text}},
        )
        for i, text in enumerate(texts)
    ]
    path = tmp_path / "c.mcp.json"
    Cassette(recorded_at=datetime(2026, 9, 1, tzinfo=UTC), messages=messages).save(path)
    return [f.locator for f in run(path).findings if f.rule == "R005"]


def test_hash_pseudonyms_from_the_bundled_pack_are_silent(tmp_path: Path) -> None:
    scrubbed, _ = build_redactor().apply_to_payload(
        {"text": "mail alice@example.com, bob@example.org, carol@example.net"},
        "server",
    )
    assert isinstance(scrubbed, dict)
    assert "<EMAIL>_" in scrubbed["text"]
    assert _lint_text(tmp_path, scrubbed["text"]) == []


def test_redacted_marker_and_mask_runs_are_silent(tmp_path: Path) -> None:
    assert _lint_text(tmp_path, "REDACTED", "•" * 28 + "6411") == []


def test_genuine_secret_beside_pseudonyms_still_fires(tmp_path: Path) -> None:
    scrubbed, _ = build_redactor().apply_to_payload(
        {"text": f"alice@example.com leaked {TOKEN}"}, "server"
    )
    assert isinstance(scrubbed, dict)
    assert _lint_text(tmp_path, scrubbed["text"]) == ["/messages/0/payload/params/data"]
