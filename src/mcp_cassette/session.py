"""Per-test cassette session: mode resolution, command building, finalization.

The fixture does not monkeypatch the agent. It hands the test a *command list* to plug
into the agent's MCP server configuration: in record mode the command is the recording
proxy wrapping the real server; in replay mode it is ``mcp-cassette serve``. Command
substitution is the whole trick, which keeps any MCP client unmodified.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from .cassette import Cassette, Fault, FaultOverlay, MatchConfig
from .report import read_report

Mode = Literal["once", "none", "all", "new_episodes"]
_Action = Literal["record", "replay", "new_episodes"]


class CassetteError(Exception):
    """Raised for a cassette-session violation; surfaced as a test failure."""


class CassetteSession:
    """Resolves record/replay behavior and builds the server command for one test."""

    def __init__(
        self,
        mode: Mode,
        cassette_path: Path,
        match: MatchConfig | None = None,
        faults: FaultOverlay | None = None,
        report_path: Path | None = None,
    ) -> None:
        """Initialize the session.

        Args:
            mode: Resolved record mode (``once``/``none``/``all``/``new_episodes``).
            cassette_path: Path to this test's cassette.
            match: Matching configuration for replay.
            faults: Optional fault overlay (replay only).
            report_path: Path for the cross-process session report; defaults to a
                sibling temp file of the cassette.
        """
        self.mode = mode
        self.cassette_path = cassette_path
        self.match = match or MatchConfig()
        self.faults = faults
        self.report_path = report_path or cassette_path.with_name(
            cassette_path.name + ".report.json"
        )
        self._faults_path = self.report_path.parent / (
            cassette_path.name + ".faults.json"
        )
        self._last_action: _Action | None = None
        self._portal_cm: Any = None
        self._portal: Any = None
        self._serve_future: Any = None
        self._http_engine: Any = None

    def with_faults(self, *faults: Fault) -> CassetteSession:
        """Return a copy of this session with the given faults applied.

        Args:
            *faults: Faults to inject at replay time.

        Returns:
            A new :class:`CassetteSession` (so parametrized tests do not share state).
        """
        overlay = FaultOverlay(faults=list(faults))
        return CassetteSession(
            mode=self.mode,
            cassette_path=self.cassette_path,
            match=self.match,
            faults=overlay,
            report_path=self.report_path,
        )

    def server_command(self, real_cmd: list[str]) -> list[str]:
        """Build the MCP server command the agent should launch for this test.

        Args:
            real_cmd: The real MCP server command and arguments.

        Returns:
            The substituted command (recording proxy or replay server).

        Raises:
            CassetteError: If the cassette is missing under ``none`` mode, or faults are
                configured under a recording action.
        """
        action = self._resolve_action()
        self._last_action = action
        if self.faults is not None and action != "replay":
            raise CassetteError(
                "faults apply to replay only; with_faults cannot run under a recording "
                f"mode (resolved action: {action})"
            )
        if action != "record" and self._peek_transport() == "http":
            raise CassetteError(
                f"cassette {self.cassette_path} was recorded over Streamable HTTP; "
                "use mcp_cassette.server_url(real_url) instead of server_command "
                "for http cassettes"
            )
        base = [sys.executable, "-m", "mcp_cassette"]
        report = ["--report", str(self.report_path)]
        if action == "record":
            return [
                *base,
                "record",
                "--cassette",
                str(self.cassette_path),
                *report,
                "--",
                *real_cmd,
            ]
        if action == "new_episodes":
            return [
                *base,
                "serve",
                str(self.cassette_path),
                *report,
                *self._match_flags(),
                "--new-episodes",
                "--",
                *real_cmd,
            ]
        # replay
        cmd = [*base, "serve", str(self.cassette_path), *report, *self._match_flags()]
        if self.faults is not None:
            self._faults_path.write_text(
                self.faults.model_dump_json(indent=2), encoding="utf-8"
            )
            cmd += ["--faults", str(self._faults_path)]
        return cmd

    def server_url(self, real_url: str) -> str:
        """Build the MCP server URL the agent should use for this test.

        The HTTP analog of :meth:`server_command` — URL substitution is the whole
        trick. In record modes the returned URL is a recording proxy in front of
        ``real_url``; in replay modes it is a local replay server rebuilt from the
        cassette; under ``new_episodes`` misses fall through to ``real_url`` live
        and are appended. The server runs in a background thread owned by this
        session and is stopped (and the cassette/report finalized) in
        :meth:`finalize`.

        Args:
            real_url: The real remote MCP endpoint (recorded for provenance).

        Returns:
            The local ``http://127.0.0.1:<port>/mcp`` URL to plug into the agent's
            MCP server configuration.

        Raises:
            CassetteError: If the cassette is missing under ``none`` mode, faults
                are configured under a recording action, the cassette was recorded
                over stdio, or the ``[http]`` extra is not installed.
        """
        action = self._resolve_action()
        self._last_action = action
        if self.faults is not None and action != "replay":
            raise CassetteError(
                "faults apply to replay only; with_faults cannot run under a recording "
                f"mode (resolved action: {action})"
            )
        if action != "record" and self._peek_transport() != "http":
            raise CassetteError(
                f"cassette {self.cassette_path} was recorded over stdio; use "
                "mcp_cassette.server_command(real_cmd) instead of server_url "
                "for stdio cassettes"
            )
        try:
            from .transports.http import HttpReplayServer, RecordingProxy
        except ImportError as exc:
            raise CassetteError(str(exc)) from exc
        if action == "record":
            engine: Any = RecordingProxy(
                server_url=real_url,
                cassette_path=str(self.cassette_path),
                report_path=str(self.report_path),
            )
        elif action == "replay":
            engine = HttpReplayServer(
                Cassette.load(self.cassette_path),
                match=self.match,
                faults=self.faults,
                report_path=str(self.report_path),
            )
        else:  # new_episodes with an existing cassette
            engine = HttpReplayServer(
                Cassette.load(self.cassette_path),
                match=self.match,
                report_path=str(self.report_path),
                fallthrough_url=real_url,
                cassette_path=str(self.cassette_path),
            )
        self._http_engine = engine
        return self._start_background(engine.serve)

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

    def finalize(self) -> None:
        """Stop any in-process server, check the session report, raise on violations.

        Raises:
            CassetteError: If a recording captured zero messages (or could not
                reach the upstream at first contact), or replay hit any unmatched
                request.
        """
        self._stop_background()
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

    def _peek_transport(self) -> str:
        """The existing cassette's transport (``stdio`` when absent/unreadable)."""
        try:
            return Cassette.load(self.cassette_path).transport
        except (FileNotFoundError, ValueError):
            return "stdio"

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

    def _match_flags(self) -> list[str]:
        flags = ["--ordering", self.match.ordering]
        for ptr in self.match.ignore_params:
            flags += ["--ignore-param", ptr]
        if self.match.rewrite_protocol_version:
            flags.append("--rewrite-protocol-version")
        return flags
