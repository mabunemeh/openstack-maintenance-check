"""Offline CLI. Cloud access is intentionally absent from the initial phase."""

import argparse
from importlib.resources import files
from pathlib import Path

from . import __version__
from .checks import evaluate
from .reporting import render_json, render_text
from .snapshot import SnapshotError, load_snapshot, parse_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maintenance-check",
        description="Explain preliminary maintenance findings from a local inventory snapshot.",
        epilog="Offline source checks only. Exit 0 does not guarantee migration or reboot safety.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="evaluate a normalized JSON snapshot")
    check.add_argument(
        "--snapshot", required=True, type=Path, help="UTF-8 normalized snapshot file"
    )
    demo = commands.add_parser(
        "demo", help="evaluate bundled synthetic evidence without credentials"
    )
    demo.add_argument("--scenario", choices=["blocked", "clear", "incomplete"], default="blocked")
    for command in (check, demo):
        command.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            snapshot = load_snapshot(args.snapshot)
        else:
            resource = files("openstack_maintenance_check").joinpath(
                "data", f"{args.scenario}.json"
            )
            snapshot = parse_snapshot(resource.read_text(encoding="utf-8"))
    except SnapshotError as exc:
        parser.exit(3, f"maintenance-check: {exc}\n")
    report = evaluate(snapshot)
    print(render_json(report) if args.format == "json" else render_text(report), end="")
    return report.exit_code
