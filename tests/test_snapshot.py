import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from openstack_maintenance_check.snapshot import (
    MAX_SNAPSHOT_BYTES,
    SnapshotError,
    load_snapshot,
    parse_snapshot,
)


def test_normalized_timestamp_and_explicit_idle(inventory):
    snapshot = parse_snapshot(json.dumps(inventory))
    assert snapshot.captured_at == datetime(2026, 9, 5, 8, tzinfo=UTC)
    assert snapshot.servers[0].task_state is None
    assert snapshot.servers[0].task_state_known is True


def test_missing_optional_observations_preserved(inventory):
    inventory["host"] = {"name": "compute-demo-01"}
    inventory["servers"][0] = {"id": "server-demo-01", "host": "compute-demo-01"}
    snapshot = parse_snapshot(json.dumps(inventory))
    assert snapshot.host.state is None
    assert snapshot.host.status is None
    assert snapshot.servers[0].status is None
    assert snapshot.servers[0].name is None
    assert snapshot.servers[0].task_state_known is False


@pytest.mark.parametrize("raw", ["{", "[]", "null", "false", "1", '"text"'])
def test_invalid_json_or_root(raw):
    with pytest.raises(SnapshotError):
        parse_snapshot(raw)


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 0, 2, None])
def test_invalid_schema_versions(inventory, version):
    inventory["schema_version"] = version
    with pytest.raises(SnapshotError, match="schema_version"):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize(
    "captured", ["2026-09-05", "2026-09-05T08:00:00", "yesterday", "", None, 42]
)
def test_timestamp_requires_valid_timezone(inventory, captured):
    inventory["captured_at"] = captured
    with pytest.raises(SnapshotError, match="captured_at"):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize(
    "field", ["schema_version", "captured_at", "host", "servers_complete", "servers"]
)
def test_required_root_fields(inventory, field):
    del inventory[field]
    with pytest.raises(SnapshotError, match="missing required fields"):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("servers_complete", "true"),
        ("servers_complete", 1),
        ("servers_complete", None),
        ("servers", {}),
        ("servers", None),
        ("host", []),
    ],
)
def test_invalid_root_types(inventory, field, value):
    inventory[field] = value
    with pytest.raises(SnapshotError):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize("path", ["root", "host", "server"])
def test_extra_fields_rejected_without_echoing_values(inventory, path):
    target = {"root": inventory, "host": inventory["host"], "server": inventory["servers"][0]}[path]
    target["unexpected\x1b"] = "DO-NOT-ECHO"
    with pytest.raises(SnapshotError, match="unexpected fields") as exc:
        parse_snapshot(json.dumps(inventory))
    assert "DO-NOT-ECHO" not in str(exc.value)
    assert "\x1b" not in str(exc.value)


@pytest.mark.parametrize("field", ["name", "state", "status"])
@pytest.mark.parametrize("value", [True, 12, [], {}, "", "   "])
def test_host_field_types(inventory, field, value):
    inventory["host"][field] = value
    with pytest.raises(SnapshotError, match=f"host.{field}"):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize("field", ["id", "host", "name", "status", "task_state"])
@pytest.mark.parametrize("value", [True, 12, [], {}, "", "   "])
def test_server_field_types(inventory, field, value):
    inventory["servers"][0][field] = value
    with pytest.raises(SnapshotError):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize("field", ["id", "host"])
def test_required_server_identity(inventory, field):
    del inventory["servers"][0][field]
    with pytest.raises(SnapshotError, match="missing required fields"):
        parse_snapshot(json.dumps(inventory))


def test_missing_host_name(inventory):
    del inventory["host"]["name"]
    with pytest.raises(SnapshotError, match="missing required fields"):
        parse_snapshot(json.dumps(inventory))


def test_non_object_server(inventory):
    inventory["servers"] = [None]
    with pytest.raises(SnapshotError, match="expected an object"):
        parse_snapshot(json.dumps(inventory))


def test_duplicate_server_ids_rejected(inventory):
    inventory["servers"].append(deepcopy(inventory["servers"][0]))
    with pytest.raises(SnapshotError, match="duplicate server ID"):
        parse_snapshot(json.dumps(inventory))


def test_other_host_not_silently_filtered(inventory):
    inventory["servers"][0]["host"] = "compute-demo-other"
    with pytest.raises(SnapshotError, match="does not match"):
        parse_snapshot(json.dumps(inventory))


@pytest.mark.parametrize("raw", ['{"schema_version":1,"schema_version":1}', '{"x":{"a":1,"a":2}}'])
def test_duplicate_json_keys(raw):
    with pytest.raises(SnapshotError, match="duplicate JSON"):
        parse_snapshot(raw)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_json_numbers(constant):
    with pytest.raises(SnapshotError, match="non-finite"):
        parse_snapshot('{"schema_version":' + constant + "}")


def test_deeply_nested_input_is_friendly_error():
    with pytest.raises(SnapshotError):
        parse_snapshot("[" * 2000 + "]" * 2000)


def test_oversized_integer_is_friendly_error():
    with pytest.raises(SnapshotError):
        parse_snapshot('{"schema_version":' + "1" * 5000 + "}")


def test_server_order_normalized(inventory):
    another = {**inventory["servers"][0], "id": "aaa-demo"}
    inventory["servers"].append(another)
    assert [s.id for s in parse_snapshot(json.dumps(inventory)).servers] == [
        "aaa-demo",
        "server-demo-01",
    ]


def test_utf8_bom_file(inventory, tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(inventory), encoding="utf-8-sig")
    assert load_snapshot(path).host.name == "compute-demo-01"


def test_invalid_encoding(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(SnapshotError, match="UTF-8"):
        load_snapshot(path)


def test_missing_file(tmp_path):
    with pytest.raises(SnapshotError, match="cannot read"):
        load_snapshot(tmp_path / "missing.json")


def test_directory_is_not_a_snapshot(tmp_path):
    with pytest.raises(SnapshotError, match="cannot read"):
        load_snapshot(tmp_path)


def test_oversize_file(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_SNAPSHOT_BYTES + 1))
    with pytest.raises(SnapshotError, match="5 MiB"):
        load_snapshot(path)
