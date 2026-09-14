"""Cassette schema v3 (SKELETON_v4 §02): v1/v2/v3 load, manifest round-trips."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import (
    FORMAT_VERSION,
    Cassette,
    Message,
    PackRef,
    RedactionManifest,
    UnsupportedFormatVersion,
)

RECORDED = datetime(2026, 9, 1, tzinfo=UTC)


def _write(path: Path, data: dict[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("version", [1, 2])
def test_older_cassettes_load_without_a_manifest(tmp_path: Path, version: int) -> None:
    path = _write(
        tmp_path / "old.mcp.json",
        {
            "format_version": version,
            "recorded_at": RECORDED.isoformat(),
            "messages": [],
        },
    )
    cassette = Cassette.load(path)
    assert cassette.redaction is None


def test_manifest_and_unclassified_message_round_trip(tmp_path: Path) -> None:
    manifest = RedactionManifest(
        profile="team-baseline",
        backends=["structural", "regex-pii"],
        packs=[PackRef(id="common", path="builtin:common", sha256="ab" * 32)],
        rule_ids=["common/email"],
        applied_at=RECORDED,
    )
    cassette = Cassette(
        recorded_at=RECORDED,
        redaction=manifest,
        messages=[
            Message(
                seq=0,
                t_offset_ms=0,
                sender="server",
                kind="unclassified",
                payload={"orphan": True},
            )
        ],
    )
    cassette.save(tmp_path / "c.mcp.json")
    on_disk = json.loads((tmp_path / "c.mcp.json").read_text(encoding="utf-8"))
    assert on_disk["format_version"] == FORMAT_VERSION == 3
    assert Cassette.load(tmp_path / "c.mcp.json") == cassette.model_copy(
        update={"format_version": 3}
    )


def test_newer_format_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "new.mcp.json",
        {"format_version": 4, "recorded_at": RECORDED.isoformat()},
    )
    with pytest.raises(UnsupportedFormatVersion):
        Cassette.load(path)


def test_save_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    Cassette(recorded_at=RECORDED).save(tmp_path / "c.mcp.json")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.mcp.json"]
