"""Command-line interface: ``record``, ``serve``, ``inspect``.

A near-zero-dependency argparse tree. The full subcommand and flag surface is registered
so ``--help`` shows the intended interface; every subcommand is a real implementation at
the MVP.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .cassette import (
    Cassette,
    FaultOverlay,
    MatchConfig,
    RedactionRule,
    UnsupportedCassetteFeature,
    UnsupportedFormatVersion,
)
from .matching import Matcher
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

    rec = sub.add_parser("record", help="Wrap a real server command for recording.")
    rec.add_argument("--cassette", required=True, help="Path to write the cassette.")
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

    srv = sub.add_parser("serve", help="Stand up a replay server from a cassette.")
    srv.add_argument("cassette", help="Path to the cassette to replay.")
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
    parser.error(f"unknown command {args.command}")
    return 2


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
    if not server_cmd:
        sys.stderr.write("mcp-cassette record: missing server command after --\n")
        return 2
    proxy = StdioRecordingProxy(
        server_cmd=server_cmd,
        cassette_path=args.cassette,
        redaction=[_parse_redaction(s) for s in args.redact],
        include_default_redactions=not args.no_default_redactions,
        report_path=args.report,
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
    try:
        server = ReplayServer(
            cassette, match=config, faults=overlay, report_path=args.report
        )
    except UnsupportedCassetteFeature as exc:
        sys.stderr.write(f"mcp-cassette serve: {exc}\n")
        return 2
    return server.run()


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
    print(f"recorded_at: {cassette.recorded_at.isoformat()}")
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
