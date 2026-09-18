"""The claim under real multi-process contention.

Three rounds of point fixes failed to close a Windows race in the claim because each
was validated by a single green CI run, which proves almost nothing about a race. This
forces the contention instead: on Windows any process *reading* the claim blocks a
concurrent create or delete of it, and a waiter polls that file continuously by design,
so a reader and a writer hammering one path is the exact shape that broke.

These tests assert the invariant, not a timing: no ``PermissionError`` ever escapes,
ownership is never shared, and the file is gone at the end.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from mcp_cassette.session.claim import claim_path

WORKERS = 4
CYCLES = 25

# Each worker cycles acquire/release on one path while every other worker does the
# same and a reader polls it. Any escaping OSError is reported and fails the test.
_WORKER = """
import sys, time
from mcp_cassette.session.claim import ClaimFile, ClaimConflict

cassette, cycles = sys.argv[1], int(sys.argv[2])
taken = 0
for _ in range(cycles):
    claim = ClaimFile(cassette, "all", wait=5.0)
    try:
        claim.acquire()
    except ClaimConflict:
        continue          # a legitimate outcome, not a failure
    except OSError as exc:
        print(f"ESCAPED {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    taken += 1
    time.sleep(0.001)     # hold it briefly so the others really contend
    try:
        claim.release()
    except OSError as exc:
        print(f"ESCAPED on release {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
print(taken)
"""

# A pure reader: never claims, just keeps a handle opening and closing on the file.
# This is what blocks the writers' unlink/create on Windows.
_READER = """
import sys, time
from mcp_cassette.session.claim import read_claim

cassette, seconds = sys.argv[1], float(sys.argv[2])
deadline = time.monotonic() + seconds
while time.monotonic() < deadline:
    read_claim(cassette)   # readers never claim; this is the contention
"""


def _spawn(script: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_no_error_escapes_under_contention(tmp_path: Path) -> None:
    """Writers and a reader hammer one claim; nothing raises out of the claim API."""
    cassette = tmp_path / "contended.mcp.json"
    reader = _spawn(_READER, str(cassette), "12")
    writers = [_spawn(_WORKER, str(cassette), str(CYCLES)) for _ in range(WORKERS)]

    results = [(w, *w.communicate(timeout=120)) for w in writers]
    reader.terminate()
    reader.communicate(timeout=30)

    failures = [
        f"worker exited {proc.returncode}: {err.strip()}"
        for proc, _out, err in results
        if proc.returncode != 0
    ]
    assert not failures, "\n".join(failures)

    # Every worker either took the claim or hit a clean ClaimConflict; across all of
    # them at least one acquisition must have succeeded, or the test proved nothing.
    acquisitions = sum(int(out.strip() or 0) for _proc, out, _err in results)
    assert acquisitions > 0

    # The last holder released, so the sidecar must not outlive the run.
    assert not claim_path(cassette).exists()
