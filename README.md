# openstack-maintenance-check

Explain what needs attention before moving workloads off an OpenStack compute
host for maintenance.

**Status: Phase 1 — offline snapshot analysis.** Reads a normalized JSON file or
bundled synthetic demo and reports preliminary source-host findings. Live cloud
collection, workload constraints, destination checks, and Ansible integration
are planned in the [phased implementation plan](docs/PLAN.md).

## Run the demo

Requires Python 3.11+. From this repository:

```sh
python -m pip install .
maintenance-check demo
maintenance-check demo --scenario incomplete --format json
maintenance-check demo --scenario clear
```

The default demo exits **1 intentionally**: it contains two blockers and one
warning. `incomplete` also exits 1; `clear` exits 0. All demos are synthetic and
work without cloud credentials, network access, or OpenStack services.

The default report includes:

```text
Maintenance preflight: "compute-demo-01"
Outcome: blocked
Captured at: 2026-09-05T08:00:00+00:00 (historical snapshot; freshness not checked)
Servers observed: 3; inventory complete: true
Findings: 2 blocker, 1 warning, 0 unknown
```

Each finding includes its rule ID, resource, reason, evidence, and next check.

## Analyze a snapshot

```sh
maintenance-check check --snapshot inventory.json
maintenance-check check --snapshot inventory.json --format json
```

Use the [normalized snapshot schema](docs/SNAPSHOT.md); raw Nova responses are
not accepted. Reports describe the supplied evidence at capture time. This
version does not check snapshot age or contact a cloud to validate the evidence.
Keep operational snapshots in the ignored `snapshots/` directory.

## Implemented checks

| Rule | What it reports |
| --- | --- |
| `inventory.complete` | Unknown when the producer cannot assert a complete host inventory |
| `host.service` | Source compute service down; missing/unrecognized health or scheduling status |
| `server.status` | ERROR and transition states; review of non-ACTIVE lifecycle states; unknown states |
| `server.task` | An observed operation in progress, or unavailable task-state evidence |

`state: up/down` describes service health. `status: enabled/disabled` describes
scheduling; disabling scheduling does not itself make a host unhealthy.
See [rule semantics](docs/RULES.md) for the full status classifications.

**No known blockers means only that these four checks found nothing.** It does
not establish migration feasibility or authorize a reboot. Destination capacity,
placement, CPU/device compatibility, storage, networking, locks, and Nova's
migration prechecks are unassessed. Every report includes these limitations.

| Exit | Meaning |
| --- | --- |
| 0 | No findings in the implemented source checks |
| 1 | Blockers, warnings, or unknown evidence found |
| 2 | Invalid command-line arguments |
| 3 | Snapshot unreadable or invalid |

JSON is a report contract with `report_schema_version: 1`, structured inventory,
findings, summary counts, coverage, and limitations. Errors go to stderr and do
not emit a partial JSON report. Text output quotes input values to escape
terminal control characters.

## Develop

```sh
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install -e ".[dev]"
pre-commit install
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
pre-commit run --all-files
```

The package uses the Python standard library at runtime. Tests exercise input
validation, rule behavior, output contracts, and CLI exit codes without a cloud.
CI is configured for Linux on Python 3.11–3.14 and Windows on Python 3.12.
Configured CI is not a claim that a hosted run has already passed.

Layout: `snapshot.py` validates evidence, `models.py` defines immutable types,
`checks.py` evaluates pure rules, `reporting.py` renders the report, and `cli.py`
connects these pieces. Packaged demo data lives under `data/`.

Licensed under [Apache-2.0](LICENSE).
