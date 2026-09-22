"""Pure preliminary checks for planned source-host maintenance.

Status semantics: https://docs.openstack.org/api-ref/compute/
These are review rules, not Nova scheduler or hypervisor migration prechecks.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from .models import COLLECTION_ISSUES, Finding, Report, Severity, Snapshot

TRANSITION_STATUSES = frozenset(
    {
        "BUILD",
        "REBUILD",
        "REBOOT",
        "HARD_REBOOT",
        "MIGRATING",
        "RESIZE",
        "REVERT_RESIZE",
        "VERIFY_RESIZE",
        "PASSWORD",
    }
)
REVIEW_STATUSES = frozenset(
    {"SHUTOFF", "PAUSED", "SUSPENDED", "SHELVED", "SHELVED_OFFLOADED", "SOFT_DELETED", "DELETED"}
)

LIMITATIONS = (
    "Preliminary source checks only; no migration or reboot is authorized by this report.",
    "Historical snapshot analysis: findings describe capture-time evidence; "
    "freshness is not checked.",
    "Inventory completeness is asserted by the snapshot producer, not independently verified.",
    "Destination capacity, placement, CPU/device compatibility, storage, networking, and locks "
    "are not assessed.",
    "Nova scheduler and hypervisor migration prechecks have not run; migration success is unknown.",
)


def inventory_complete(snapshot: Snapshot) -> Iterable[Finding]:
    if not snapshot.servers_complete:
        yield Finding(
            "inventory.complete",
            Severity.UNKNOWN,
            "host",
            snapshot.host.name,
            "The server inventory is incomplete; additional workloads may be present.",
            (("servers_complete", False),),
            "Obtain a complete host inventory across all projects before planning maintenance.",
        )


def host_service(snapshot: Snapshot) -> Iterable[Finding]:
    host = snapshot.host
    if host.state == "down":
        yield Finding(
            "host.service",
            Severity.BLOCKER,
            "host",
            host.name,
            "The source compute service is down; "
            "planned live maintenance cannot be assessed normally.",
            (("state", host.state),),
            "Investigate source service health and choose planned maintenance or failure recovery.",
        )
    elif host.state != "up":
        yield Finding(
            "host.service",
            Severity.UNKNOWN,
            "host",
            host.name,
            "Source compute service health is unavailable or unrecognized.",
            (("state", host.state),),
            "Read the source nova-compute service state using an authorized administrative view.",
        )
    if host.status not in {"enabled", "disabled"}:
        yield Finding(
            "host.service",
            Severity.UNKNOWN,
            "host",
            host.name,
            "Source compute scheduling status is unavailable or unrecognized.",
            (("status", host.status),),
            "Confirm whether scheduling on this compute service is enabled or disabled.",
        )


def server_status(snapshot: Snapshot) -> Iterable[Finding]:
    for server in snapshot.servers:
        if server.status == "ACTIVE":
            continue
        if server.status == "ERROR":
            severity = Severity.BLOCKER
            reason = "The instance is in ERROR and requires investigation before planned movement."
            next_check = "Review the instance fault and establish a recovery or migration approach."
        elif server.status in TRANSITION_STATUSES:
            severity = Severity.BLOCKER
            reason = "The instance is in a transition or awaiting completion of an operation."
            next_check = "Resolve the existing operation and collect a new snapshot."
        elif server.status in REVIEW_STATUSES:
            severity = Severity.WARNING
            reason = (
                "The instance is not ACTIVE; review its lifecycle and intended migration method."
            )
            next_check = "Confirm host residency and the supported maintenance path for this state."
        else:
            severity = Severity.UNKNOWN
            reason = "The instance status is unavailable or not covered by this rule set."
            next_check = (
                "Obtain the current Nova status and review support for this lifecycle state."
            )
        yield Finding(
            "server.status",
            severity,
            "server",
            server.id,
            reason,
            (("status", server.status),),
            next_check,
        )


def server_task(snapshot: Snapshot) -> Iterable[Finding]:
    for server in snapshot.servers:
        if not server.task_state_known:
            yield Finding(
                "server.task",
                Severity.UNKNOWN,
                "server",
                server.id,
                "Task state was not observed; an idle instance cannot be assumed.",
                (("task_state_known", False),),
                "Collect the task-state attribute with sufficient API visibility.",
            )
        elif server.task_state is not None:
            yield Finding(
                "server.task",
                Severity.BLOCKER,
                "server",
                server.id,
                "The instance has an operation in progress.",
                (("task_state", server.task_state),),
                "Wait for or investigate the existing operation, then collect a new snapshot.",
            )


CHECKS: tuple[tuple[str, Callable[[Snapshot], Iterable[Finding]]], ...] = (
    ("inventory.complete", inventory_complete),
    ("host.service", host_service),
    ("server.status", server_status),
    ("server.task", server_task),
)


def collection_issues(snapshot: Snapshot) -> Iterable[Finding]:
    if snapshot.collection:
        for code in snapshot.collection.issues:
            yield Finding(
                "collection.issues",
                Severity.UNKNOWN,
                "host",
                snapshot.host.name,
                COLLECTION_ISSUES[code],
                (("issue_code", code),),
                "Resolve collection or visibility problems, then collect a new inventory.",
            )


def evaluate(
    snapshot: Snapshot,
    *,
    historical: bool = True,
    live: bool = False,
    now: datetime | None = None,
    max_age_seconds: int = 300,
) -> Report:
    """Evaluate capture-time evidence; CLI checks enable freshness by default."""
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    historical = historical and not live
    evaluated_at = None if historical else (now or datetime.now(UTC))
    checks = list(CHECKS)
    if snapshot.collection:
        checks.append(("collection.issues", collection_issues))
    priority = {Severity.BLOCKER: 0, Severity.UNKNOWN: 1, Severity.WARNING: 2}
    findings = [finding for _, check in checks for finding in check(snapshot)]
    checks_run = [name for name, _ in checks]
    if evaluated_at:
        checks_run.append("evidence.freshness")
        age = (evaluated_at - snapshot.captured_at).total_seconds()
        completed = (
            snapshot.collection.completed_at if snapshot.collection else snapshot.captured_at
        )
        if age > max_age_seconds or (completed - evaluated_at).total_seconds() > 30:
            findings.append(
                Finding(
                    "evidence.freshness",
                    Severity.UNKNOWN,
                    "host",
                    snapshot.host.name,
                    "Evidence is stale or dated in the future; current state is unconfirmed.",
                    (("captured_at", snapshot.captured_at.isoformat()),),
                    "Collect fresh evidence and verify clocks; --historical is for past analysis.",
                )
            )
    ordered = tuple(
        sorted(
            findings,
            key=lambda finding: (
                priority[finding.severity],
                finding.resource_type,
                finding.resource_id,
                finding.rule_id,
                finding.reason,
            ),
        )
    )
    limitations = list(LIMITATIONS)
    if not historical:
        limitations[1] = (
            "Freshness is measured from collection start; collection is not atomic and "
            "cloud state can change after observation."
        )
    if live:
        limitations[2] = (
            "All-project inventory was requested; completeness depends on Nova policy and "
            "successful collection, not an independent cloud-wide census."
        )
    return Report(
        snapshot,
        ordered,
        tuple(checks_run),
        tuple(limitations),
        "live" if live else "historical" if historical else "snapshot",
        evaluated_at,
        None if historical else max_age_seconds,
    )
