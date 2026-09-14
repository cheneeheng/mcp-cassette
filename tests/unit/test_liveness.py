"""Process liveness probe (ITER_04_v4 decision 3)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from mcp_cassette.session.claim import pid_alive


def test_current_process_is_alive() -> None:
    assert pid_alive(os.getpid())


def test_reaped_child_is_not_alive() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not pid_alive(proc.pid)


@pytest.mark.skipif(sys.platform != "win32", reason="os.kill(pid, 0) kills on Windows")
def test_probing_a_live_process_leaves_it_running() -> None:
    # The regression test for decision 3: replacing the Windows branch with
    # os.kill(pid, 0) would TerminateProcess the helper and fail here loudly.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert pid_alive(proc.pid)
        time.sleep(0.3)
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
