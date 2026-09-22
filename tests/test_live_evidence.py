import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from openstack_maintenance_check import cli
from openstack_maintenance_check import snapshot as snapshot_module
from openstack_maintenance_check.checks import evaluate
from openstack_maintenance_check.collector import CollectionError
from openstack_maintenance_check.models import Collection
from openstack_maintenance_check.reporting import render_json, render_text
from openstack_maintenance_check.snapshot import (
    SnapshotError,
    export_snapshot,
    load_snapshot,
    parse_snapshot,
    snapshot_dict,
)

NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)


@pytest.fixture
def collected(inventory):
    inventory["captured_at"] = NOW.isoformat()
    original = parse_snapshot(json.dumps(inventory))
    return replace(original, collection=Collection(NOW, NOW + timedelta(seconds=2), "2.1"))


def test_v2_roundtrip_preserves_missing_task_state(collected, tmp_path):
    snapshot = replace(collected, servers=(replace(collected.servers[0], task_state_known=False),))
    path = tmp_path / "snapshot.json"
    export_snapshot(snapshot, path)
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 2
    assert "task_state" not in raw["servers"][0]
    assert load_snapshot(path) == snapshot


def test_v1_stays_readable_and_exportable(inventory, tmp_path):
    original = parse_snapshot(json.dumps(inventory))
    path = tmp_path / "snapshot.json"
    export_snapshot(original, path)
    assert load_snapshot(path) == original
    assert json.loads(path.read_text())["schema_version"] == 1


def test_partial_snapshot_roundtrip_retains_issues(collected):
    partial = replace(
        collected,
        servers_complete=False,
        collection=replace(collected.collection, issues=("forbidden",)),
    )
    replay = parse_snapshot(json.dumps(snapshot_dict(partial)))
    assert replay == partial
    assert evaluate(replay, historical=True).outcome == "incomplete"


@pytest.mark.parametrize(
    "issues",
    [
        ["arbitrary-sensitive-text"],
        [None],
        "forbidden",
        [{"code": "forbidden"}],
        ["forbidden", "forbidden"],
    ],
)
def test_invalid_issue_codes_rejected(collected, issues):
    data = snapshot_dict(collected)
    data["collection"]["issues"] = issues
    with pytest.raises(SnapshotError, match="collection.issues"):
        parse_snapshot(json.dumps(data))


def test_issues_cannot_be_hidden_by_complete_flag(collected):
    data = snapshot_dict(collected)
    data["collection"]["issues"] = ["forbidden"]
    with pytest.raises(SnapshotError, match="must be false"):
        parse_snapshot(json.dumps(data))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("started_at", "2026-09-22T07:59:00Z"),
        ("completed_at", "2026-09-22T07:59:00Z"),
        ("compute_api_version", "2.999"),
        ("completed_at", "2026-09-22T08:00:02"),
    ],
)
def test_collection_metadata_validation(collected, key, value):
    data = snapshot_dict(collected)
    data["collection"][key] = value
    with pytest.raises(SnapshotError, match="collection"):
        parse_snapshot(json.dumps(data))


def test_collection_schema_version_boundaries(collected):
    data = snapshot_dict(collected)
    data["schema_version"] = 1
    with pytest.raises(SnapshotError, match="forbidden"):
        parse_snapshot(json.dumps(data))
    data["schema_version"] = 2
    del data["collection"]
    with pytest.raises(SnapshotError, match="required"):
        parse_snapshot(json.dumps(data))


def test_export_never_overwrites(collected, tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("keep me")
    with pytest.raises(SnapshotError, match="already exists"):
        export_snapshot(collected, path)
    assert path.read_text() == "keep me"


def test_export_missing_parent_error_is_safe(collected, tmp_path):
    with pytest.raises(SnapshotError, match="parent directory"):
        export_snapshot(collected, tmp_path / "missing" / "snapshot.json")


def test_export_obeys_import_size_limit(collected, tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "MAX_SNAPSHOT_BYTES", 20)
    path = tmp_path / "snapshot.json"
    with pytest.raises(SnapshotError, match="export limit"):
        export_snapshot(collected, path)
    assert not path.exists()


def test_live_report_freshness_and_timing(collected):
    report = evaluate(collected, live=True, now=NOW + timedelta(seconds=10))
    assert report.exit_code == 0
    data = json.loads(render_json(report))
    assert data["analysis_mode"] == "live"
    assert data["freshness"]["age_seconds"] == 10
    assert data["freshness"]["max_age_seconds"] == 300
    assert data["collection"]["compute_api_version"] == "2.1"
    text = render_text(report)
    assert "duration: 2s" in text
    assert "historical snapshot" not in text
    assert "freshness not checked" not in text


@pytest.mark.parametrize(
    ("seconds", "outcome"), [(300, "no_known_blockers"), (301, "incomplete"), (-31, "incomplete")]
)
def test_freshness_boundaries(collected, seconds, outcome):
    assert (
        evaluate(collected, historical=False, now=NOW + timedelta(seconds=seconds)).outcome
        == outcome
    )


def test_collection_duration_counts_toward_staleness(collected):
    snapshot = replace(
        collected,
        collection=replace(collected.collection, completed_at=NOW + timedelta(seconds=400)),
    )
    report = evaluate(snapshot, live=True, now=NOW + timedelta(seconds=400))
    assert report.outcome == "incomplete"
    assert report.findings[0].rule_id == "evidence.freshness"


def test_historical_replay_skips_only_age_check(collected):
    snapshot = replace(collected, servers=(replace(collected.servers[0], status="ERROR"),))
    report = evaluate(snapshot, historical=True, now=NOW + timedelta(days=10))
    assert report.outcome == "blocked"
    assert report.analysis_mode == "historical"
    assert report.evaluated_at is None
    assert "evidence.freshness" not in report.checks_run
    assert "freshness not checked" in render_text(report)


def test_custom_freshness_threshold(collected):
    assert (
        evaluate(
            collected, historical=False, now=NOW + timedelta(seconds=30), max_age_seconds=20
        ).outcome
        == "incomplete"
    )
    with pytest.raises(ValueError, match="positive"):
        evaluate(collected, max_age_seconds=0)


@pytest.mark.parametrize(
    "args",
    [
        ["--cloud", "demo", "--host", "compute-demo", "--snapshot", "fixture.json"],
        ["--cloud", "demo"],
        ["--cloud", "demo", "--host", "compute-demo", "--historical"],
        ["--snapshot", "fixture.json", "--host", "compute-demo"],
        ["--snapshot", "fixture.json", "--export-snapshot", "output.json"],
        ["--snapshot", "fixture.json", "--max-age-seconds", "0"],
        ["--snapshot", "fixture.json", "--max-age-seconds", "-1"],
        ["--snapshot", "fixture.json", "--max-age-seconds", "nan"],
        ["--cloud", " ", "--host", "compute-demo"],
        ["--cloud", "demo", "--host", ""],
    ],
)
def test_cli_invalid_combinations_fail_before_collection(args, monkeypatch):
    def unexpected(*args):
        pytest.fail("invalid CLI attempted live collection")

    monkeypatch.setattr(cli, "collect_cloud", unexpected)
    with pytest.raises(SystemExit) as exc:
        cli.main(["check", *args])
    assert exc.value.code == 2


def test_cli_live_collection_and_opt_in_export(collected, monkeypatch, tmp_path, capsys):
    def fake_collect(cloud, host):
        assert cloud == "demo-profile" and host == "compute-demo-01"
        current = datetime.now(UTC)
        return replace(
            collected, captured_at=current, collection=Collection(current, current, "2.1")
        )

    monkeypatch.setattr(cli, "collect_cloud", fake_collect)
    path = tmp_path / "evidence.json"
    assert (
        cli.main(
            [
                "check",
                "--cloud",
                "demo-profile",
                "--host",
                "compute-demo-01",
                "--export-snapshot",
                str(path),
                "--max-age-seconds",
                "900",
                "--format",
                "json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    assert report["analysis_mode"] == "live"
    assert load_snapshot(path).collection is not None


def test_cli_collection_failure_has_no_json_or_traceback(monkeypatch, capsys):
    def fail(*args):
        raise CollectionError("forbidden")

    monkeypatch.setattr(cli, "collect_cloud", fail)
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["check", "--cloud", "demo-profile", "--host", "compute-demo-01", "--format", "json"]
        )
    assert exc.value.code == 3
    output = capsys.readouterr()
    assert output.out == "" and "Traceback" not in output.err


def test_cli_snapshot_stale_by_default_and_historical_opt_in(inventory, tmp_path, capsys):
    inventory["captured_at"] = "2000-01-01T00:00:00Z"
    path = tmp_path / "old.json"
    path.write_text(json.dumps(inventory))
    assert cli.main(["check", "--snapshot", str(path), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "incomplete"
    assert cli.main(["check", "--snapshot", str(path), "--historical"]) == 0
    assert "freshness not checked" in capsys.readouterr().out


def test_demo_does_not_initialize_sdk(monkeypatch, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "openstack", None)
    assert cli.main(["demo", "--scenario", "clear"]) == 0
    assert "no_known_blockers" in capsys.readouterr().out
