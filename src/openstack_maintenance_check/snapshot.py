"""Strict normalized snapshot input. This format is not a raw Nova API response."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Host, Server, Snapshot

MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024


class SnapshotError(ValueError):
    """An unreadable or invalid snapshot, with a message safe for terminal output."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SnapshotError("non-finite numbers are not valid snapshot JSON")


def _object(value: Any, path: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotError(f"{path}: expected an object")
    if required - value.keys():
        missing = ", ".join(sorted(required - value.keys()))
        raise SnapshotError(f"{path}: missing required fields: {missing}")
    if value.keys() - required - optional:
        raise SnapshotError(f"{path}: unexpected fields (use the normalized snapshot schema)")
    return value


def _string(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(
            f"{path}: expected a non-empty string" + (" or null" if nullable else "")
        )
    return value


def parse_snapshot(text: str) -> Snapshot:
    """Validate serialized input; missing optional observations stay unavailable."""
    try:
        raw = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except SnapshotError:
        raise
    except (ValueError, RecursionError) as exc:
        raise SnapshotError("invalid JSON snapshot") from exc
    raw = _object(
        raw,
        "snapshot",
        {"schema_version", "captured_at", "host", "servers_complete", "servers"},
        set(),
    )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise SnapshotError("schema_version: only integer version 1 is supported")
    captured = _string(raw["captured_at"], "captured_at")
    assert captured is not None
    try:
        captured_at = datetime.fromisoformat(captured)
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("timezone missing")
        captured_at = captured_at.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise SnapshotError("captured_at: expected an ISO 8601 timestamp with a timezone") from exc
    host_raw = _object(raw["host"], "host", {"name"}, {"state", "status"})
    name = _string(host_raw["name"], "host.name")
    assert name is not None
    host = Host(
        name=name,
        state=_string(host_raw.get("state"), "host.state", nullable=True),
        status=_string(host_raw.get("status"), "host.status", nullable=True),
    )
    if type(raw["servers_complete"]) is not bool:
        raise SnapshotError("servers_complete: expected a boolean")
    if not isinstance(raw["servers"], list):
        raise SnapshotError("servers: expected an array")
    servers = []
    seen: set[str] = set()
    for index, item in enumerate(raw["servers"]):
        path = f"servers[{index}]"
        item = _object(item, path, {"id", "host"}, {"name", "status", "task_state"})
        server_id = _string(item["id"], f"{path}.id")
        server_host = _string(item["host"], f"{path}.host")
        assert server_id is not None and server_host is not None
        if server_id in seen:
            raise SnapshotError(f"{path}.id: duplicate server ID")
        if server_host != host.name:
            raise SnapshotError(f"{path}.host: does not match the snapshot host")
        seen.add(server_id)
        servers.append(
            Server(
                id=server_id,
                name=_string(item.get("name"), f"{path}.name", nullable=True),
                host=server_host,
                status=_string(item.get("status"), f"{path}.status", nullable=True),
                task_state=_string(item.get("task_state"), f"{path}.task_state", nullable=True),
                task_state_known="task_state" in item,
            )
        )
    return Snapshot(
        captured_at=captured_at,
        host=host,
        servers_complete=raw["servers_complete"],
        servers=tuple(sorted(servers, key=lambda server: server.id)),
    )


def load_snapshot(path: Path) -> Snapshot:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise SnapshotError("snapshot exceeds the 5 MiB input limit")
        text = data.decode("utf-8-sig")
    except OSError as exc:
        raise SnapshotError("cannot read snapshot file; check its path and permissions") from exc
    except UnicodeError as exc:
        raise SnapshotError("snapshot must be UTF-8 JSON") from exc
    return parse_snapshot(text)
