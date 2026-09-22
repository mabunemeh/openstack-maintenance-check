# openstack-maintenance-check — implementation plan

Created: 2026-09-05. This document separates planned capabilities from delivered
behavior. The README describes only what currently runs.

## Purpose and boundaries

Help an OpenStack operator answer: **What needs attention before moving the
workloads off a compute host for maintenance?** The output must explain the
evidence, its limits, and the next check for each finding.

This is a preflight assistant for planned maintenance on a functioning source
host. Recovery after host failure is a different workflow. The tool never
disables a compute service, migrates an instance, changes configuration, or
reboots a host. It does not replace Nova's scheduler or migration prechecks.

The portfolio goal is to demonstrate OpenStack operations, Python engineering,
and Ansible automation through a small, runnable tool with honest limitations.

## Delivery map

| Phase | Deliverable | Depends on | Completion gate |
| --- | --- | --- | --- |
| 1 | Offline report engine and runnable demos | None | Installed CLI, parser/rule/CLI tests, lint, package smoke test |
| 2 | Read-only Nova inventory collection | 1 | Adapter contract tests and opt-in lab inventory comparison |
| 3 | Workload-specific migration review | 2 | Documented rules with positive, negative, and unknown evidence cases |
| 4 | Destination suitability evidence | 3 | Lab comparisons with Nova; no scheduler-equivalence claim |
| 5 | Optional Ansible host evidence | 3; integrates with 4 | Read-only collection validated in disposable Linux lab |
| 6 | Operator reports and integration validation | 4, 5 | End-to-end lab scenarios and report review |
| 7 | First public release and portfolio demo | 6 | Release artifacts, green hosted CI, reproducible demo, verified docs |

Phases are sequential deliverables, each split into reviewable changes below.
Phase 5 can be deferred for a deliberately API-only release if its absence is
made explicit in the report. Public publishing and real-cloud operations are
separate from implementing the local project.

## Shared technical contract

- Python 3.11+, `src/` package layout, type hints, pytest, Ruff, Apache-2.0,
  pre-commit with gitleaks, and GitHub Actions. Phase 1 needs no runtime packages.
- Separate collection, normalized data, pure rules, and rendering. Rules never
  make network requests. Explicit registration keeps the rule set reviewable.
- Stable rule IDs and findings containing severity, resource, reason, evidence,
  and a next check. Findings are sorted deterministically.
- Severities: `blocker` (known obstacle within the stated workflow), `warning`
  (requires operator review), `unknown` (evidence missing or unsupported).
- Report outcomes: `blocked`, `attention_required`, `incomplete`, or
  `no_known_blockers`. Precedence is blocker, unknown, warning, then no findings;
  counts preserve all categories even when one determines the outcome.
- Exit codes: 0 = no findings in implemented checks; 1 = findings, including
  unknowns; 2 = invalid CLI arguments; 3 = input/collection failure. Exit 0 never
  asserts that the host can be rebooted or that a migration will succeed.
- Reports always include check coverage and limitations. Distinguish an empty
  complete inventory from missing/partial inventory. Null task state means
  observed idle; an absent task-state field means unavailable evidence.
- Inputs and reports are versioned. Demo fixtures are synthetic. Production
  snapshots may contain operational identifiers and must not be committed.
- Baseline reports describe evidence at snapshot capture time. Freshness policy
  and collection duration belong to Phase 2; historic replay remains explicit.

## Phase 1 — offline foundation

**Goal:** deliver something a reviewer can install and demonstrate without cloud
credentials, while fixing the evidence semantics before adding API complexity.

### 1A. Repository and data contract

1. Create `repos/openstack-maintenance-check` with package metadata, license,
   ignore rules, CI, and pre-commit configuration.
2. Define immutable host, server, snapshot, finding, and report models.
3. Implement a strict normalized JSON loader with `schema_version: 1`, a
   timezone-aware capture timestamp, one source host, an explicit inventory
   completeness flag, and a server list.
4. Reject malformed types, unsupported schemas, duplicate JSON keys, duplicate
   server IDs, extra fields, and servers assigned to another host. Missing
   optional evidence remains unknown; it is never inferred as healthy.
5. Document the input schema with examples, including unavailable task state.

### 1B. Pure baseline checks

1. Inventory completeness: partial listing produces an unknown finding.
2. Source service: `down` blocks this planned live-maintenance workflow; absent
   or unrecognized state is unknown. `disabled` is valid scheduling status,
   separate from service health, and does not itself block maintenance.
3. Instance status: `ERROR` and transition states require resolution; stopped
   or other stable states require operator review of the migration method.
   Unrecognized or absent status is unknown.
4. Task state: any observed non-null operation blocks starting another
   operation; missing task-state evidence is unknown.
5. Keep rule decisions explicitly scoped to preliminary source checks. CPU,
   storage, networking, destinations, locks, and migration feasibility are
   listed as unassessed until subsequent phases implement them.

### 1C. CLI, demonstrations, and gate

1. `maintenance-check check --snapshot FILE [--format text|json]` evaluates a
   local snapshot. It never reads credentials or creates a connection.
2. `maintenance-check demo --scenario blocked|clear|incomplete` loads packaged
   synthetic evidence through the same parser and engine.
3. Text output explains findings; JSON preserves structured evidence, coverage,
   summary, and limitations. Escape terminal control characters from input.
4. Tests cover malformed input, unknown evidence, complete empty inventory,
   disabled source service, transition/error states, deterministic reports,
   exit codes, and installed/bundled demo behavior.
5. Run tests, lint, formatting, package build, installed-wheel smoke tests, and
   secret scan. Configure Linux CI across Python 3.11–3.14 and Windows smoke
   coverage. Record local results separately from hosted CI results.

**Out of scope:** cloud SDK, credentials, raw Nova JSON import, HTML, resource
capacity calculations, Ansible, executable remediation, publishing.

**Acceptance:** a fresh local install can run all three demo scenarios; incomplete
evidence cannot yield exit 0; no README claim exceeds these implemented checks.

## Phase 2 — live Nova collection

**Goal:** collect the same normalized evidence from a selected cloud and host.

### 2A. Verified SDK adapter

- Verify exact `openstacksdk` calls and supported Nova microversions before
  implementation. Use named `clouds.yaml` profiles and normal SDK authentication.
- Add `check --cloud NAME --host HOST` mutually exclusive with `--snapshot`.
- List `nova-compute` services and resolve the exact source host. Avoid ambiguous
  hypervisor hostname/service-host assumptions; report missing/ambiguous matches.
- Collect all-project server details with pagination, explicitly inspecting host
  placement, status, task state, and visibility. Do not silently fall back to a
  tenant-only view and call it complete.

### 2B. Failure and evidence handling

- Preserve partial listings and per-field availability. Test missing admin
  attributes, policy denial, timeout, expired authentication, and interrupted
  pagination. Errors must not expose credentials or raw response bodies.
- Track collection start/end, collection issues, API versions, and evidence age.
  Add a documented freshness threshold; stale evidence produces unknowns unless
  explicitly replayed as historical analysis.
- Add opt-in sanitized snapshot export using allowlisted fields. Ignore exported
  inventory by default. Snapshot persistence must be an explicit user action.

**Acceptance:** mock adapter tests validate request scope and pagination; an
opt-in disposable lab run compares the tool's inventory with administrative CLI
inventory. No live deployment is required for the normal test suite.

## Phase 3 — workload-specific migration review

**Goal:** explain which workloads need a different migration path or more evidence.

### 3A. Enriched normalized evidence

- Resolve flavors and relevant extra specs, image properties, attached volumes,
  server locks, server groups, and relevant Neutron port attributes.
- Record source and availability for each field. Deleted flavors/images, denied
  access, and unsupported microversions must stay visible as unknowns.
- Version the schema when extending it and retain explicit v1 replay support.

### 3B. Evidence-backed rules

- CPU pinning, NUMA, hugepages, PCI/SR-IOV, vGPU, and other device requirements:
  flag required compatibility checks; do not declare them universally
  unmigratable. Capability depends on Nova/libvirt/QEMU/device support.
- Volume-backed vs local/ephemeral storage: explain the required migration path
  and what storage connectivity/capacity evidence remains missing.
- Locks and policy requirements: explain the actor-dependent restriction.
- Anti-affinity and placement restrictions: carry constraints into Phase 4.

**Acceptance:** every rule has an authoritative reference, a stated support
boundary, evidence attribution, and tests for finding/no-finding/unknown. Verify
release-specific behavior instead of copying historical OpenStack assumptions.

## Phase 4 — destination suitability evidence

**Goal:** compare an optional destination host with a workload's known needs.

### 4A. Destination collection and explanation

- Add optional `--destination HOST`. Verify service state, cell boundaries,
  relevant aggregate/trait constraints, and Placement inventory/allocations.
- Query allocation candidates using verified API semantics, accounting for
  resource classes, allocation ratios, reserved resources, and provider trees.
- Assess one workload at a time initially. Explain that independent candidates
  do not prove there is capacity for the entire source host simultaneously.

### 4B. Planning boundaries

- Add a clearly labeled capacity estimate only after resource accounting has
  fixtures for dedicated/shared CPUs, reserved capacity, and nested providers.
- Keep CPU compatibility, networking, storage reachability, concurrent cloud
  changes, and final scheduler/precheck decisions as explicit remaining checks.
- Do not submit migrations to test feasibility. Compare reports with separately
  authorized lab migration experiments instead.

**Acceptance:** reports distinguish verified incompatibilities from estimates and
unknowns; lab cases exercise insufficient capacity and restricted placement.

## Phase 5 — optional Ansible host evidence

**Goal:** demonstrate host operations skills and fill gaps unavailable through APIs.

### 5A. Collection-only playbook

- Create a narrowly scoped playbook/role for source and destination hosts.
- Collect allowlisted libvirt/QEMU versions, CPU capabilities, relevant migration
  configuration, mounts, and hugepage information using facts and specific
  commands. No arbitrary shell passthrough or configuration changes.
- Support a documented deployment layout first; containerized and host-installed
  services require separate adapters. Redact credentials before export.

### 5B. Merge and review

- Export a versioned evidence file and validate host identity/capture time before
  merging it with the API snapshot.
- Treat denied access and unsupported layouts as unknown. Correlate API and host
  evidence in existing rules instead of duplicating findings.

**Acceptance:** disposable VM tests show no configuration changes; malformed,
stale, mismatched, and partially collected evidence stays visible in the report.

## Phase 6 — operator workflow and integration validation

**Goal:** make the result practical for a maintenance review.

- Add a self-contained HTML report with escaped content, per-VM findings,
  evidence age, inventory counts, coverage, and a review checklist.
- Document check → resolve/review findings → independently perform maintenance
  → collect again. Command suggestions remain text; never auto-execute them.
- Build lab scenarios for idle/busy/error instances, unavailable compute,
  incomplete policy visibility, special flavors, and destination constraints.
- Test deterministic exports, redaction, large inventories, and partial API
  failures. Publish measured collection costs/limits only after measurement.
- Record a supported-version matrix and actual lab evidence; distinguish
  unit-tested behavior from behavior verified against a real cloud.

**Acceptance:** an operator can trace every conclusion to its evidence and see
what remains untested. HTML and JSON agree with the same report model.

## Phase 7 — release and profile presentation

**Goal:** publish a project whose visible claims match validated behavior.

- Review prior art, including Nova's native operations, Watcher, and Masakari;
  explain the specific maintenance-report workflow without claiming uniqueness.
- Finalize a concise README, architecture/rule documentation, contributing
  instructions, changelog, and a synthetic demo recording.
- Verify distribution/name availability before promising a PyPI install command.
  Build wheel/sdist and install each in a clean environment.
- Add release automation and trusted publishing only after repository ownership
  and release destination are established. Require hosted CI and secret scan.
- Prepare the GitHub repository description and profile entry. Publish/push when
  requested; record only real release dates and validation outcomes.

**Acceptance:** reproducible install, green hosted CI, downloadable validated
artifacts, honest compatibility matrix, and a short demo requiring no credentials.

## Sources and decisions to revisit

Authoritative references reviewed on 2026-09-05:

- [Nova Compute API](https://docs.openstack.org/api-ref/compute/): server status,
  task-state attributes, administrative visibility, and service health/status.
- [Nova live migration guide](https://docs.openstack.org/nova/latest/admin/live-migration-usage.html):
  operator checks, migration progress, and failure handling.
- [Nova migration configuration](https://docs.openstack.org/nova/latest/admin/configuring-migrations.html):
  prerequisites and deployment-dependent constraints.
- [GitHub setup-python](https://github.com/actions/setup-python) and
  [checkout](https://github.com/actions/checkout): CI action setup.

Review API details again at the start of each live-integration phase. No SDK
method contract, full compatibility claim, package-name availability, or
exhaustive prior-art assessment is implied by this initial plan.

## Execution record

### 2026-09-05 — Phase 1 implemented

Delivered the offline CLI, strict version-1 snapshot parser, immutable models,
four baseline checks, deterministic text/JSON reports, three packaged synthetic
demos, input/rule documentation, Apache-2.0 license, CI configuration, and
pre-commit hooks. No live SDK dependency or cloud operations were introduced.

Local validation on Windows, Python 3.12.10:

- 160 tests passed; combined statement/branch coverage 99.35%. All rules, parser,
  reporting, models, and CLI have full measured coverage. The two-line module
  entry point is exercised by a subprocess smoke test outside coverage tracing.
- Ruff lint and formatting checks passed.
- Wheel and source distribution built; the wheel was built from the source
  distribution, then installed without dependencies in a fresh virtualenv.
- From outside the checkout, installed demos returned `clear=0`, `blocked=1`,
  and `incomplete=1`, with valid JSON and expected findings. The installed console
  entry point also returned its version correctly.
- Gitleaks passed on staged project files. Repository hooks check formatting,
  whitespace, YAML, file size, and staged secrets. CI additionally scans committed
  history, because a normal CI checkout has no staged changes.

The sandbox's shared pytest temporary directory was inaccessible. The successful
test invocation used an ignored repository-local temporary directory:

```powershell
New-Item -ItemType Directory -Path .test-tmp -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest --basetemp .test-tmp/run-02 --tb=short
```

For a later sandbox run, use a fresh child directory under `.test-tmp`. Normal
development and CI can use `python -m pytest` with their standard temp directory.

**Validation at initial implementation:** hosted GitHub Actions, the Linux/Python
3.11–3.14 matrix, and real OpenStack behavior had not been run. The first phase
was prepared locally for review. Publication and hosted validation are recorded
separately below; later implementation phases are not started.

**Next discrete change:** Phase 2A — verify the SDK adapter contract and implement
read-only Nova host/service and all-project server collection, preserving the
existing offline execution path and evidence semantics.

### 2026-09-05 — Phase 1 published to GitHub

Published the first phase on the `main` branch of the public
[mabunemeh/openstack-maintenance-check repository](https://github.com/mabunemeh/openstack-maintenance-check).
The initial implementation commit is
[`fb840fb`](https://github.com/mabunemeh/openstack-maintenance-check/commit/fb840fbd775a6eba4f4c7dad07c021ad6943aa47).

Hosted CI runs the five platform/Python combinations, package build and installed
demo checks, repository hooks, and committed-history secret scanning. Its
[run history](https://github.com/mabunemeh/openstack-maintenance-check/actions/workflows/ci.yml)
records results for each pushed revision. No PyPI release, version tag, profile
edit, or live-cloud integration validation is part of this publication.

### 2026-09-22 — Phase 2 implementation; lab gate pending

Implemented locally on `feat/live-nova-collection`:

- **2A:** optional `live` dependency, named-profile SDK connection, exact source
  service resolution, and host-filtered all-project Nova pagination. Requests
  use the public authenticated Compute adapter with microversion 2.1 and retain
  original JSON field presence; normalized SDK resource defaults would otherwise
  erase the missing-vs-null task-state distinction. No SDK private fields are used.
- **2B:** partial evidence and fixed collection issue codes, sanitized error
  messages, collection timing/API metadata, freshness checks, historical replay,
  and explicit allowlisted snapshot export that refuses to overwrite files.
- Input schema 2 adds collection metadata; schema-1 snapshots remain readable.
  Output schema advances to 2. Normal snapshot checks now enforce a default
  300-second age limit; demos remain historical and explicit `--historical`
  preserves capture-time replay behavior.
- Added `docs/LIVE.md`, migration notes in the README/schema/rule documents,
  and a runnable offline lab inventory comparison helper. CI adds minimum-SDK
  coverage and verifies that a wheel installed without the SDK still runs demos
  and reports an actionable error for live commands.

Local verification on Windows/Python 3.12.10:

- 257 tests passed with openstacksdk 4.20.0 and again with the supported minimum
  4.17.0. Combined statement/branch coverage: 99.53%.
- Contract tests use the actual SDK connection/HTTP adapter with synthetic Nova
  responses. They verify request scope, microversion, pagination, partial rows,
  missing attributes, 401/403 errors, timeouts, malformed responses, cache bypass,
  redirect handling, export/replay, and CLI exit behavior.
- SDK internal deprecation warnings were observed and recorded in the workspace
  contribution log. They are not test failures or migration findings.
- Ruff lint/format, YAML, whitespace, large-file, and staged-secret hooks passed.
  Wheel and source distribution built successfully. The wheel installed without
  dependencies into a separate environment and ran all three demos outside the
  checkout; live mode without the SDK returned the documented exit-3 diagnostic.

**Gate still pending:** a real disposable-cloud inventory comparison using the
instructions in `docs/LIVE.md`. No cloud credentials or host were supplied for
this implementation, and no live environment was contacted. Hosted CI for this
phase will run when the branch is published; local mocked contract tests do not
certify a Nova release. Phase 3 has not started.

**Design limits:** microversion 2.1 is the bounded initial contract. API visibility
still depends on deployment policy, cell availability, and concurrent changes;
successful pagination is not an independent census or migration precheck.
