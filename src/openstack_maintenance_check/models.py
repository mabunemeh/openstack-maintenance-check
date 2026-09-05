"""Normalized evidence and report types, independent of collection and output."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


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
class Snapshot:
    captured_at: datetime
    host: Host
    servers_complete: bool
    servers: tuple[Server, ...]


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
