"""Crash-safety checkpoint tests: the sidecar loop and both proxies' snapshot gates.

The loop is driven directly with stub snapshots rather than through a live recording:
what matters is *when* it writes (new messages only), *where* (the ``.partial``
sidecar, never the cassette path), and that finalize clears it. The end-to-end
mid-session behaviour under a hard kill lives in ``tests/integration/test_record.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio
import pytest

from mcp_cassette.cassette import Cassette, Message
from mcp_cassette.record import checkpoint
from mcp_cassette.record.proxy import StdioRecordingProxy
from mcp_cassette.transports.http.proxy import RecordingProxy


def _cassette(count: int) -> Cassette:
    return Cassette(
        recorded_at=datetime.now(UTC),
        messages=[
            Message(
                seq=i,
                t_offset_ms=i,
                sender="client",
                kind="notification",
                method="ping",
                payload={"jsonrpc": "2.0", "method": "ping"},
            )
            for i in range(count)
        ],
    )


def _drive(
    snapshots: list[Cassette | None], cassette_path: Path, interval: float = 0.01
) -> None:
    """Run the loop until ``snapshots`` is exhausted, then cancel it."""
    pending = list(snapshots)

    def snapshot() -> Cassette | None:
        return pending.pop(0) if pending else None

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(checkpoint.run, interval, snapshot, str(cassette_path))
            while pending:
                await anyio.sleep(interval)
            await anyio.sleep(interval * 3)
            tg.cancel_scope.cancel()

    anyio.run(partial(main))


def test_partial_path_is_a_sibling_sidecar(tmp_path: Path) -> None:
    assert checkpoint.partial_path(tmp_path / "demo.json") == (
        tmp_path / "demo.json.partial"
    )


def test_writes_growing_session_to_the_sidecar_not_the_cassette(
    tmp_path: Path,
) -> None:
    cassette_path = tmp_path / "demo.json"
    _drive([_cassette(1), _cassette(3)], cassette_path)

    # The cassette path itself stays untouched: mode="once" resolves record-vs-replay
    # by its existence, so a checkpoint there would replay as a finished recording.
    assert not cassette_path.exists()
    partial_file = checkpoint.partial_path(cassette_path)
    assert Cassette.load(partial_file).messages[-1].seq == 2


def test_skips_rounds_with_no_new_messages(tmp_path: Path) -> None:
    cassette_path = tmp_path / "demo.json"
    partial_file = checkpoint.partial_path(cassette_path)
    writes = 0

    def snapshot() -> Cassette | None:
        return _cassette(2)

    async def main() -> None:
        nonlocal writes
        async with anyio.create_task_group() as tg:
            tg.start_soon(checkpoint.run, 0.01, snapshot, str(cassette_path))
            await anyio.sleep(0.05)
            writes = json.loads(partial_file.read_text(encoding="utf-8"))["messages"]
            mtime = partial_file.stat().st_mtime_ns
            await anyio.sleep(0.05)
            # message count never moved, so no further write happened
            assert partial_file.stat().st_mtime_ns == mtime
            tg.cancel_scope.cancel()

    anyio.run(partial(main))
    assert len(writes) == 2


def test_declined_snapshot_writes_nothing(tmp_path: Path) -> None:
    cassette_path = tmp_path / "demo.json"
    _drive([None, None], cassette_path)
    assert not checkpoint.partial_path(cassette_path).exists()
    assert not cassette_path.exists()


def test_discard_removes_the_sidecar_and_tolerates_its_absence(tmp_path: Path) -> None:
    cassette_path = tmp_path / "demo.json"
    partial_file = checkpoint.partial_path(cassette_path)
    partial_file.write_text("{}", encoding="utf-8")
    checkpoint.discard(cassette_path)
    assert not partial_file.exists()
    checkpoint.discard(cassette_path)  # already gone: no error


def test_stdio_snapshot_declines_until_something_is_recorded(tmp_path: Path) -> None:
    proxy = StdioRecordingProxy(
        server_cmd=["unused"], cassette_path=str(tmp_path / "c.json")
    )
    assert proxy._snapshot() is None  # noqa: SLF001
    proxy._recorder.on_message(  # noqa: SLF001
        "client", json.dumps({"jsonrpc": "2.0", "method": "ping"})
    )
    snapshot = proxy._snapshot()  # noqa: SLF001
    assert snapshot is not None
    assert len(snapshot.messages) == 1


def test_stdio_finalize_clears_the_sidecar(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    checkpoint.partial_path(cassette_path).write_text("{}", encoding="utf-8")
    proxy = StdioRecordingProxy(server_cmd=["unused"], cassette_path=str(cassette_path))
    proxy._finalize()  # noqa: SLF001
    assert cassette_path.exists()
    assert not checkpoint.partial_path(cassette_path).exists()


def test_http_snapshot_waits_for_a_reachable_upstream(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )
    proxy._recorder.on_message(  # noqa: SLF001
        "client", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    )
    # Traffic exists but the upstream has not answered yet: writing now could leave a
    # sidecar for a session that turns out to be a first-contact failure.
    assert proxy._snapshot() is None  # noqa: SLF001
    proxy._upstream_ok = True  # noqa: SLF001
    proxy._session_id = "sid-1"  # noqa: SLF001
    snapshot = proxy._snapshot()  # noqa: SLF001
    assert snapshot is not None
    assert snapshot.transport == "http"
    assert snapshot.session_id == "sid-1"


def test_http_fatal_finalize_clears_the_sidecar(tmp_path: Path) -> None:
    cassette_path = tmp_path / "c.json"
    partial_file = checkpoint.partial_path(cassette_path)
    partial_file.write_text("{}", encoding="utf-8")
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp", cassette_path=str(cassette_path)
    )
    proxy._fatal = "cannot reach upstream"  # noqa: SLF001
    proxy.finalize()
    assert not cassette_path.exists()
    assert not partial_file.exists()


@pytest.mark.parametrize("interval", [None, 0])
def test_interval_off_starts_no_checkpoint_task(
    tmp_path: Path, interval: float | None
) -> None:
    cassette_path = tmp_path / "c.json"
    proxy = RecordingProxy(
        server_url="http://127.0.0.1:9/mcp",
        cassette_path=str(cassette_path),
        checkpoint_interval=interval,
    )

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            await tg.start(proxy.serve)
            proxy._recorder.on_message(  # noqa: SLF001
                "client", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            )
            proxy._upstream_ok = True  # noqa: SLF001
            await anyio.sleep(0.1)
            assert not checkpoint.partial_path(cassette_path).exists()
            tg.cancel_scope.cancel()

    anyio.run(partial(main))
