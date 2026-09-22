# openstack-maintenance-check

[![CI](https://github.com/mabunemeh/openstack-maintenance-check/actions/workflows/ci.yml/badge.svg)](https://github.com/mabunemeh/openstack-maintenance-check/actions/workflows/ci.yml)

Explain what needs attention before moving workloads off an OpenStack compute
host for maintenance.

**Status: Phase 2 — live Nova collection implemented; lab validation pending.**
Collects read-only source-host evidence from a named OpenStack cloud, or evaluates
a normalized JSON snapshot. Workload constraints, destination checks, and Ansible
integration remain in the [phased implementation plan](docs/PLAN.md).

## Run the demo

Requires Python 3.11+. Clone and install from source:

```sh
git clone https://github.com/mabunemeh/openstack-maintenance-check.git
cd openstack-maintenance-check
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
maintenance-check check --snapshot inventory.json --historical
```

Use the [normalized snapshot schema](docs/SNAPSHOT.md); raw Nova responses are
not accepted. Evidence older than five minutes produces an unknown finding by
default; adjust with `--max-age-seconds`. Use `--historical` for capture-time
analysis that deliberately skips age checks. Bundled demos always use that mode.
Keep operational snapshots in the ignored `snapshots/` directory.

## Collect live Nova evidence

```sh
python -m pip install ".[live]"
maintenance-check check --cloud lab-admin --host compute-demo-01
maintenance-check check --cloud lab-admin --host compute-demo-01 --format json

# Optional export; the directory must exist and the file must be new:
mkdir snapshots
maintenance-check check --cloud lab-admin --host compute-demo-01 --export-snapshot snapshots/host.json
```

Use your existing named `clouds.yaml` profile and the exact **nova-compute service
host**, which may differ from the hypervisor hostname. Live collection requests
Compute microversion 2.1, requires administrative inventory visibility, and never
falls back to a tenant-only listing. It preserves partial results and reports
collection issues as unknown evidence. No cloud resources are changed.

See [live collection and lab verification](docs/LIVE.md) for API contracts,
permission requirements, limits, and an opt-in comparison with administrative
OpenStack CLI output. No real-cloud compatibility claim has been established yet.

## Implemented checks

| Rule | What it reports |
| --- | --- |
| `inventory.complete` | Unknown when the producer cannot assert a complete host inventory |
| `host.service` | Source compute service down; missing/unrecognized health or scheduling status |
| `server.status` | ERROR and transition states; review of non-ACTIVE lifecycle states; unknown states |
| `server.task` | An observed operation in progress, or unavailable task-state evidence |
| `collection.issues` | Missing identity/placement, partial pages, or failed requests in collected evidence |
| `evidence.freshness` | Stale or future-dated evidence; skipped only in historical analysis |

`state: up/down` describes service health. `status: enabled/disabled` describes
scheduling; disabling scheduling does not itself make a host unhealthy.
See [rule semantics](docs/RULES.md) for the full status classifications.

**No known blockers means only that the implemented checks found nothing.** It does
not establish migration feasibility or authorize a reboot. Destination capacity,
placement, CPU/device compatibility, storage, networking, locks, and Nova's
migration prechecks are unassessed. Every report includes these limitations.

| Exit | Meaning |
| --- | --- |
| 0 | No findings in the implemented source checks |
| 1 | Blockers, warnings, or unknown evidence found |
| 2 | Invalid command-line arguments |
| 3 | Invalid/unreadable snapshot, failed cloud setup/host resolution, or failed export |

JSON uses `report_schema_version: 2` with structured inventory, collection timing,
freshness policy, findings, counts, coverage, and limitations. Input snapshots
support versions 1 and 2. Errors go to stderr; failures after host resolution
produce an incomplete report with exit 1. Raw API exception bodies are omitted.
Text output escapes terminal control characters in input values.

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

Offline use needs only the Python standard library. The optional `live` extra
installs openstacksdk. Tests exercise real SDK HTTP requests against mocked Nova
responses, pagination, missing fields, failures, replay/export, and CLI behavior.
CI covers Linux on Python 3.11–3.14 and Windows on Python 3.12. See the
[CI history](https://github.com/mabunemeh/openstack-maintenance-check/actions/workflows/ci.yml)
for hosted validation. These tests do not contact a real OpenStack cloud.

Layout: `collector.py` gathers allowlisted evidence, `snapshot.py` validates it,
`models.py` defines immutable types,
`checks.py` evaluates pure rules, `reporting.py` renders the report, and `cli.py`
connects these pieces. Packaged demo data lives under `data/`.

Licensed under [Apache-2.0](LICENSE).
