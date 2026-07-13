"""Per-test cassette session: mode resolution, command building, finalization.

The fixture does not monkeypatch the agent. It hands the test a *command list* to plug
into the agent's MCP server configuration: in record mode the command is the recording
proxy wrapping the real server; in replay mode it is ``mcp-cassette serve``. Command
substitution is the whole trick, which keeps any MCP client unmodified.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from .cassette import Fault, FaultOverlay, MatchConfig
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

    def finalize(self) -> None:
        """Check the session report and raise on violations.

        Raises:
            CassetteError: If a recording captured zero messages, or replay hit any
                unmatched request.
        """
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
