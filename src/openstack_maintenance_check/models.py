"""Normalized evidence and report types, independent of collection and output."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Codes, not exception text: exported evidence must not contain response bodies,
# credentials, URLs, or arbitrary SDK/configuration error messages.
COLLECTION_ISSUES = {
    "sdk_unavailable": "Live collection requires installation with the [live] extra.",
    "configuration_failed": "Cannot initialize named cloud; check clouds.yaml and auth settings.",
    "host_not_found": "No matching nova-compute service; check exact host name and visibility.",
    "ambiguous_host": "Multiple matching nova-compute services; the source host is ambiguous.",
    "forbidden": "Nova denied inventory access; all-project visibility is unconfirmed.",
    "authentication_failed": "Authentication failed or expired during collection.",
    "connection_failed": "A connection or timeout failure interrupted collection.",
    "request_failed": "Nova rejected or failed an inventory request.",
    "invalid_response": "Nova returned a malformed inventory response.",
    "missing_identity": "Some returned instances lack an ID or observable host placement.",
    "host_mismatch": "Some returned instances do not match the requested source host.",
    "duplicate_server": "An instance ID appeared repeatedly; inventory may have changed.",
    "pagination_incomplete": "Pagination could not be completed reliably.",
    "invalid_task_state": "Some task-state values were malformed and remain unknown.",
}


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Host:
    name: str
    state: str | None
    status: str | None


@dataclass(frozen=True)
class Server:
    id: str
    name: str | None
    host: str
    status: str | None
    task_state: str | None
    task_state_known: bool


@dataclass(frozen=True)
class Collection:
    started_at: datetime
    completed_at: datetime
    compute_api_version: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    host: Host
    servers_complete: bool
    servers: tuple[Server, ...]
    collection: Collection | None = None


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    resource_type: str
    resource_id: str
    reason: str
    evidence: tuple[tuple[str, str | bool | None], ...]
    next_check: str


@dataclass(frozen=True)
class Report:
    snapshot: Snapshot
    findings: tuple[Finding, ...]
    checks_run: tuple[str, ...]
    limitations: tuple[str, ...]
    analysis_mode: str = "historical"
    evaluated_at: datetime | None = None
    max_age_seconds: int | None = None

    @property
    def outcome(self) -> str:
        severities = {finding.severity for finding in self.findings}
        if Severity.BLOCKER in severities:
            return "blocked"
        if Severity.UNKNOWN in severities:
            return "incomplete"
        if Severity.WARNING in severities:
            return "attention_required"
        return "no_known_blockers"

    @property
    def exit_code(self) -> int:
        return 1 if self.findings else 0
