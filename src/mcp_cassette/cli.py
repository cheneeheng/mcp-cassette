"""Command-line interface: ``record``, ``serve``, ``inspect``.

A near-zero-dependency argparse tree. The full subcommand and flag surface is registered
so ``--help`` shows the intended interface; every subcommand is a real implementation at
the MVP.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from urllib.parse import urlsplit

from pydantic import ValidationError

from .cassette import (
    Cassette,
    FaultOverlay,
    MatchConfig,
    RedactionRule,
    UnsupportedFormatVersion,
)
from .lint import run_with_notes
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

    lint = sub.add_parser(
        "lint",
        help="Heuristic security scan of a cassette (CI-friendly; exit 4 on errors).",
        description=(
            "Scan recorded tool descriptions and results for known smells: "
            "injection phrasing (R001), description drift vs a baseline (R002), "
            "duplicate tool names (R003), instruction-shaped results (R004). "
            "These are pattern rules, not a guarantee — a clean lint is absence "
            "of known smells, nothing more."
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


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        cassette = Cassette.load(args.cassette)
    except (UnsupportedFormatVersion, FileNotFoundError) as exc:
        sys.stderr.write(f"mcp-cassette serve: {exc}\n")
        return 2
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
        ).run()

    overlay = FaultOverlay.load(args.faults) if args.faults else None
    server = ReplayServer(
        cassette, match=config, faults=overlay, report_path=args.report
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
    ).run()


def _cmd_inspect(args: argparse.Namespace) -> int:
    try:
        cassette = Cassette.load(args.cassette)
    except (UnsupportedFormatVersion, FileNotFoundError) as exc:
        sys.stderr.write(f"mcp-cassette inspect: {exc}\n")
        return 2
    messages = cassette.messages
    if args.method:
        messages = [m for m in messages if m.method == args.method]

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

    by_method: Counter[str] = Counter(m.method or f"<{m.kind}>" for m in messages)
    for name, count in sorted(by_method.items()):
        print(f"  {name}: {count}")
    if messages:
        span = messages[-1].t_offset_ms - messages[0].t_offset_ms
        print(f"timing span: {span} ms")

    if args.faults:
        _inspect_faults(cassette, args.faults)
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    try:
        report, notes = run_with_notes(
            args.cassette,
            args.baseline,
            args.select or None,
            ignore=args.ignore,
        )
    except (
        UnsupportedFormatVersion,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
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
    has_errors = any(f.severity == "error" for f in report.findings)
    return 4 if has_errors else 0


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
