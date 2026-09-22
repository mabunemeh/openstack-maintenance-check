"""Render the same report model as structured JSON or plain, terminal-safe text."""

import json
from dataclasses import asdict
from typing import Any

from . import __version__
from .models import Report, Severity
from .snapshot import snapshot_dict


def report_dict(report: Report) -> dict[str, Any]:
    snapshot = report.snapshot
    return {
        "report_schema_version": 2,
        "tool_version": __version__,
        "analysis_mode": report.analysis_mode,
        "captured_at": snapshot.captured_at.isoformat(),
        "collection": snapshot_dict(snapshot).get("collection"),
        "freshness": {
            "evaluated_at": report.evaluated_at.isoformat() if report.evaluated_at else None,
            "max_age_seconds": report.max_age_seconds,
            "age_seconds": (report.evaluated_at - snapshot.captured_at).total_seconds()
            if report.evaluated_at
            else None,
        },
        "host": asdict(snapshot.host),
        "inventory": {
            "servers_complete": snapshot.servers_complete,
            "server_count": len(snapshot.servers),
            "servers": [asdict(server) for server in snapshot.servers],
        },
        "outcome": report.outcome,
        "summary": {
            severity.value: sum(finding.severity == severity for finding in report.findings)
            for severity in Severity
        },
        "checks_run": list(report.checks_run),
        "findings": [
            {**asdict(finding), "evidence": dict(finding.evidence)} for finding in report.findings
        ],
        "limitations": list(report.limitations),
    }


def render_json(report: Report) -> str:
    return json.dumps(report_dict(report), indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def render_text(report: Report) -> str:
    data = report_dict(report)
    # JSON quoting preserves identifiers while escaping newlines, ESC, bidi controls,
    # and non-ASCII input. Dynamic values cannot inject terminal commands or headings.
    host = json.dumps(report.snapshot.host.name, ensure_ascii=True)
    freshness = (
        "historical snapshot; freshness not checked"
        if report.analysis_mode == "historical"
        else f"{report.analysis_mode}; maximum evidence age {report.max_age_seconds}s"
    )
    lines = [
        f"Maintenance preflight: {host}",
        f"Outcome: {report.outcome}",
        f"Captured at: {data['captured_at']} ({freshness})",
        f"Servers observed: {len(report.snapshot.servers)}; "
        f"inventory complete: {str(report.snapshot.servers_complete).lower()}",
        "Findings: "
        + ", ".join(f"{count} {severity}" for severity, count in data["summary"].items()),
        "Checks run: " + ", ".join(report.checks_run),
        "",
    ]
    if report.snapshot.collection:
        collection = report.snapshot.collection
        duration = (collection.completed_at - collection.started_at).total_seconds()
        lines.append(
            f"Collection completed: {collection.completed_at.isoformat()}; "
            f"duration: {duration:g}s; Compute API: {collection.compute_api_version}"
        )
    if not report.findings:
        lines.append("No known blockers in the implemented source checks.")
    for finding in report.findings:
        resource_id = json.dumps(finding.resource_id, ensure_ascii=True)
        lines.extend(
            [
                f"[{finding.severity.value.upper()}] {finding.rule_id} "
                f"{finding.resource_type}={resource_id}",
                f"  {finding.reason}",
                "  Evidence: " + json.dumps(dict(finding.evidence), ensure_ascii=True),
                f"  Next check: {finding.next_check}",
            ]
        )
    lines.extend(["", "Limitations:"])
    lines.extend(f"- {limitation}" for limitation in report.limitations)
    return "\n".join(lines) + "\n"
