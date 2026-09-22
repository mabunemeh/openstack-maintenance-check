"""Live collection and offline analysis share the same evidence and rules."""

import argparse
from importlib.resources import files
from pathlib import Path

from . import __version__
from .checks import evaluate
from .collector import CollectionError, collect_cloud
from .reporting import render_json, render_text
from .snapshot import SnapshotError, export_snapshot, load_snapshot, parse_snapshot


def _positive(value: str) -> int:
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive integer") from None


def _nonempty(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("expected a non-empty value")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maintenance-check",
        description="Explain preliminary maintenance findings from Nova or a local snapshot.",
        epilog="Read-only source checks. Exit 0 does not guarantee migration or reboot safety.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="collect Nova evidence or evaluate a snapshot")
    sources = check.add_mutually_exclusive_group(required=True)
    sources.add_argument("--snapshot", type=Path, help="UTF-8 normalized snapshot file")
    sources.add_argument(
        "--cloud", type=_nonempty, help="named clouds.yaml profile (requires [live])"
    )
    check.add_argument("--host", type=_nonempty, help="exact nova-compute service host for --cloud")
    check.add_argument(
        "--historical", action="store_true", help="replay a snapshot without age checks"
    )
    check.add_argument(
        "--max-age-seconds",
        type=_positive,
        default=300,
        help="maximum age from capture/collection start (default: 300)",
    )
    check.add_argument(
        "--export-snapshot",
        type=Path,
        help="save live evidence to a new file; parent directory must exist",
    )
    demo = commands.add_parser(
        "demo", help="evaluate bundled synthetic evidence without credentials"
    )
    demo.add_argument("--scenario", choices=["blocked", "clear", "incomplete"], default="blocked")
    for command in (check, demo):
        command.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)
    if args.command == "check":
        if args.cloud and not args.host:
            parser.error("--host is required with --cloud")
        if args.snapshot and (args.host or args.export_snapshot):
            parser.error("--host and --export-snapshot require --cloud")
        if args.cloud and args.historical:
            parser.error("--historical requires --snapshot")
    try:
        if args.command == "check":
            snapshot = (
                collect_cloud(args.cloud, args.host) if args.cloud else load_snapshot(args.snapshot)
            )
            report = evaluate(
                snapshot,
                historical=args.historical,
                live=bool(args.cloud),
                max_age_seconds=args.max_age_seconds,
            )
            if args.export_snapshot:
                export_snapshot(snapshot, args.export_snapshot)
        else:
            resource = files("openstack_maintenance_check").joinpath(
                "data", f"{args.scenario}.json"
            )
            snapshot = parse_snapshot(resource.read_text(encoding="utf-8"))
            report = evaluate(snapshot, historical=True)
    except (SnapshotError, CollectionError) as exc:
        parser.exit(3, f"maintenance-check: {exc}\n")
    print(render_json(report) if args.format == "json" else render_text(report), end="")
    return report.exit_code
