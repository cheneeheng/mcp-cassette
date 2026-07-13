"""Cross-process session reports.

The pytest fixture spawns record/replay engines as subprocesses (the agent under test
runs the command), so outcomes like an empty recording or replay misses cannot be read
from a return value. Engines optionally write a small JSON report to a path the fixture
chose; :meth:`~mcp_cassette.session.CassetteSession.finalize` reads it to fail the test
with a clear message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(path: str, data: dict[str, Any]) -> None:
    """Atomically write a session report as JSON.

    Args:
        path: Destination file path.
        data: JSON-serializable report contents.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(target)


def read_report(path: str) -> dict[str, Any] | None:
    """Read a session report, or ``None`` if it does not exist."""
    target = Path(path)
    if not target.exists():
        return None
    data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    return data
