"""Strict normalized snapshot input. This format is not a raw Nova API response."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import COLLECTION_ISSUES, Collection, Host, Server, Snapshot

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


def _timestamp(value: Any, path: str) -> datetime:
    text = _string(value, path)
    assert text is not None
    try:
        timestamp = datetime.fromisoformat(text)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timezone missing")
        return timestamp.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise SnapshotError(f"{path}: expected an ISO 8601 timestamp with a timezone") from exc


def _collection(raw: Any, captured_at: datetime) -> Collection:
    data = _object(
        raw, "collection", {"started_at", "completed_at", "compute_api_version", "issues"}, set()
    )
    started = _timestamp(data["started_at"], "collection.started_at")
    completed = _timestamp(data["completed_at"], "collection.completed_at")
    if started != captured_at or completed < started:
        raise SnapshotError(
            "collection: capture must equal start, and completion must follow start"
        )
    if data["compute_api_version"] != "2.1":
        raise SnapshotError("collection.compute_api_version: only 2.1 is supported")
    issues = data["issues"]
    if not isinstance(issues, list) or any(
        not isinstance(issue, str) or issue not in COLLECTION_ISSUES for issue in issues
    ):
        raise SnapshotError("collection.issues: expected an array of recognized issue codes")
    if len(set(issues)) != len(issues):
        raise SnapshotError("collection.issues: duplicate issue codes")
    return Collection(started, completed, "2.1", tuple(sorted(issues)))


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
        {"collection"},
    )
    if type(raw["schema_version"]) is not int or raw["schema_version"] not in {1, 2}:
        raise SnapshotError("schema_version: only integer versions 1 and 2 are supported")
    if (raw["schema_version"] == 2) != ("collection" in raw):
        raise SnapshotError("collection: required in version 2 and forbidden in version 1")
    captured_at = _timestamp(raw["captured_at"], "captured_at")
    collection = _collection(raw["collection"], captured_at) if "collection" in raw else None
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
    if collection and collection.issues and raw["servers_complete"]:
        raise SnapshotError("servers_complete: must be false when collection issues are present")
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
        collection=collection,
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


def snapshot_dict(snapshot: Snapshot) -> dict[str, Any]:
    """Export only the normalized allowlist; never SDK objects or raw API bodies."""
    data: dict[str, Any] = {
        "schema_version": 2 if snapshot.collection else 1,
        "captured_at": snapshot.captured_at.isoformat(),
        "host": {
            "name": snapshot.host.name,
            "state": snapshot.host.state,
            "status": snapshot.host.status,
        },
        "servers_complete": snapshot.servers_complete,
        "servers": [
            {
                "id": server.id,
                "name": server.name,
                "host": server.host,
                "status": server.status,
                **({"task_state": server.task_state} if server.task_state_known else {}),
            }
            for server in snapshot.servers
        ],
    }
    if snapshot.collection:
        collection = snapshot.collection
        data["collection"] = {
            "started_at": collection.started_at.isoformat(),
            "completed_at": collection.completed_at.isoformat(),
            "compute_api_version": collection.compute_api_version,
            "issues": list(collection.issues),
        }
    return data


def export_snapshot(snapshot: Snapshot, path: Path) -> None:
    """Write an explicitly requested new file. Never overwrite an existing path."""
    text = json.dumps(snapshot_dict(snapshot), indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    parse_snapshot(text)
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("snapshot exceeds the 5 MiB export limit")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError as exc:
        raise SnapshotError("snapshot export target already exists; choose a new path") from exc
    except OSError as exc:
        raise SnapshotError(
            "cannot export snapshot; check parent directory and permissions"
        ) from exc
