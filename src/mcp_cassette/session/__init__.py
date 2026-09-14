"""Per-test cassette session: mode resolution, command building, finalization.

The fixture does not monkeypatch the agent. It hands the test a *command list* to plug
into the agent's MCP server configuration: in record mode the command is the recording
proxy wrapping the real server; in replay mode it is ``mcp-cassette serve``. Command
substitution is the whole trick, which keeps any MCP client unmodified.

The same machinery is pytest-free, so :func:`use_cassette` opens the third front door:
plain Python code — an agent harness, a notebook, a benchmark runner — gets a session
with the same modes, the same fault matrix, and the same failure semantics.
:func:`use_cassette_async` is the fourth, for async callers: its HTTP server runs as a
task in the caller's own event loop rather than on a portal thread.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal, get_args

import anyio
import anyio.abc
import anyio.lowlevel

from ..cassette import (
    Cassette,
    Fault,
    FaultOverlay,
    MatchConfig,
    PaceConfig,
    UnsupportedFormatVersion,
)
from ..redaction.redactor import Redactor
from ..redaction.replay import RequestTransform, append_redactor, replay_transform
from ..report import read_report
from .claim import DEFAULT_CLAIM_WAIT, ClaimConflict, ClaimFile

Mode = Literal["once", "none", "all", "new_episodes"]
_Action = Literal["record", "replay", "new_episodes"]

VALID_MODES: tuple[str, ...] = get_args(Mode)
"""The four accepted record modes, shared by every front door."""


class CassetteError(Exception):
    """Raised for a cassette-session violation; surfaced as a test failure."""


def resolve_mode(explicit: str | None = None) -> Mode:
    """Resolve the record mode for a non-pytest caller.

    Precedence is ``MCP_CASSETTE_MODE`` > ``explicit`` > ``"once"``. The environment
    stays the top tier so the CI invariant (``MCP_CASSETTE_MODE=none`` forbids
    recording) holds through the library door too — a harness cannot silently record
    in CI by hard-coding ``mode="all"``. The environment is read on every call and
    never cached, so ``monkeypatch.setenv`` behaves.

    Args:
        explicit: The caller-supplied mode, if any.

    Returns:
        The resolved mode.

    Raises:
        ValueError: If either source names a mode that does not exist.
    """
    # The argument is validated before the environment is consulted. Otherwise a typo
    # raises on a developer's machine (no env set) but is swallowed in CI, where
    # MCP_CASSETTE_MODE=none is the standing invariant — the one environment that
    # would have told you stays quiet. Which mode resolves never changes.
    validated = (
        _validate_mode(explicit, "mode= argument") if explicit is not None else None
    )
    env = os.environ.get("MCP_CASSETTE_MODE")
    if env:
        return _validate_mode(env, "env MCP_CASSETTE_MODE")
    return validated or "once"


def _validate_mode(value: str, source: str) -> Mode:
    if value not in VALID_MODES:
        raise ValueError(
            f"invalid mcp_cassette mode {value!r} from {source}; "
            f"expected one of {VALID_MODES}"
        )
    return value  # type: ignore[return-value]


@contextmanager
def use_cassette(
    cassette: str | os.PathLike[str],
    *,
    mode: str | None = None,
    match: MatchConfig | None = None,
    faults: FaultOverlay | None = None,
    pace: PaceConfig | None = None,
    report_path: str | os.PathLike[str] | None = None,
    pii_packs: Sequence[str | os.PathLike[str]] | None = None,
) -> Iterator[CassetteSession]:
    """Record/replay an MCP session from plain Python code.

    The library front door. Plug :meth:`CassetteSession.server_command` (stdio) or
    :meth:`CassetteSession.server_url` (Streamable HTTP) into the agent's MCP server
    configuration inside the block; a clean exit calls :meth:`CassetteSession.finalize`.

    If the block raises, the session is closed (no thread or socket leaks) and the
    original exception propagates untouched — report checks are skipped, because a
    replay miss is usually a *consequence* of the real failure and chaining it on top
    would bury the cause.

    Two blocks may be open at once for different cassettes. Two sessions *writing* the
    same cassette path are detected: a recording session holds an advisory claim, so the
    second one waits (``once``) or raises :class:`CassetteError` (``all``,
    ``new_episodes``). Replaying one cassette from many sessions is always safe.

    Args:
        cassette: Path to this session's cassette.
        mode: Record mode; see :func:`resolve_mode` for precedence.
        match: Matching configuration for replay.
        faults: Optional fault overlay (replay only).
        pace: Optional replay pacing configuration.
        report_path: Where to write the cross-process session report. Defaults to a
            temporary directory removed on exit, so library callers do not find
            untracked JSON next to cassettes they commit.
        pii_packs: Redaction pack files: applied when recording, and used to resolve
            the packs a recorded cassette's manifest names when replaying.

    Yields:
        The :class:`CassetteSession` for this block.

    Raises:
        CassetteError: On a clean exit whose session recorded nothing or hit a
            replay miss.
        ValueError: If ``mode`` (or the environment) names an unknown mode.
        RuntimeError: If called from inside a running event loop, where the blocking
            portal deadlocks; use :func:`use_cassette_async` there.
    """
    _refuse_running_loop()
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None
    if report_path is None:
        tmp_dir = tempfile.TemporaryDirectory(prefix="mcp-cassette-")
        report_path = Path(tmp_dir.name) / "report.json"
    session = CassetteSession(
        mode=resolve_mode(mode),
        cassette_path=Path(cassette),
        match=match,
        faults=faults,
        pace=pace,
        report_path=Path(report_path),
        pii_packs=pii_packs,
    )
    try:
        yield session
    except BaseException:
        session.close()
        raise
    else:
        session.finalize()
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


def _refuse_running_loop() -> None:
    # current_token() returns only on an event-loop thread; a worker thread, where the
    # sync door stays legal, raises NoEventLoopError like plain synchronous code.
    try:
        anyio.lowlevel.current_token()
    except anyio.NoEventLoopError:
        return
    raise RuntimeError(
        "use_cassette() was called from inside a running event loop, where its "
        "blocking portal deadlocks; use 'async with use_cassette_async(...)' instead, "
        "or call use_cassette() from a worker thread"
    )


@asynccontextmanager
async def use_cassette_async(
    cassette: str | os.PathLike[str],
    *,
    mode: str | None = None,
    match: MatchConfig | None = None,
    faults: FaultOverlay | None = None,
    pace: PaceConfig | None = None,
    report_path: str | os.PathLike[str] | None = None,
    pii_packs: Sequence[str | os.PathLike[str]] | None = None,
) -> AsyncIterator[CassetteSession]:
    """Record/replay an MCP session from async code, with no portal thread.

    The async twin of :func:`use_cassette`, with the same arguments, modes, and
    failure semantics. :meth:`CassetteSession.server_url` starts its HTTP server as a
    task in a task group this block owns, inside the caller's event loop, so there is
    no thread hop and nothing to join at shutdown. Works under asyncio and trio.
    :meth:`CassetteSession.server_command` is unchanged: a stdio server is a program
    the client launches, so it is still a command list.

    A recording session takes its write claim on entry, waiting with ``anyio.sleep``
    so a contended claim never blocks the loop. Cleanup is shielded from cancellation,
    so a cancelled body still stops the server and releases the claim. If the body
    raises, the original exception propagates untouched and report checks are
    skipped.

    Args:
        cassette: Path to this session's cassette.
        mode: Record mode; see :func:`resolve_mode` for precedence.
        match: Matching configuration for replay.
        faults: Optional fault overlay (replay only).
        pace: Optional replay pacing configuration.
        report_path: Where to write the session report. Defaults to a temporary
            directory removed on exit.
        pii_packs: Redaction pack files, as for :func:`use_cassette`.

    Yields:
        The :class:`CassetteSession` for this block.

    Raises:
        CassetteError: On a clean exit whose session recorded nothing or hit a
            replay miss, or when another live process holds the cassette for writing.
        ValueError: If ``mode`` (or the environment) names an unknown mode.
    """
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None
    if report_path is None:
        tmp_dir = tempfile.TemporaryDirectory(prefix="mcp-cassette-")
        report_path = Path(tmp_dir.name) / "report.json"
    session = CassetteSession(
        mode=resolve_mode(mode),
        cassette_path=Path(cassette),
        match=match,
        faults=faults,
        pace=pace,
        report_path=Path(report_path),
        pii_packs=pii_packs,
    )
    error: BaseException | None = None
    try:
        await session._aacquire_claim()
        try:
            async with anyio.create_task_group() as tg:
                session._task_group = tg
                try:
                    yield session
                except BaseException as exc:
                    # Re-raised below, outside the task group: raised inside it, the
                    # caller's exception would arrive wrapped in an ExceptionGroup.
                    error = exc
                tg.cancel_scope.cancel()
        finally:
            # Every server task has now finished, or never started and never will.
            session._detach_loop()
            with anyio.CancelScope(shield=True):
                await session.aclose()
        if error is not None:
            raise error
        await session.afinalize()
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


class CassetteSession:
    """Resolves record/replay behavior and builds the server command for one test."""

    def __init__(
        self,
        mode: Mode,
        cassette_path: Path,
        match: MatchConfig | None = None,
        faults: FaultOverlay | None = None,
        pace: PaceConfig | None = None,
        report_path: Path | None = None,
        pii_packs: Sequence[str | os.PathLike[str]] | None = None,
        node_id: str | None = None,
    ) -> None:
        """Initialize the session.

        Args:
            mode: Resolved record mode (``once``/``none``/``all``/``new_episodes``).
            cassette_path: Path to this test's cassette.
            match: Matching configuration for replay.
            faults: Optional fault overlay (replay only).
            pace: Optional replay pacing configuration (replay only).
            report_path: Path for the cross-process session report; defaults to a
                sibling temp file of the cassette, suffixed with the pytest-xdist
                worker id when one is set, so parallel workers never overwrite each
                other's misses.
            pii_packs: Redaction pack files, for recording and for resolving a
                recorded manifest at replay.
            node_id: The pytest node id, recorded in the write claim so a conflict
                names the test holding it.
        """
        self.mode = mode
        self.cassette_path = cassette_path
        self.match = match or MatchConfig()
        self.faults = faults
        self.pace = pace
        self.pii_packs = [str(pack) for pack in pii_packs or []]
        self.node_id = node_id
        # Read at construction, never at import, so monkeypatch.setenv behaves.
        self.worker = os.environ.get("PYTEST_XDIST_WORKER") or None
        self.claim_wait = DEFAULT_CLAIM_WAIT
        suffix = f".{self.worker}.report.json" if self.worker else ".report.json"
        self.report_path = report_path or cassette_path.with_name(
            cassette_path.name + suffix
        )
        self._claim: ClaimFile | None = None
        self._faults_tmp: tempfile.TemporaryDirectory[str] | None = None
        self._last_action: _Action | None = None
        self._derived: list[CassetteSession] = []
        self._portal_cm: Any = None
        self._portal: Any = None
        self._serve_future: Any = None
        self._http_engine: Any = None
        # Set by use_cassette_async: server_url then serves in this task group.
        self._task_group: anyio.abc.TaskGroup | None = None
        self._in_loop: (
            tuple[anyio.CancelScope, anyio.Event, socket.socket, str] | None
        ) = None

    def with_faults(self, *faults: Fault) -> CassetteSession:
        """Return a copy of this session with the given faults applied.

        The copy is registered on this session, because the pytest fixture finalizes
        the session it handed the test, not the derivative the test actually ran.
        Without the link a fault test's replay misses would go unreported and an
        HTTP server started on the derivative would outlive the test.

        Args:
            *faults: Faults to inject at replay time.

        Returns:
            A new :class:`CassetteSession` (so parametrized tests do not share state).
        """
        overlay = FaultOverlay(faults=list(faults))
        derived = CassetteSession(
            mode=self.mode,
            cassette_path=self.cassette_path,
            match=self.match,
            faults=overlay,
            pace=self.pace,
            report_path=self.report_path,
            pii_packs=self.pii_packs,
            node_id=self.node_id,
        )
        derived._task_group = self._task_group
        self._derived.append(derived)
        return derived

    def server_command(self, real_cmd: list[str]) -> list[str]:
        """Build the MCP server command the agent should launch for this test.

        Args:
            real_cmd: The real MCP server command and arguments.

        Returns:
            The substituted command (recording proxy or replay server).

        Raises:
            CassetteError: If the cassette is missing under ``none`` mode, faults are
                configured under a recording action, the cassette was recorded over
                Streamable HTTP, or another live process holds it for writing.
        """
        action = self._resolve_action()
        if self.faults is not None and action != "replay":
            raise CassetteError(
                "faults apply to replay only; with_faults cannot run under a recording "
                f"mode (resolved action: {action})"
            )
        # Unconditional (ITER_06_v3 F4): skipping it for the record action left the
        # destructive path unguarded — mode=all would replace a committed HTTP
        # recording with a stdio one.
        if self._peek_transport() == "http":
            raise CassetteError(
                f"cassette {self.cassette_path} was recorded over Streamable HTTP; "
                "use mcp_cassette.server_url(real_url) instead of server_command "
                "for http cassettes"
            )
        action = self._claim_for(action)
        self._last_action = action
        base = [sys.executable, "-m", "mcp_cassette"]
        report = ["--report", str(self.report_path)]
        packs = [flag for pack in self.pii_packs for flag in ("--pii-pack", pack)]
        # The session already holds the claim; the child must neither re-take it (it
        # would conflict with its own parent) nor release it.
        held = ["--claim-held-by", str(os.getpid())]
        if action == "record":
            return [
                *base,
                "record",
                "--cassette",
                str(self.cassette_path),
                *report,
                *packs,
                *held,
                "--",
                *real_cmd,
            ]
        # Resolve the recorded redaction here, where a missing pack is a CassetteError
        # naming the fix, rather than an exit 2 inside the agent's server process.
        self._replay_redaction(appending=action == "new_episodes")
        if action == "new_episodes":
            return [
                *base,
                "serve",
                str(self.cassette_path),
                *report,
                *self._match_flags(),
                *self._pace_flags(),
                *packs,
                *held,
                "--new-episodes",
                "--",
                *real_cmd,
            ]
        # replay
        cmd = [
            *base,
            "serve",
            str(self.cassette_path),
            *report,
            *self._match_flags(),
            *self._pace_flags(),
            *packs,
        ]
        if self.faults is not None:
            cmd += ["--faults", self._write_faults()]
        return cmd

    def _write_faults(self) -> str:
        """Serialize this session's overlay where only this session can see it.

        The replay server reads the overlay from a file, so an in-memory
        :meth:`with_faults` overlay has to be written somewhere. That somewhere is a
        private temporary directory, removed in :meth:`close`: the obvious name,
        ``<cassette>.faults.json``, is the one the docs tell users to hand-write for
        the CLI, so deriving it from the cassette path would overwrite a committed
        overlay and then leave the generated one behind next to it.

        Returns:
            Path to the written overlay, as a string for the command line.
        """
        assert self.faults is not None
        if self._faults_tmp is None:
            self._faults_tmp = tempfile.TemporaryDirectory(prefix="mcp-cassette-")
        path = Path(self._faults_tmp.name) / (self.cassette_path.name + ".faults.json")
        path.write_text(self.faults.model_dump_json(indent=2), encoding="utf-8")
        return str(path)

    def server_url(self, real_url: str) -> str:
        """Build the MCP server URL the agent should use for this test.

        The HTTP analog of :meth:`server_command` — URL substitution is the whole
        trick. In record modes the returned URL is a recording proxy in front of
        ``real_url``; in replay modes it is a local replay server rebuilt from the
        cassette; under ``new_episodes`` misses fall through to ``real_url`` live
        and are appended. The server runs in a background thread owned by this
        session and is stopped (and the cassette/report finalized) in
        :meth:`finalize`. Under :func:`use_cassette_async` it runs instead as a task
        in the caller's event loop, and a second call returns the same URL.

        Args:
            real_url: The real remote MCP endpoint (recorded for provenance).

        Returns:
            The local ``http://127.0.0.1:<port>/mcp`` URL to plug into the agent's
            MCP server configuration.

        Raises:
            CassetteError: If the cassette is missing under ``none`` mode, faults
                are configured under a recording action, the cassette was recorded
                over stdio, the ``[http]`` extra is not installed, or another live
                process holds the cassette for writing.
        """
        if self._in_loop is not None:
            return self._in_loop[3]
        action = self._resolve_action()
        if self.faults is not None and action != "replay":
            raise CassetteError(
                "faults apply to replay only; with_faults cannot run under a recording "
                f"mode (resolved action: {action})"
            )
        # Unconditional, mirroring server_command (ITER_06_v3 F4).
        if self._peek_transport() == "stdio":
            raise CassetteError(
                f"cassette {self.cassette_path} was recorded over stdio; use "
                "mcp_cassette.server_command(real_cmd) instead of server_url "
                "for stdio cassettes"
            )
        try:
            from ..transports.http import HttpReplayServer, RecordingProxy
        except ImportError as exc:
            raise CassetteError(str(exc)) from exc
        action = self._claim_for(action)
        self._last_action = action
        if action == "record":
            try:
                engine: Any = RecordingProxy(
                    server_url=real_url,
                    cassette_path=str(self.cassette_path),
                    report_path=str(self.report_path),
                    pii_packs=self.pii_packs,
                )
            except (OSError, ValueError) as exc:
                raise CassetteError(str(exc)) from exc
        elif action == "replay":
            transform, _ = self._replay_redaction()
            engine = HttpReplayServer(
                Cassette.load(self.cassette_path),
                match=self.match,
                faults=self.faults,
                report_path=str(self.report_path),
                pace=self.pace,
                request_transform=transform,
            )
        else:  # new_episodes with an existing cassette
            transform, appender = self._replay_redaction(appending=True)
            engine = HttpReplayServer(
                Cassette.load(self.cassette_path),
                match=self.match,
                report_path=str(self.report_path),
                fallthrough_url=real_url,
                cassette_path=str(self.cassette_path),
                pace=self.pace,
                request_transform=transform,
                append_redactor=appender,
            )
        self._http_engine = engine
        if self._task_group is not None:
            return self._start_in_loop(engine)
        return self._start_background(engine.serve)

    def _start_in_loop(self, engine: Any) -> str:
        """Serve ``engine`` as a task in the caller's task group, with no thread.

        ``server_url`` is synchronous yet must return a URL before that task has run,
        so the listening socket is bound here and handed to the engine.
        """
        assert self._task_group is not None
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
        except OSError:
            sock.close()
            raise
        scope = anyio.CancelScope()
        done = anyio.Event()

        async def run() -> None:
            try:
                with scope:
                    await engine.serve(sock=sock)
            finally:
                sock.close()
                done.set()

        url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
        self._in_loop = (scope, done, sock, url)
        self._task_group.start_soon(run)
        return url

    async def _astop_in_loop(self) -> None:
        for derived in self._derived:
            await derived._astop_in_loop()
        if self._in_loop is None:
            return
        scope, done, sock, _ = self._in_loop
        self._in_loop = None
        scope.cancel()
        # Once the task group has exited, a task that never started never will, so
        # its done event would never fire.
        if self._task_group is not None:
            await done.wait()
        sock.close()

    def _detach_loop(self) -> None:
        self._task_group = None
        for derived in self._derived:
            derived._detach_loop()

    def _start_background(self, serve: Any) -> str:
        from anyio.from_thread import start_blocking_portal

        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        try:
            future, url = self._portal.start_task(serve)
        except BaseException:
            self._stop_background()
            raise
        self._serve_future = future
        return str(url)

    def _stop_background(self) -> None:
        if self._portal_cm is None:
            return
        if self._serve_future is not None:
            self._serve_future.cancel()
            self._serve_future = None
        portal_cm = self._portal_cm
        self._portal_cm = None
        self._portal = None
        portal_cm.__exit__(None, None, None)

    def close(self) -> None:
        """Stop the in-process HTTP server, if one was started; no report checks.

        Idempotent, and a no-op when :meth:`server_url` was never called (the
        background server is started only by an explicit ``server_url()``, never
        lazily, so there is nothing to race against). Sessions derived by
        :meth:`with_faults` are closed too, and any generated fault overlay is
        removed with the temporary directory holding it.
        """
        for derived in self._derived:
            derived.close()
        self._stop_background()
        self._release_claim()
        if self._faults_tmp is not None:
            self._faults_tmp.cleanup()
            self._faults_tmp = None

    def finalize(self) -> None:
        """Close the session, check the report, and raise on violations.

        When :meth:`with_faults` derived sessions from this one, they are the
        sessions that ran, so their reports are the ones checked.

        Raises:
            CassetteError: If a recording captured zero messages (or could not
                reach the upstream at first contact), or replay hit any unmatched
                request.
        """
        self.close()
        self._check_report()

    async def aclose(self) -> None:
        """Stop the in-loop HTTP server and release the claim; no report checks.

        The async peer of :meth:`close`, for sessions opened by
        :func:`use_cassette_async`: stopping the server means cancelling a task and
        waiting for it to finalize, not stopping a portal thread. Idempotent.
        """
        await self._astop_in_loop()
        self.close()

    async def afinalize(self) -> None:
        """:meth:`aclose`, then the same report checks as :meth:`finalize`.

        Raises:
            CassetteError: If a recording captured zero messages (or could not
                reach the upstream at first contact), or replay hit any unmatched
                request.
        """
        await self.aclose()
        self._check_report()

    def _check_report(self) -> None:
        if self._derived:
            for derived in self._derived:
                derived._check_report()
            return
        fatal = getattr(self._http_engine, "fatal_error", None)
        if fatal is not None:
            raise CassetteError(f"recording failed: {fatal}")
        if self._last_action is None:
            return
        report = read_report(str(self.report_path))
        if report is None:
            return
        if self._last_action in ("record",) and report.get("messages", 0) == 0:
            raise CassetteError(
                "recording captured zero messages — agent never spoke to the proxied "
                f"server. Is the command wired in? (cassette: {self.cassette_path})"
            )
        misses = report.get("misses") or []
        if misses:
            summary = "\n".join(f"  - {m}" for m in misses)
            raise CassetteError(
                f"replay had {len(misses)} unmatched request(s):\n{summary}\n"
                f"Re-record with MCP_CASSETTE_MODE=all or delete {self.cassette_path}."
            )

    def _replay_redaction(
        self, *, appending: bool = False
    ) -> tuple[RequestTransform | None, Redactor | None]:
        """Resolve the cassette's recorded redaction for replay.

        Args:
            appending: Also build the redactor for ``new_episodes`` appends.

        Returns:
            ``(request_transform, append_redactor)``; both ``None`` for a cassette
            without a manifest, or one this peek cannot read (the engine's own load
            reports that with its own message).

        Raises:
            CassetteError: If a pack the manifest names cannot be resolved from
                ``pii_packs``, its recorded path, or the bundled packs.
        """
        try:
            manifest = Cassette.load(self.cassette_path).redaction
        except (OSError, ValueError, UnsupportedFormatVersion):
            return None, None
        try:
            transform = replay_transform(manifest, self.pii_packs)
            appender = append_redactor(manifest, self.pii_packs) if appending else None
        except (OSError, ValueError) as exc:
            raise CassetteError(str(exc)) from exc
        return transform, appender

    def _claim_for(self, action: _Action) -> _Action:
        """Hold the single-writer claim for an action that writes.

        Readers never claim. ``once`` waits for another writer, then re-resolves
        against the filesystem as it now stands: the holder may have written the
        cassette (replay) or recorded nothing and written no file (record, holding the
        claim). ``all`` and ``new_episodes`` fail fast — downgrading a deliberate
        re-record to replay, or interleaving two appenders, would silently violate
        what the caller asked for.

        Args:
            action: The action resolved before any claim was taken.

        Returns:
            The action to run.

        Raises:
            CassetteError: If another live process holds the claim.
        """
        if action == "replay":
            return action
        if self._claim is None:
            claim = self._new_claim()
            try:
                claim.acquire()
            except ClaimConflict as exc:
                raise CassetteError(str(exc)) from exc
            self._claim = claim
        return self._settle()

    async def _aacquire_claim(self) -> None:
        """Take the write claim up front for :func:`use_cassette_async`.

        Waits with ``anyio.sleep``: a 30-second blocking sleep inside the caller's
        event loop would stall every other task in their harness. The later
        ``_claim_for`` then finds the claim already held.

        Raises:
            CassetteError: If another live process still holds the claim.
        """
        if self.mode == "none" or self._resolve_action() == "replay":
            return
        claim = self._new_claim()
        try:
            await claim.aacquire()
        except ClaimConflict as exc:
            raise CassetteError(str(exc)) from exc
        self._claim = claim
        self._settle()

    def _new_claim(self) -> ClaimFile:
        return ClaimFile(
            self.cassette_path,
            self.mode,
            wait=self.claim_wait if self.mode == "once" else 0.0,
            node_id=self.node_id,
            worker=self.worker,
        )

    def _settle(self) -> _Action:
        settled = self._resolve_action()
        if settled == "replay":
            self._release_claim()
        return settled

    def _release_claim(self) -> None:
        if self._claim is not None:
            self._claim.release()
            self._claim = None

    def _peek_transport(self) -> str | None:
        """The existing cassette's transport; ``None`` when absent or unreadable."""
        try:
            return Cassette.load(self.cassette_path).transport
        except (OSError, ValueError, UnsupportedFormatVersion):
            # Best-effort peek: an absent or unreadable cassette trips neither
            # transport guard, and the real load reports the problem with its own
            # message rather than tracebacking out of server_command().
            return None

    def _resolve_action(self) -> _Action:
        exists = self.cassette_path.exists()
        if self.mode == "once":
            return "replay" if exists else "record"
        if self.mode == "none":
            if not exists:
                raise CassetteError(
                    f"no cassette at {self.cassette_path} and recording is forbidden "
                    "(mode=none). Record one first with MCP_CASSETTE_MODE=once."
                )
            return "replay"
        if self.mode == "all":
            return "record"
        # new_episodes
        return "new_episodes" if exists else "record"

    def _pace_flags(self) -> list[str]:
        # Emitted only for mode="recorded": the scale/cap flags are rejected by the
        # CLI without it, and "none" is already the default.
        if self.pace is None or self.pace.mode != "recorded":
            return []
        return [
            "--pace",
            "recorded",
            "--pace-scale",
            str(self.pace.scale),
            "--pace-cap-ms",
            str(self.pace.cap_ms),
        ]

    def _match_flags(self) -> list[str]:
        flags = ["--ordering", self.match.ordering]
        for ptr in self.match.ignore_params:
            flags += ["--ignore-param", ptr]
        if self.match.rewrite_protocol_version:
            flags.append("--rewrite-protocol-version")
        return flags
