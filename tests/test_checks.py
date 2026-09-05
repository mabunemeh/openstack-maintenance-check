import json

import pytest

from openstack_maintenance_check.checks import evaluate
from openstack_maintenance_check.snapshot import parse_snapshot


def report_for(inventory):
    return evaluate(parse_snapshot(json.dumps(inventory)))


def test_idle_active_server_and_disabled_host_pass_baseline(inventory):
    report = report_for(inventory)
    assert report.outcome == "no_known_blockers"
    assert report.exit_code == 0
    assert not report.findings
    assert len(report.checks_run) == 4
    assert any("not assessed" in limitation for limitation in report.limitations)


def test_enabled_host_is_valid_health_evidence(inventory):
    inventory["host"]["status"] = "enabled"
    assert report_for(inventory).exit_code == 0


def test_complete_empty_host_passes_only_baseline(inventory):
    inventory["servers"] = []
    report = report_for(inventory)
    assert report.exit_code == 0
    assert report.outcome == "no_known_blockers"
    assert report.limitations


def test_partial_empty_inventory_never_passes(inventory):
    inventory["servers"] = []
    inventory["servers_complete"] = False
    report = report_for(inventory)
    assert report.exit_code == 1
    assert report.outcome == "incomplete"
    assert report.findings[0].rule_id == "inventory.complete"


def test_empty_inventory_does_not_hide_down_host(inventory):
    inventory["servers"] = []
    inventory["host"]["state"] = "down"
    report = report_for(inventory)
    assert report.outcome == "blocked"
    assert report.findings[0].rule_id == "host.service"


@pytest.mark.parametrize(
    ("field", "value"), [("state", None), ("state", "UP"), ("status", None), ("status", "other")]
)
def test_unknown_service_observations(inventory, field, value):
    inventory["host"][field] = value
    report = report_for(inventory)
    assert report.outcome == "incomplete"
    assert dict(report.findings[0].evidence)[field] == value


@pytest.mark.parametrize(
    "status",
    [
        "ERROR",
        "BUILD",
        "REBUILD",
        "REBOOT",
        "HARD_REBOOT",
        "MIGRATING",
        "RESIZE",
        "REVERT_RESIZE",
        "VERIFY_RESIZE",
        "PASSWORD",
    ],
)
def test_known_error_and_pending_states_block(inventory, status):
    inventory["servers"][0]["status"] = status
    report = report_for(inventory)
    assert report.outcome == "blocked"
    assert report.findings[0].rule_id == "server.status"
    assert report.findings[0].next_check


@pytest.mark.parametrize(
    "status",
    ["SHUTOFF", "PAUSED", "SUSPENDED", "SHELVED", "SHELVED_OFFLOADED", "SOFT_DELETED", "DELETED"],
)
def test_lifecycle_states_require_review_without_claiming_impossible_migration(inventory, status):
    inventory["servers"][0]["status"] = status
    report = report_for(inventory)
    assert report.outcome == "attention_required"
    assert report.exit_code == 1
    assert report.findings[0].severity == "warning"


@pytest.mark.parametrize("status", [None, "FUTURE_STATE", "active"])
def test_unknown_instance_status(inventory, status):
    inventory["servers"][0]["status"] = status
    assert report_for(inventory).outcome == "incomplete"


def test_missing_task_attribute_does_not_mean_idle(inventory):
    del inventory["servers"][0]["task_state"]
    report = report_for(inventory)
    assert report.outcome == "incomplete"
    assert report.findings[0].rule_id == "server.task"
    assert dict(report.findings[0].evidence) == {"task_state_known": False}


@pytest.mark.parametrize("task", ["image_snapshot", "migrating", "new_future_task"])
def test_any_observed_task_blocks_even_when_status_active(inventory, task):
    inventory["servers"][0]["task_state"] = task
    report = report_for(inventory)
    assert report.outcome == "blocked"
    assert report.findings[0].rule_id == "server.task"
    assert dict(report.findings[0].evidence) == {"task_state": task}


def test_blockers_preserve_unknowns_and_warnings(inventory):
    inventory["servers_complete"] = False
    inventory["servers"][0]["status"] = "SHUTOFF"
    inventory["servers"][0]["task_state"] = "image_snapshot"
    report = report_for(inventory)
    assert report.outcome == "blocked"
    assert [f.severity for f in report.findings] == ["blocker", "unknown", "warning"]


def test_unknown_takes_precedence_over_warning(inventory):
    inventory["servers_complete"] = False
    inventory["servers"][0]["status"] = "SHUTOFF"
    assert report_for(inventory).outcome == "incomplete"
