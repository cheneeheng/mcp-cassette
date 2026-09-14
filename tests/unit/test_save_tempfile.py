"""Unique temp names on the cassette write path (ITER_04_v4 §04)."""

from __future__ import annotations

import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette.cassette import Cassette

RECORDED = datetime(2026, 9, 1, tzinfo=UTC)


def test_concurrent_saves_into_one_directory_never_collide(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def writer(name: str) -> None:
        try:
            for _ in range(25):
                Cassette(recorded_at=RECORDED).save(tmp_path / name)
        except BaseException as exc:  # noqa: BLE001 — surfaced by the assert below
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(name,))
        for name in ("a.mcp.json", "b.mcp.json")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    for name in ("a.mcp.json", "b.mcp.json"):
        assert Cassette.load(tmp_path / name).recorded_at == RECORDED
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.mcp.json", "b.mcp.json"]


def test_temp_file_lives_in_the_destination_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def spy(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(Path(name))
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    target = tmp_path / "nested" / "c.mcp.json"
    Cassette(recorded_at=RECORDED).save(target)
    (temp,) = created
    assert temp.parent == target.parent
    assert temp.name != target.name + ".tmp"
    assert not temp.exists()
