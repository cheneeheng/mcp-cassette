"""Command-line interface: ``record``, ``serve``, ``inspect``, ``diff``, ``lint``.

A near-zero-dependency argparse tree. The full subcommand and flag surface is registered
so ``--help`` shows the intended interface; every subcommand is a real implementation at
the MVP.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .cassette import (
    Cassette,
    FaultOverlay,
    MatchConfig,
    Message,
    PaceConfig,
    RedactionRule,
    UnsupportedFormatVersion,
)
from .diffing import CassetteDiff, ToolChange, diff_cassettes
from .lint import ProjectLintConfig, discover_config, run_with_notes
from .lint.engine import latest_tools
from .matching import Matcher
from .record.checkpoint import DEFAULT_CHECKPOINT_INTERVAL
from .record.proxy import StdioRecordingProxy
from .replay.faults import Injector
from .replay.new_episodes import NewEpisodesProxy
from .replay.server import ReplayServer


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
        help="Local port for the recording proxy (default: ephemeral).",
    )
    rec.add_argument(
        "--max-idle",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "End the recording after this much client inactivity — the "
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
        help="Disable the always-on default redaction rule set.",
    )
    rec.add_argument("--report", help="Write a JSON session report to this path.")
    rec.epilog = "Pass the real server command after a -- separator: -- CMD [ARGS...]."

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
    ins.add_argument(
        "--timeline",
        action="store_true",
        help="One line per message: who sent what, when, with which id and size.",
    )
    ins.add_argument(
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
            "Scan recorded tool descriptions and results for known smells: "
            "injection phrasing (R001), description drift vs a baseline (R002), "
            "duplicate tool names (R003), instruction-shaped results (R004). "
            "These are pattern rules, not a guarantee — a clean lint is absence "
            "of known smells, nothing more. Packs extend the bundled rules; they "
            "never replace them."
        ),
    )
    lint.add_argument("cassette", help="Path to the cassette to lint.")
    lint.add_argument(
        "--baseline",
        help="Older cassette to compare tool surfaces against (enables R002).",
    )
    lint.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text; json is deterministic and diffable).",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
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
    if args.url:
        try:
            from .transports.http import RecordingProxy
        except ImportError as exc:
            sys.stderr.write(f"mcp-cassette record: {exc}\n")
            return 2
        return RecordingProxy(
            server_url=args.url,
            cassette_path=args.cassette,
            redaction=[_parse_redaction(s) for s in args.redact],
            include_default_redactions=not args.no_default_redactions,
            port=args.port,
            report_path=args.report,
            max_idle=args.max_idle,
            checkpoint_interval=args.checkpoint_interval,
        ).run()
    proxy = StdioRecordingProxy(
        server_cmd=server_cmd,
        cassette_path=args.cassette,
        redaction=[_parse_redaction(s) for s in args.redact],
        include_default_redactions=not args.no_default_redactions,
        report_path=args.report,
        checkpoint_interval=args.checkpoint_interval,
    )
    return proxy.run()


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
    try:
        cassette = Cassette.load(args.cassette)
    except (UnsupportedFormatVersion, FileNotFoundError) as exc:
        sys.stderr.write(f"mcp-cassette serve: {exc}\n")
        return 2
    pace, pace_error = _build_pace(args)
    if pace_error is not None:
        sys.stderr.write(f"mcp-cassette serve: {pace_error}\n")
        return 2
    args.pace_config = pace
    config = MatchConfig(
        ignore_params=args.ignore_param,
        ordering=args.ordering,
        rewrite_protocol_version=args.rewrite_protocol_version,
    )
    if cassette.transport == "http":
        return _cmd_serve_http(args, cassette, config)
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
        return NewEpisodesProxy(
            cassette=cassette,
            cassette_path=args.cassette,
            server_cmd=server_cmd,
            match=config,
            report_path=args.report,
            pace=pace,
        ).run()

    overlay = FaultOverlay.load(args.faults) if args.faults else None
    server = ReplayServer(
        cassette, match=config, faults=overlay, report_path=args.report, pace=pace
    )
    return server.run()


def _cmd_serve_http(
    args: argparse.Namespace, cassette: Cassette, config: MatchConfig
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
    overlay = FaultOverlay.load(args.faults) if args.faults else None
    return HttpReplayServer(
        cassette,
        match=config,
        faults=overlay,
        port=args.port,
        report_path=args.report,
        fallthrough_url=fallthrough_url,
        cassette_path=args.cassette if fallthrough_url else None,
        pace=args.pace_config,
    ).run()


_TIMELINE_COLUMNS = (
    "{seq:<5} {t:>11}  {dir:<4} {kind:<13} {method:<24} {id:<8} {size:>7}"
)
_TIMELINE_HTTP = "  {exch:>5} {chan:<5}"


def _cmd_inspect(args: argparse.Namespace) -> int:
    try:
        cassette = Cassette.load(args.cassette)
    except (UnsupportedFormatVersion, FileNotFoundError) as exc:
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
    if args.faults:
        _inspect_faults(cassette, args.faults)
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


def _timing_span(messages: list[Message]) -> int:
    if not messages:
        return 0
    return messages[-1].t_offset_ms - messages[0].t_offset_ms


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        result = diff_cassettes(args.old, args.new)
    except (UnsupportedFormatVersion, FileNotFoundError, ValidationError) as exc:
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
        _print_diff(result)
    return 0 if result.identical else 5


def _print_diff(result: CassetteDiff) -> None:
    if result.identical:
        print("identical: no structural differences")
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
    return config.model_copy(
        update={
            "select": args.select or config.select,
            "ignore": args.ignore or config.ignore,
            "fail_on": args.fail_on or config.fail_on,
        }
    )


def _cmd_lint(args: argparse.Namespace) -> int:
    try:
        config = _resolve_lint_config(args)
        report, notes = run_with_notes(
            args.cassette,
            args.baseline,
            config.select or None,
            ignore=config.ignore,
            packs=list(args.pattern_pack),
            config=config,
        )
    except (
        UnsupportedFormatVersion,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        sys.stderr.write(f"mcp-cassette lint: {exc}\n")
        return 2
    if args.format == "json":
        print(report.model_dump_json(indent=2))
    else:
        for note in notes:
            print(note)
        for finding in report.findings:
            first, *rest = finding.message.split("\n")
            print(f"{finding.rule} {finding.severity} {finding.locator} {first}")
            for line in rest:
                print(f"    {line}")
        if not report.findings:
            print("clean: no findings")
    # fail_on changes only the exit code; a finding's own severity is never
    # rewritten, so JSON output stays a faithful record.
    threshold = ("warning", "error") if config.fail_on == "warning" else ("error",)
    return 4 if any(f.severity in threshold for f in report.findings) else 0


def _inspect_faults(cassette: Cassette, faults_path: str) -> None:
    overlay = FaultOverlay.load(faults_path)
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
