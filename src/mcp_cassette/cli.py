"""Command-line interface: ``record``, ``serve``, ``inspect``, ``diff``, ``lint``.

A near-zero-dependency argparse tree. The full subcommand and flag surface is registered
so ``--help`` shows the intended interface; every subcommand is a real implementation at
the MVP.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from .cassette import (
    Cassette,
    FaultOverlay,
    MatchConfig,
    Message,
    PaceConfig,
    RedactionRule,
    SaltMode,
    UnsupportedFormatVersion,
)
from .diffing import CassetteDiff, ToolChange, diff_cassettes
from .lint import (
    EntropyConfig,
    LintReport,
    ProjectLintConfig,
    discover_config,
    run_with_notes,
)
from .lint.engine import latest_tools
from .matching import Matcher
from .record.checkpoint import DEFAULT_CHECKPOINT_INTERVAL
from .record.proxy import StdioRecordingProxy
from .redaction import Redactor, append_redactor, replay_transform, resolve_packs
from .redaction.replay import RequestTransform
from .replay.faults import Injector
from .replay.new_episodes import NewEpisodesProxy
from .replay.server import ReplayServer
from .session.claim import ClaimConflict, ClaimFile, read_claim

_LOAD_ERRORS = (UnsupportedFormatVersion, OSError, ValueError)
"""Everything loading a cassette or fault overlay can raise, as exit-2 usage errors.

``OSError`` covers an unreadable path (missing, a directory, no permission);
``ValueError`` covers bad content, since both :class:`json.JSONDecodeError` and
pydantic's ``ValidationError`` derive from it. Every load site catches the same
tuple so no subcommand can traceback where its sibling prints a diagnostic.
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argparse tree for the CLI."""
    parser = argparse.ArgumentParser(
        prog="mcp-cassette",
        description="Record/replay and mocking for MCP agent test suites.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record",
        help="Record a real server: wrap a stdio command, or proxy a remote URL.",
    )
    rec.add_argument("--cassette", required=True, help="Path to write the cassette.")
    rec.add_argument(
        "--url",
        help=(
            "Remote Streamable HTTP MCP endpoint to record (mutually exclusive "
            "with a -- CMD; needs the [http] extra)."
        ),
    )
    rec.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port for the --url recording proxy (default: ephemeral).",
    )
    rec.add_argument(
        "--max-idle",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "End a --url recording after this much client inactivity — the "
            "unattended-CI escape hatch (default: off; recording ends on signal)."
        ),
    )
    rec.add_argument(
        "--checkpoint-interval",
        type=float,
        default=DEFAULT_CHECKPOINT_INTERVAL,
        metavar="SECONDS",
        help=(
            f"Seconds between crash-safety checkpoints to <cassette>.partial "
            f"(default: {DEFAULT_CHECKPOINT_INTERVAL:g}; 0 disables). A kill loses "
            "only what arrived since the last checkpoint."
        ),
    )
    rec.add_argument(
        "--redact",
        action="append",
        default=[],
        metavar="LOCATOR[=REPLACEMENT]",
        help="Extra redaction rule (repeatable). Key-glob or JSON pointer.",
    )
    rec.add_argument(
        "--no-default-redactions",
        action="store_true",
        help=(
            "Disable the always-on default redactions: the structural key rules and "
            "the bundled PII pack."
        ),
    )
    rec.add_argument(
        "--pii-pack",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "TOML redaction pack scrubbing free text at record time (repeatable; "
            "additive to the bundled pack)."
        ),
    )
    rec.add_argument(
        "--redact-profile",
        metavar="NAME",
        help=(
            "Profile name stamped on the cassette's redaction manifest — what "
            "lint --require-redaction checks."
        ),
    )
    rec.add_argument(
        "--redact-salt-env",
        action="store_true",
        help=(
            "Key hash pseudonyms with $MCP_CASSETTE_REDACT_SALT. The stable default "
            "keeps cassettes diffable but a stable pseudonym of a low-entropy value "
            "is dictionary-attackable; with this flag pseudonyms differ across "
            "projects and re-records produce different bytes."
        ),
    )
    rec.add_argument(
        "--force",
        action="store_true",
        help=(
            "Break another live process's write claim on --cassette (with a "
            "warning) instead of exiting 6."
        ),
    )
    rec.add_argument(
        "--claim-wait",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Wait this long for another writer's claim on --cassette to be released "
            "before exiting 6 (default: 0, fail fast)."
        ),
    )
    rec.add_argument("--claim-held-by", type=int, default=None, help=argparse.SUPPRESS)
    rec.add_argument("--report", help="Write a JSON session report to this path.")
    rec.epilog = (
        "Pass the real server command after a -- separator: -- CMD [ARGS...]. "
        "Exit 6 means another live process holds --cassette for writing."
    )

    srv = sub.add_parser(
        "serve",
        help=(
            "Stand up a replay server from a cassette (transport inferred from "
            "the cassette: stdio or Streamable HTTP)."
        ),
    )
    srv.add_argument("cassette", help="Path to the cassette to replay.")
    srv.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port for an http cassette (default: ephemeral; URL printed).",
    )
    srv.add_argument(
        "--url",
        help=(
            "Real server URL for --new-episodes with an http cassette "
            "(default: the cassette's recorded server_url)."
        ),
    )
    srv.add_argument(
        "--ordering",
        choices=["per_method", "strict", "none"],
        default="per_method",
        help="Matching order discipline (default: per_method).",
    )
    srv.add_argument(
        "--ignore-param",
        action="append",
        default=[],
        metavar="POINTER",
        help="JSON pointer excluded from matching (repeatable).",
    )
    srv.add_argument(
        "--rewrite-protocol-version",
        action="store_true",
        help="Rewrite the initialize protocolVersion to the client's requested value.",
    )
    srv.add_argument("--faults", help="Path to a fault overlay JSON sidecar.")
    srv.add_argument(
        "--pii-pack",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Redaction pack named by the cassette's manifest, to re-apply its "
            "scrubbing to live requests (repeatable; packs it does not name are "
            "ignored)."
        ),
    )
    srv.add_argument(
        "--pace",
        choices=["none", "recorded"],
        default="none",
        help=(
            "Replay recorded inter-message latency (default: off — replay is instant)."
        ),
    )
    srv.add_argument(
        "--pace-scale",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Multiply every recorded gap (default: 1.0; must be > 0).",
    )
    srv.add_argument(
        "--pace-cap-ms",
        type=int,
        default=None,
        metavar="MS",
        help=(
            "Per-gap upper bound (default: 5000; 0 = uncapped). Keeps one "
            "pathological recorded pause from looking like a hung job."
        ),
    )
    srv.add_argument(
        "--new-episodes",
        action="store_true",
        help="Replay matches; fall through misses to the real server (needs -- CMD).",
    )
    srv.add_argument("--claim-held-by", type=int, default=None, help=argparse.SUPPRESS)
    srv.add_argument("--report", help="Write a JSON session report to this path.")
    srv.epilog = "For --new-episodes, pass the real server command after --: -- CMD ..."

    ins = sub.add_parser("inspect", help="Human-readable cassette summary.")
    ins.add_argument("cassette", help="Path to the cassette.")
    ins.add_argument("--method", help="Only summarize messages for this method.")
    ins.add_argument(
        "--faults",
        help="Dry-run a fault overlay: report which recorded requests it would hit.",
    )
    ins.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text; json is deterministic and diffable).",
    )
    # One view per run: --timeline and --tools each replace the whole text output,
    # so combining them can only drop one silently. Rejected, not resolved.
    view = ins.add_mutually_exclusive_group()
    view.add_argument(
        "--timeline",
        action="store_true",
        help="One line per message: who sent what, when, with which id and size.",
    )
    view.add_argument(
        "--tools",
        action="store_true",
        help="One line per recorded tool (deduplicated by name, last seen wins).",
    )
    ins.add_argument(
        "--grep",
        metavar="PATTERN",
        help="Regex matched against each message payload; composes with --method.",
    )

    dif = sub.add_parser(
        "diff",
        help="Structurally compare two cassettes (exit 5 when they differ).",
        description=(
            "Compare metadata, per-method counts, tool surfaces, and the exchange "
            "sequence. JSON-RPC ids, t_offset_ms, and seq are never compared — they "
            "are re-stamped or clock-derived, so comparing them would make every "
            "re-recording differ. Descriptive, not a gate: for a CI gate on tool "
            "surfaces use lint's R002 (exit 4) or diff --tools-only (exit 5)."
        ),
    )
    dif.add_argument("old", help="Baseline cassette.")
    dif.add_argument("new", help="Current cassette.")
    dif.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text; json is deterministic and diffable).",
    )
    dif.add_argument(
        "--tools-only",
        action="store_true",
        help="Compare tool surfaces only — the common CI use.",
    )

    lint = sub.add_parser(
        "lint",
        help="Heuristic security scan of a cassette (CI-friendly; exit 4 on errors).",
        description=(
            "Scan recorded tool definitions, results, and every other string for "
            "known smells: injection phrasing (R001), description drift vs a "
            "baseline (R002), duplicate tool names (R003), instruction-shaped "
            "results (R004), high-entropy strings that look like secrets (R005), "
            "injection phrasing in tool names or inputSchema text (R006), "
            "non-ASCII or mixed-script tool names (R007). "
            "These are pattern rules, not a guarantee — a clean lint is absence "
            "of known smells, nothing more. Packs extend the bundled rules; they "
            "never replace them."
        ),
    )
    lint.add_argument("cassette", nargs="+", help="Cassette(s) to lint.")
    lint.add_argument(
        "--baseline",
        help=(
            "Older cassette to compare tool surfaces against (enables R002; one "
            "CASSETTE only)."
        ),
    )
    lint.add_argument(
        "--require-redaction",
        metavar="NAME",
        help=(
            "Exit 4 unless every cassette's redaction manifest names this profile "
            "(see record --redact-profile)."
        ),
    )
    lint.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text; json is deterministic and diffable).",
    )
    lint.add_argument(
        "--annotate",
        choices=["github"],
        default=None,
        help=(
            "Also print one GitHub Actions workflow command per finding on stdout, "
            "after the --format output (file-level; nothing when there are no "
            "findings)."
        ),
    )
    lint.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="RULE",
        help="Run only these rule ids (repeatable, e.g. --select R001).",
    )
    lint.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="RULE",
        help="Skip these rule ids (repeatable).",
    )
    lint.add_argument(
        "--pattern-pack",
        action="append",
        default=[],
        metavar="PATH",
        help="TOML pattern pack to load (repeatable; additive to project config).",
    )
    lint.add_argument(
        "--fail-on",
        choices=["error", "warning"],
        default=None,
        help="Lowest severity that exits 4 (default: error, or the project config).",
    )
    lint.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore [tool.mcp_cassette.lint] in the nearest pyproject.toml.",
    )
    lint.add_argument(
        "--entropy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Run R005 high-entropy secret detection (default: on, at warning). "
            "Entropy is a smell, not a verdict — review findings, do not auto-fix. "
            "At most 200 candidate tokens per message are scanned."
        ),
    )
    lint.add_argument(
        "--entropy-min-bits",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Lowest Shannon entropy per character R005 reports (default: 3.5).",
    )
    lint.add_argument(
        "--entropy-min-length",
        type=int,
        default=None,
        metavar="N",
        help="Shortest token R005 considers (default: 20).",
    )
    lint.add_argument(
        "--entropy-allow",
        action="append",
        default=None,
        metavar="STRING",
        help=(
            "Literal token R005 never reports (repeatable; exact match, not a regex; "
            "replaces the project config's allowlist)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    _force_utf8_output()
    raw = list(sys.argv[1:] if argv is None else argv)
    front, server_cmd = _split_server_cmd(raw)
    parser = build_parser()
    args = parser.parse_args(front)
    args.server_cmd = server_cmd
    if args.command == "record":
        return _cmd_record(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "inspect":
        return _cmd_inspect(args)
    if args.command == "diff":
        return _cmd_diff(args)
    if args.command == "lint":
        return _cmd_lint(args)
    parser.error(f"unknown command {args.command}")  # pragma: no cover
    return 2  # pragma: no cover — required subparsers reject unknown commands


def _force_utf8_output() -> None:
    """Make the text streams encode any cassette content, on every platform.

    Findings quote recorded text verbatim — a Cyrillic tool name (R007), a ``mask``
    run of U+2022 — and the default console encoding on Windows is cp1252, which
    cannot encode either. Without this the CLI dies with ``UnicodeEncodeError`` and
    exit 1, losing the exit-code contract on the rules whose whole point is a
    non-ASCII character. The JSON-RPC paths write bytes through :mod:`._stdio` and
    are unaffected.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def _split_server_cmd(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first standalone ``--`` into (front, server command)."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def _parse_redaction(spec: str) -> RedactionRule:
    if "=" in spec:
        locator, replacement = spec.split("=", 1)
        return RedactionRule(locator=locator, replacement=replacement)
    return RedactionRule(locator=spec)


def _salt_mode(args: argparse.Namespace) -> SaltMode:
    return "env" if args.redact_salt_env else "stable"


def _cmd_record(args: argparse.Namespace) -> int:
    server_cmd = args.server_cmd
    if args.url and server_cmd:
        sys.stderr.write(
            "mcp-cassette record: --url and a -- CMD are mutually exclusive\n"
        )
        return 2
    if not args.url and not server_cmd:
        sys.stderr.write(
            "mcp-cassette record: pass a remote --url URL or a server command "
            "after --\n"
        )
        return 2
    if not args.url and (args.port or args.max_idle is not None):
        sys.stderr.write(
            "mcp-cassette record: --port/--max-idle apply to --url recording only, "
            "not to a stdio -- CMD\n"
        )
        return 2
    if args.url:
        try:
            from .transports.http import RecordingProxy  # noqa: F401 — extra check
        except ImportError as exc:
            sys.stderr.write(f"mcp-cassette record: {exc}\n")
            return 2
    # CLI record always records, so a live claim takes the `all` row: fail fast,
    # unless --claim-wait asks for a bounded wait or --force breaks it.
    return _claimed(
        args,
        "record",
        "all",
        lambda claim: _record(args, server_cmd, claim),
        wait=args.claim_wait,
        force=args.force,
    )


def _record(
    args: argparse.Namespace, server_cmd: list[str], claim: ClaimFile | None
) -> int:
    # After the claim, not before: with a live claim the run exits 6 and the file
    # this warning would promise to replace is never touched.
    if Path(args.cassette).exists():
        sys.stderr.write(
            f"mcp-cassette record: {args.cassette} already exists and will be "
            "replaced when this session ends; copy it first to keep it\n"
        )
    run: Callable[[], int]
    try:
        if args.url:
            from .transports.http import RecordingProxy

            run = RecordingProxy(
                server_url=args.url,
                cassette_path=args.cassette,
                redaction=[_parse_redaction(s) for s in args.redact],
                include_default_redactions=not args.no_default_redactions,
                port=args.port,
                report_path=args.report,
                max_idle=args.max_idle,
                checkpoint_interval=args.checkpoint_interval,
                pii_packs=args.pii_pack,
                redact_profile=args.redact_profile,
                salt_mode=_salt_mode(args),
                claim=claim,
            ).run
        else:
            run = StdioRecordingProxy(
                server_cmd=server_cmd,
                cassette_path=args.cassette,
                redaction=[_parse_redaction(s) for s in args.redact],
                include_default_redactions=not args.no_default_redactions,
                report_path=args.report,
                checkpoint_interval=args.checkpoint_interval,
                pii_packs=args.pii_pack,
                redact_profile=args.redact_profile,
                salt_mode=_salt_mode(args),
                claim=claim,
            ).run
    except _LOAD_ERRORS as exc:
        sys.stderr.write(f"mcp-cassette record: {exc}\n")
        return 2
    return run()


def _claimed(
    args: argparse.Namespace,
    command: str,
    mode: str,
    run: Callable[[ClaimFile | None], int],
    *,
    wait: float = 0.0,
    force: bool = False,
) -> int:
    """Run a cassette write under the single-writer claim; exit 6 on a conflict.

    A fixture or library session takes the claim itself before handing the agent this
    command and passes its own pid as ``--claim-held-by``, so the child neither
    re-takes the claim (it would conflict with its parent) nor releases it.
    """
    claim: ClaimFile | None = None
    holder = read_claim(args.cassette) if args.claim_held_by is not None else None
    if holder is None or holder.pid != args.claim_held_by:
        claim = ClaimFile(args.cassette, mode, wait=wait, force=force)
        try:
            claim.acquire()
        except ClaimConflict as exc:
            sys.stderr.write(f"mcp-cassette {command}: {exc}\n")
            return 6
    try:
        return run(claim)
    finally:
        if claim is not None:
            claim.release()


def _build_pace(args: argparse.Namespace) -> tuple[PaceConfig | None, str | None]:
    """Resolve the pacing flags into a config, or a usage error to print."""
    if args.pace != "recorded":
        if args.pace_scale is not None or args.pace_cap_ms is not None:
            return None, (
                "--pace-scale/--pace-cap-ms have no effect without --pace recorded"
            )
        return None, None
    if args.pace_scale is not None and args.pace_scale <= 0:
        return None, f"--pace-scale {args.pace_scale} is invalid: must be > 0"
    return (
        PaceConfig(
            mode="recorded",
            scale=1.0 if args.pace_scale is None else args.pace_scale,
            cap_ms=5000 if args.pace_cap_ms is None else args.pace_cap_ms,
        ),
        None,
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.new_episodes and args.faults:
        # Same rule the programmatic doors enforce (CassetteSession raises when an
        # overlay meets a non-replay action): a fault changes the path the agent
        # takes, and under --new-episodes that changed path is what gets appended
        # to the cassette.
        sys.stderr.write(
            "mcp-cassette serve: --faults applies to replay only; --new-episodes "
            "records novel exchanges live\n"
        )
        return 2
    try:
        cassette = Cassette.load(args.cassette)
        overlay = FaultOverlay.load(args.faults) if args.faults else None
    except _LOAD_ERRORS as exc:
        sys.stderr.write(f"mcp-cassette serve: {exc}\n")
        return 2
    pace, pace_error = _build_pace(args)
    if pace_error is not None:
        sys.stderr.write(f"mcp-cassette serve: {pace_error}\n")
        return 2
    args.pace_config = pace
    try:
        transform, appender = _replay_redaction(args, cassette)
    except _LOAD_ERRORS as exc:
        sys.stderr.write(f"mcp-cassette serve: {exc}\n")
        return 2
    config = MatchConfig(
        ignore_params=args.ignore_param,
        ordering=args.ordering,
        rewrite_protocol_version=args.rewrite_protocol_version,
    )
    if cassette.transport == "http":
        return _cmd_serve_http(args, cassette, config, overlay, transform, appender)
    if args.url:
        sys.stderr.write(
            "mcp-cassette serve: --url applies to http cassettes; this cassette "
            "was recorded over stdio (pass the server command after -- instead)\n"
        )
        return 2
    if args.new_episodes:
        server_cmd = args.server_cmd
        if not server_cmd:
            sys.stderr.write(
                "mcp-cassette serve --new-episodes: missing server command after --\n"
            )
            return 2
        return _claimed(
            args,
            "serve",
            "new_episodes",
            lambda claim: NewEpisodesProxy(
                cassette=cassette,
                cassette_path=args.cassette,
                server_cmd=server_cmd,
                match=config,
                report_path=args.report,
                pace=pace,
                request_transform=transform,
                append_redactor=appender,
                claim=claim,
            ).run(),
        )

    server = ReplayServer(
        cassette,
        match=config,
        faults=overlay,
        report_path=args.report,
        pace=pace,
        request_transform=transform,
    )
    return server.run()


def _replay_redaction(
    args: argparse.Namespace, cassette: Cassette
) -> tuple[RequestTransform | None, Redactor | None]:
    """Resolve the cassette's recorded redaction into replay-time transforms.

    Raises:
        ValueError: If ``--pii-pack`` meets a cassette with no manifest, or a pack the
            manifest names cannot be resolved.
    """
    manifest = cassette.redaction
    if manifest is None:
        if args.pii_pack:
            raise ValueError(
                f"--pii-pack given, but {args.cassette} carries no redaction "
                "manifest, so there is nothing to resolve a pack against (is this "
                "the cassette you meant to serve?)"
            )
        return None, None
    _, unused = resolve_packs(manifest, args.pii_pack)
    for path in unused:
        sys.stderr.write(
            f"mcp-cassette serve: note: --pii-pack {path} is not named by this "
            "cassette's redaction manifest; ignored\n"
        )
    transform = replay_transform(manifest, args.pii_pack)
    appender = append_redactor(manifest, args.pii_pack) if args.new_episodes else None
    return transform, appender


def _cmd_serve_http(
    args: argparse.Namespace,
    cassette: Cassette,
    config: MatchConfig,
    overlay: FaultOverlay | None,
    transform: RequestTransform | None = None,
    appender: Redactor | None = None,
) -> int:
    try:
        from .transports.http import HttpReplayServer
    except ImportError as exc:
        sys.stderr.write(f"mcp-cassette serve: {exc}\n")
        return 2
    fallthrough_url: str | None = None
    if args.new_episodes:
        fallthrough_url = args.url or cassette.server_url
        if not fallthrough_url:
            sys.stderr.write(
                "mcp-cassette serve --new-episodes: no --url given and the "
                "cassette records no server_url\n"
            )
            return 2

    def run(claim: ClaimFile | None) -> int:
        return HttpReplayServer(
            cassette,
            match=config,
            faults=overlay,
            port=args.port,
            report_path=args.report,
            fallthrough_url=fallthrough_url,
            cassette_path=args.cassette if fallthrough_url else None,
            pace=args.pace_config,
            request_transform=transform,
            append_redactor=appender,
        ).run()

    if fallthrough_url is None:
        return run(None)
    return _claimed(args, "serve", "new_episodes", run)


_TIMELINE_COLUMNS = (
    "{seq:<5} {t:>11}  {dir:<4} {kind:<13} {method:<24} {id:<8} {size:>7}"
)
_TIMELINE_HTTP = "  {exch:>5} {chan:<5}"


def _cmd_inspect(args: argparse.Namespace) -> int:
    try:
        cassette = Cassette.load(args.cassette)
        # Loaded up front, not inside the dry-run: a bad overlay must fail before
        # half a report has already been printed.
        overlay = FaultOverlay.load(args.faults) if args.faults else None
    except _LOAD_ERRORS as exc:
        sys.stderr.write(f"mcp-cassette inspect: {exc}\n")
        return 2
    try:
        messages = _filter_messages(cassette, args.method, args.grep)
    except re.error as exc:
        sys.stderr.write(
            f"mcp-cassette inspect: invalid --grep pattern {args.grep!r}: {exc}\n"
        )
        return 2

    if args.format == "json":
        print(json.dumps(_inspect_document(args, cassette, messages), indent=2))
        return 0
    if args.timeline:
        _inspect_timeline(cassette, messages)
        return 0
    if args.tools:
        _inspect_tools(cassette)
        return 0
    _inspect_summary(args, cassette, messages)
    if overlay is not None:
        _inspect_faults(cassette, overlay)
    return 0


def _filter_messages(
    cassette: Cassette, method: str | None, grep: str | None
) -> list[Message]:
    """Apply ``--method`` and ``--grep`` (AND) to the cassette's messages."""
    messages = cassette.messages
    if method:
        messages = [m for m in messages if m.method == method]
    if grep:
        pattern = re.compile(grep)
        messages = [m for m in messages if pattern.search(_payload_text(m))]
    return messages


def _payload_text(message: Message) -> str:
    if isinstance(message.payload, str):
        return message.payload
    return json.dumps(message.payload, sort_keys=True, separators=(",", ":"))


def _inspect_summary(
    args: argparse.Namespace, cassette: Cassette, messages: list[Message]
) -> None:
    print(f"cassette: {args.cassette}")
    print(f"format_version: {cassette.format_version}")
    print(f"transport: {cassette.transport}")
    print(f"recorded_at: {cassette.recorded_at.isoformat()}")
    if cassette.transport == "http":
        if cassette.server_url:
            print(f"server host: {urlsplit(cassette.server_url).netloc}")
        exchanges = {m.exchange for m in cassette.messages if m.exchange is not None}
        print(f"exchanges: {len(exchanges)}")
    if cassette.protocol_version:
        print(f"protocol_version: {cassette.protocol_version}")
    if cassette.server_info:
        print(f"server: {cassette.server_info.name} {cassette.server_info.version}")
    print(f"messages: {len(messages)}")

    for name, count in sorted(_method_counts(messages).items()):
        print(f"  {name}: {count}")
    if messages:
        print(f"timing span: {_timing_span(messages)} ms")
    unanswered = _unanswered_requests(cassette)
    if unanswered:
        # ASCII only: this line lands in whatever console the operator has, and a
        # cp1252 terminal turns a typographic dash into a replacement character.
        print(
            f"unanswered requests: {unanswered}"
            " (the server never responded to these; replay exits 3 on one of them)"
        )


def _inspect_timeline(cassette: Cassette, messages: list[Message]) -> None:
    http = cassette.transport == "http"
    header = _TIMELINE_COLUMNS.format(
        seq="seq",
        t="t_offset_ms",
        dir="dir",
        kind="kind",
        method="method",
        id="id",
        size="bytes",
    )
    if http:
        header += _TIMELINE_HTTP.format(exch="exch", chan="chan")
    print(header)
    for m in messages:
        row = _TIMELINE_COLUMNS.format(
            seq=m.seq,
            t=m.t_offset_ms,
            dir="->" if m.sender == "client" else "<-",
            kind=m.kind,
            method=m.method or "-",
            id="-" if m.msg_id is None else m.msg_id,
            size=len(_payload_text(m)),
        )
        if http:
            row += _TIMELINE_HTTP.format(
                exch="-" if m.exchange is None else m.exchange,
                chan=m.channel or "-",
            )
        print(row)


def _inspect_tools(cassette: Cassette) -> None:
    for name, tool in sorted(latest_tools(cassette).items()):
        args_count = _schema_arg_count(tool.input_schema)
        first_line = (tool.description or "").split("\n")[0]
        print(f"{name}  ({args_count} args)  {first_line}")


def _schema_arg_count(schema: Any) -> int:
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        return len(schema["properties"])
    return 0


def _inspect_document(
    args: argparse.Namespace, cassette: Cassette, messages: list[Message]
) -> dict[str, Any]:
    """The deterministic ``--format json`` document (byte-stable for one input)."""
    document: dict[str, Any] = {
        "cassette": args.cassette,
        "format_version": cassette.format_version,
        "message_counts": dict(sorted(_method_counts(messages).items())),
        "messages": len(messages),
        "protocol_version": cassette.protocol_version,
        "recorded_at": cassette.recorded_at.isoformat(),
        "server_info": (
            {"name": cassette.server_info.name, "version": cassette.server_info.version}
            if cassette.server_info
            else None
        ),
        "timing_span_ms": _timing_span(messages),
        "tools": [
            {
                "name": name,
                "description": tool.description,
                "args": _schema_arg_count(tool.input_schema),
            }
            for name, tool in sorted(latest_tools(cassette).items())
        ],
        "transport": cassette.transport,
        "unanswered_requests": _unanswered_requests(cassette),
    }
    if cassette.transport == "http":
        document["server_host"] = (
            urlsplit(cassette.server_url).netloc if cassette.server_url else None
        )
        document["exchanges"] = len(
            {m.exchange for m in cassette.messages if m.exchange is not None}
        )
    if args.timeline:
        document["timeline"] = [
            {
                "seq": m.seq,
                "t_offset_ms": m.t_offset_ms,
                "sender": m.sender,
                "kind": m.kind,
                "method": m.method,
                "id": m.msg_id,
                "bytes": len(_payload_text(m)),
                "exchange": m.exchange,
                "channel": m.channel,
            }
            for m in messages
        ]
    return document


def _method_counts(messages: list[Message]) -> Counter[str]:
    return Counter(m.method or f"<{m.kind}>" for m in messages)


def _unanswered_requests(cassette: Cassette) -> int:
    """Count client requests the recorded server never answered.

    Always computed over the whole cassette, never the ``--method``/``--grep``
    subset: a filter that drops responses would otherwise invent unanswered
    requests. A non-zero count is the in-band signal that a recording is broken
    rather than merely short — the message count alone cannot tell them apart.
    """
    answered = {
        m.msg_id
        for m in cassette.messages
        if m.sender == "server" and m.kind == "response" and m.msg_id is not None
    }
    return sum(
        1
        for m in cassette.messages
        if m.sender == "client" and m.kind == "request" and m.msg_id not in answered
    )


def _timing_span(messages: list[Message]) -> int:
    if not messages:
        return 0
    return messages[-1].t_offset_ms - messages[0].t_offset_ms


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        result = diff_cassettes(args.old, args.new)
    except _LOAD_ERRORS as exc:
        sys.stderr.write(f"mcp-cassette diff: {exc}\n")
        return 2
    if args.tools_only:
        result = result.model_copy(
            update={
                "metadata": [],
                "methods": [],
                "sequence": [],
                "identical": not result.tools,
            }
        )
    if args.format == "json":
        print(result.model_dump_json(indent=2))
    else:
        _print_diff(result, tools_only=args.tools_only)
    return 0 if result.identical else 5


def _print_diff(result: CassetteDiff, *, tools_only: bool = False) -> None:
    if result.identical:
        # --tools-only narrowed the question, so the answer must say so rather than
        # claim the whole cassette matched.
        scope = "tool surface" if tools_only else "structural"
        print(f"identical: no {scope} differences")
        return
    if result.metadata:
        print("metadata:")
        for change in result.metadata:
            print(f"  {change.field}: {change.old} -> {change.new}")
    if result.methods:
        print("methods:")
        for delta in result.methods:
            print(f"  {delta.method}: {delta.old_count} -> {delta.new_count}")
    if result.tools:
        print("tools:")
        for tool_change in result.tools:
            print(f"  {tool_change.tool}: {_tool_change_summary(tool_change)}")
            for line in tool_change.diff:
                print(f"    {line}")
    if result.sequence:
        print("sequence:")
        for line in result.sequence:
            print(f"  {line}")


def _tool_change_summary(change: ToolChange) -> str:
    if change.change == "description":
        added = sum(1 for d in change.diff if d.startswith("+") and d[:3] != "+++")
        removed = sum(1 for d in change.diff if d.startswith("-") and d[:3] != "---")
        return f"description changed (+{added} -{removed} lines)"
    if change.change == "input_schema":
        return "inputSchema changed"
    return change.change


def _resolve_lint_config(args: argparse.Namespace) -> ProjectLintConfig:
    """Layer CLI flags over the project config.

    Packs compose (a developer adding a personal pack should not lose the team's);
    ``--select``, ``--ignore`` and ``--fail-on`` replace their config counterparts,
    because an explicit selection is an override, not a merge.
    """
    config = ProjectLintConfig() if args.no_config else discover_config()
    overrides = {
        key: value
        for key, value in {
            "enabled": args.entropy,
            "min_bits": args.entropy_min_bits,
            "min_length": args.entropy_min_length,
            "allowlist": args.entropy_allow,
        }.items()
        if value is not None
    }
    entropy = config.entropy
    if overrides:
        base = (config.entropy or EntropyConfig()).model_dump()
        entropy = EntropyConfig.model_validate({**base, **overrides})
    return config.model_copy(
        update={
            "select": args.select or config.select,
            "ignore": args.ignore or config.ignore,
            "fail_on": args.fail_on or config.fail_on,
            "require_redaction": args.require_redaction or config.require_redaction,
            "entropy": entropy,
        }
    )


class _LintResult(NamedTuple):
    path: str
    report: LintReport
    notes: list[str]
    redaction_failure: str | None


def _cmd_lint(args: argparse.Namespace) -> int:
    if args.baseline is not None and len(args.cassette) > 1:
        sys.stderr.write(
            "mcp-cassette lint: --baseline compares one cassette; pass a single "
            "CASSETTE with it\n"
        )
        return 2
    results: list[_LintResult] = []
    try:
        config = _resolve_lint_config(args)
        # Every cassette is linted before anything prints, so a load error on the
        # third path cannot leave half a JSON document behind.
        for path in args.cassette:
            report, notes = run_with_notes(
                path,
                args.baseline,
                config.select or None,
                ignore=config.ignore,
                packs=list(args.pattern_pack),
                config=config,
            )
            failure = _redaction_failure(path, config.require_redaction)
            results.append(_LintResult(path, report, notes, failure))
    except _LOAD_ERRORS as exc:
        sys.stderr.write(f"mcp-cassette lint: {exc}\n")
        return 2
    if args.format == "json":
        if len(results) == 1:
            print(results[0].report.model_dump_json(indent=2))
        else:
            documents = [r.report.model_dump(mode="json") for r in results]
            print(json.dumps(documents, indent=2, ensure_ascii=False))
    else:
        for result in results:
            _print_lint_text(result, with_header=len(results) > 1)
    if args.annotate == "github":
        for result in results:
            _print_github_annotations(result)
    for result in results:
        if result.redaction_failure is not None:
            sys.stderr.write(f"mcp-cassette lint: {result.redaction_failure}\n")
    # fail_on changes only the exit code; a finding's own severity is never
    # rewritten, so JSON output stays a faithful record.
    threshold = ("warning", "error") if config.fail_on == "warning" else ("error",)
    failed = any(
        result.redaction_failure is not None
        or any(f.severity in threshold for f in result.report.findings)
        for result in results
    )
    return 4 if failed else 0


def _print_lint_text(result: _LintResult, *, with_header: bool) -> None:
    if with_header:
        print(f"{result.path}:")
    for note in result.notes:
        print(note)
    for finding in result.report.findings:
        first, *rest = finding.message.split("\n")
        print(f"{finding.rule} {finding.severity} {finding.locator} {first}")
        for line in rest:
            print(f"    {line}")
    if not result.report.findings:
        print("clean: no findings")


def _print_github_annotations(result: _LintResult) -> None:
    # File-level only: findings carry JSON pointers, not line numbers.
    file = _escape_workflow_property(result.path)
    for finding in result.report.findings:
        first = finding.message.split("\n")[0]
        title = _escape_workflow_property(finding.rule)
        text = _escape_workflow_data(f"{finding.rule} {finding.locator} {first}")
        print(f"::{finding.severity} file={file},title={title}::{text}")


def _escape_workflow_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_workflow_property(value: str) -> str:
    return _escape_workflow_data(value).replace(":", "%3A").replace(",", "%2C")


def _redaction_failure(path: str, profile: str | None) -> str | None:
    """Why ``path`` fails ``--require-redaction``, or ``None`` when it passes."""
    if profile is None:
        return None
    manifest = Cassette.load(path).redaction
    fix = (
        f"re-record it with 'mcp-cassette record --redact-profile {profile} "
        f"--cassette {path} -- CMD'"
    )
    if manifest is None:
        return f"{path} carries no redaction manifest; {fix}"
    if manifest.profile != profile:
        return (
            f"{path} was scrubbed with redaction profile {manifest.profile!r}, "
            f"expected {profile!r}; {fix}"
        )
    return None


def _inspect_faults(cassette: Cassette, overlay: FaultOverlay) -> None:
    matcher = Matcher(cassette, MatchConfig())
    injector = Injector(overlay)
    print("\nfault overlay dry-run:")
    for ex in matcher._exchanges:  # noqa: SLF001 — same package
        payload = ex.request.payload
        method = payload.get("method") if isinstance(payload, dict) else None
        fault = injector.consult(method)
        if fault is not None:
            print(f"  seq {ex.request.seq} {method} -> {fault.type}")
    for fault in injector.unused_faults():
        print(f"  WARNING: {fault.type} on {fault.target.method} matches nothing")


if __name__ == "__main__":
    sys.exit(main())
