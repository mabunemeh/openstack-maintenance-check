"""Offline comparison of an exported snapshot with `openstack server list -f json -c ID`."""

import argparse
import json
from pathlib import Path

from openstack_maintenance_check.snapshot import SnapshotError, load_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("cli_inventory", type=Path)
    args = parser.parse_args(argv)
    try:
        snapshot = load_snapshot(args.snapshot)
        rows = json.loads(args.cli_inventory.read_text(encoding="utf-8-sig"))
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("ID"), str) or not row["ID"].strip()
            for row in rows
        ):
            raise ValueError("invalid inventory")
        reference = {row["ID"] for row in rows}
        if len(reference) != len(rows):
            raise ValueError("duplicate IDs")
    except (OSError, ValueError, UnicodeError, SnapshotError):
        parser.exit(2, "Cannot read valid snapshot and administrative CLI inventory files.\n")
    if not snapshot.servers_complete:
        print("Comparison inconclusive: collected inventory is incomplete.")
        return 1
    observed = {server.id for server in snapshot.servers}
    missing, extra = reference - observed, observed - reference
    print(
        f"Collected: {len(observed)}; CLI: {len(reference)}; "
        f"missing from collection: {len(missing)}; only in collection: {len(extra)}"
    )
    return int(bool(missing or extra))


if __name__ == "__main__":
    raise SystemExit(main())
