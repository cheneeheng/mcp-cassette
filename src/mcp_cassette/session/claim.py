"""Advisory single-writer claim over a cassette path.

Two writers on one cassette path lose a recording: the last ``os.replace`` wins and the
other session vanishes. The claim is a ``<cassette>.claim`` sidecar created with
``O_EXCL`` — there is no window between "does it exist" and "create it" — holding a
:class:`ClaimRecord` that names the writer. Readers never take one: concurrent replay
against one cassette is safe.

Advisory and bounded: a wait ends in :class:`ClaimConflict`, never a hang. Stale claims
are reclaimed on either of two independent conditions (holder pid not alive, or claim
older than the TTL), because pids are reused and long recordings are legitimate.
Reliable local ``O_EXCL`` is assumed; an NFS-safe protocol is out of scope.
"""

from __future__ import annotations

import os
import secrets
import socket
import sys
import time
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import anyio
from pydantic import BaseModel, ValidationError

DEFAULT_CLAIM_WAIT = 30.0
"""Seconds a ``once`` session waits for another writer before giving up."""

DEFAULT_CLAIM_TTL = 14400.0
"""Seconds after which a claim is stale whatever its holder's liveness (4 hours)."""

_POLL_SECONDS = 0.1
_CORRUPT_GRACE_SECONDS = 5.0
_SHARING_RETRY_SECONDS = 2.0
_SHARING_POLL_SECONDS = 0.02  # not _POLL_SECONDS: never resonate with a waiter's poll


def _despite_sharing[T](op: Callable[[], T], default: T | None = None) -> T | None:
    """Run one claim-file mutation, retrying while a peer's open handle blocks it.

    On Windows a process merely *reading* the claim blocks a concurrent create or
    delete of it (``ERROR_SHARING_VIOLATION`` / ``ERROR_ACCESS_DENIED``, both
    :class:`PermissionError`), and a waiter polls that file continuously by design —
    so every mutation here is exposed, not just the one a given traceback names.

    Args:
        op: The mutation to attempt.
        default: Returned when the contention outlasts the retry window.

    Returns:
        ``op()``'s result, or ``default`` if it never got through.

    Raises:
        PermissionError: On POSIX, where this is a real permission problem on the
            directory rather than a sharing rule, and must surface.
    """
    deadline = time.monotonic() + _SHARING_RETRY_SECONDS
    while True:
        try:
            return op()
        except PermissionError:
            if sys.platform != "win32":  # pragma: no cover — POSIX passthrough
                raise
            if time.monotonic() >= deadline:
                # Giving up beats failing the run: a claim that outlives its holder
                # is reclaimed by the next acquirer on pid liveness or the TTL.
                return default
            time.sleep(_SHARING_POLL_SECONDS)


class ClaimRecord(BaseModel):
    """Who holds a cassette for writing — a sidecar, never written into a cassette.

    ``token`` is random per acquisition. It is what makes ownership exact: two claims
    taken by one process can carry identical pid, host, and (on a coarse clock)
    timestamp, and a holder must never release a claim someone else has since taken.
    """

    pid: int
    host: str
    node_id: str | None = None
    worker: str | None = None
    mode: str
    created_at: datetime
    token: str | None = None


class ClaimConflict(Exception):  # noqa: N818 — reads as the condition, like CassetteError
    """Another live process holds the cassette for writing."""

    def __init__(self, message: str, holder: ClaimRecord) -> None:
        """Keep the holder so callers can report it."""
        super().__init__(message)
        self.holder = holder


def claim_path(cassette_path: str | os.PathLike[str]) -> Path:
    """The ``<cassette>.claim`` sidecar path for a cassette."""
    target = Path(cassette_path)
    return target.with_name(target.name + ".claim")


def read_claim(cassette_path: str | os.PathLike[str]) -> ClaimRecord | None:
    """The current claim on a cassette, or ``None`` when unclaimed or unreadable."""
    try:
        return ClaimRecord.model_validate_json(
            claim_path(cassette_path).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def pid_alive(pid: int) -> bool:
    """Whether a process id refers to a running process on this host.

    On Windows this must not use ``os.kill(pid, 0)``: CPython implements only
    ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` there and otherwise calls
    ``TerminateProcess`` — the "probe" would kill a colleague's recording with exit
    code 0.
    """
    if sys.platform == "win32":  # pragma: no cover — exercised on the Windows CI leg
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists; we merely may not signal it
    return True


if sys.platform == "win32":  # pragma: no cover — exercised on the Windows CI leg
    import ctypes
    from ctypes import wintypes

    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x00000102
    _ERROR_ACCESS_DENIED = 5

    def _pid_alive_windows(pid: int) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            # Access denied means the process exists but belongs to someone else.
            return bool(ctypes.get_last_error() == _ERROR_ACCESS_DENIED)
        try:
            # Signaled = exited; an exited process can linger while handles are open.
            return bool(kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
        finally:
            kernel32.CloseHandle(handle)


class ClaimFile:
    """An advisory, bounded single-writer claim; usable as a context manager."""

    def __init__(
        self,
        cassette_path: str | os.PathLike[str],
        mode: str,
        wait: float = DEFAULT_CLAIM_WAIT,
        claim_ttl: float = DEFAULT_CLAIM_TTL,
        force: bool = False,
        *,
        node_id: str | None = None,
        worker: str | None = None,
    ) -> None:
        """Describe the claim; nothing is created until :meth:`acquire`.

        Args:
            cassette_path: The cassette to claim.
            mode: The record mode, recorded for whoever hits the conflict.
            wait: Seconds to wait for a live holder to release (``0`` fails fast).
            claim_ttl: Seconds after which any claim is stale.
            force: Break a live claim (with a warning) instead of waiting.
            node_id: The pytest node id holding the claim, when there is one.
            worker: The pytest-xdist worker id holding the claim, when there is one.
        """
        self.cassette_path = Path(cassette_path)
        self.path = claim_path(cassette_path)
        self.mode = mode
        self.wait = wait
        self.claim_ttl = claim_ttl
        self.force = force
        self.node_id = node_id
        self.worker = worker
        self._record: ClaimRecord | None = None

    @property
    def held(self) -> bool:
        """Whether this object currently holds the claim."""
        return self._record is not None

    def __enter__(self) -> ClaimFile:
        """Acquire on entry."""
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Release on exit."""
        self.release()

    def acquire(self) -> None:
        """Take the claim, waiting up to ``wait`` seconds for a live holder.

        Raises:
            ClaimConflict: If a live holder still has it when the wait runs out.
        """
        deadline = time.monotonic() + self.wait
        while True:
            holder = self._attempt()
            if holder is None:
                return
            if time.monotonic() >= deadline:
                raise ClaimConflict(self._conflict_message(holder), holder)
            time.sleep(_POLL_SECONDS)

    async def aacquire(self) -> None:
        """Like :meth:`acquire`, waiting with ``anyio.sleep``.

        A blocking sleep inside a caller's event loop would stall every other task in
        their harness — fixing one concurrency bug by introducing another.

        Raises:
            ClaimConflict: If a live holder still has it when the wait runs out.
        """
        deadline = time.monotonic() + self.wait
        while True:
            holder = self._attempt()
            if holder is None:
                return
            if time.monotonic() >= deadline:
                raise ClaimConflict(self._conflict_message(holder), holder)
            await anyio.sleep(_POLL_SECONDS)

    def release(self) -> None:
        """Remove the claim if this object still owns it. Idempotent.

        A claim another writer broke with ``force`` is theirs now and is left alone.
        """
        if self._record is None:
            return
        if read_claim(self.cassette_path) == self._record:
            # The most important of the three: a release that fails leaves a live
            # claim on disk, and a waiter polling it blocks for its whole wait.
            _despite_sharing(lambda: self.path.unlink(missing_ok=True))
        self._record = None

    def _attempt(self) -> ClaimRecord | None:
        """One acquisition round; ``None`` means this object now holds the claim."""
        holder: ClaimRecord | None = None
        for _ in range(3):  # a vanished or reclaimed claim is retried at once
            if self._create():
                return None
            holder = self._current_holder()
            if holder is None:
                continue
            reason = self._break_reason(holder)
            if reason is None:
                return holder
            warnings.warn(
                f"mcp-cassette: reclaiming the claim on {self.cassette_path}: {reason}",
                stacklevel=4,
            )
            _despite_sharing(lambda: self.path.unlink(missing_ok=True))
        return holder or self._current_holder() or self._placeholder(time.time())

    def _create(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = ClaimRecord(
            pid=os.getpid(),
            host=socket.gethostname(),
            node_id=self.node_id,
            worker=self.worker,
            mode=self.mode,
            created_at=datetime.now(UTC),
            token=secrets.token_hex(8),
        )
        try:
            # Windows reports a contended claim as ERROR_ACCESS_DENIED rather than
            # EEXIST, so _despite_sharing waits it out; None means it never got
            # through, which is "we did not create it" — the same as EEXIST here.
            fd = _despite_sharing(
                lambda: os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            )
        except FileExistsError:
            return False
        if fd is None:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(record.model_dump_json())
        self._record = record
        return True

    def _current_holder(self) -> ClaimRecord | None:
        record = read_claim(self.cassette_path)
        if record is not None:
            return record
        try:
            modified = self.path.stat().st_mtime
        except OSError:
            return None  # released between our create and this read
        return self._placeholder(modified)

    @staticmethod
    def _placeholder(modified: float) -> ClaimRecord:
        # A claim file that exists but does not parse: mid-write, or corrupt.
        return ClaimRecord(
            pid=0,
            host="",
            mode="unknown",
            created_at=datetime.fromtimestamp(modified, UTC),
        )

    def _break_reason(self, holder: ClaimRecord) -> str | None:
        age = (datetime.now(UTC) - holder.created_at).total_seconds()
        who = f"pid {holder.pid} on {holder.host}"
        if holder.pid == 0:
            if age > _CORRUPT_GRACE_SECONDS:
                return f"the claim file is unreadable and {age:.0f}s old"
            return None
        if self.force:
            return f"--force breaks the live claim of {who}, {age:.0f}s old"
        if age > self.claim_ttl:
            return f"{who} took it {age:.0f}s ago, past the {self.claim_ttl:.0f}s TTL"
        # A remote pid says nothing about liveness here, so another host's claim is
        # only ever aged out.
        if holder.host == socket.gethostname() and not pid_alive(holder.pid):
            return f"holder {who} is not alive (claim {age:.0f}s old)"
        return None

    def _conflict_message(self, holder: ClaimRecord) -> str:
        age = (datetime.now(UTC) - holder.created_at).total_seconds()
        who = holder.worker or "another process"
        if holder.node_id:
            who += f" [{holder.node_id}]"
        return (
            f"cassette {self.cassette_path} is held for writing by {who} (pid "
            f"{holder.pid} on {holder.host or 'unknown host'}, started {age:.0f}s "
            f"ago); waited {self.wait:g}s. Fixes: run with -n0, give each test its "
            "own cassette path, or --force to break the claim."
        )
