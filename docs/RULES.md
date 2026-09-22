# Rule semantics

Scope: preliminary review before moving workloads off a functioning source host
for planned maintenance. These rules do not perform Nova migration prechecks.

## inventory.complete

`servers_complete: false` produces `unknown`. Zero observed servers is not proof
that the source host has no workloads. A complete empty inventory passes this
one check; host service checks still run.

## host.service

- `state: down` produces `blocker` for this planned live-maintenance workflow.
  Investigate whether source recovery or a failure-recovery workflow is needed.
- Missing/null/unrecognized state produces `unknown`.
- Both `status: enabled` and `status: disabled` are valid observations.
  Disabling scheduling is not equivalent to a down compute service.
- Missing/null/unrecognized scheduling status produces `unknown`.

The rule does not disable scheduling or claim that an enabled source has been
prepared for maintenance. Host preparation remains an operator responsibility.

## server.status

| Status | Classification | Reason |
| --- | --- | --- |
| `ACTIVE` | No finding from this rule | Other checks and limitations still apply |
| `ERROR` | Blocker | Investigate the fault and establish a movement/recovery approach |
| `BUILD`, `REBUILD`, `REBOOT`, `HARD_REBOOT`, `MIGRATING`, `RESIZE`, `REVERT_RESIZE`, `VERIFY_RESIZE`, `PASSWORD` | Blocker | Existing operation or pending completion requires resolution |
| `SHUTOFF`, `PAUSED`, `SUSPENDED`, `SHELVED`, `SHELVED_OFFLOADED`, `SOFT_DELETED`, `DELETED` | Warning | Review lifecycle, current host residency, and supported maintenance method |
| Missing/null/any other value | Unknown | Evidence absent or status not covered |

A warning does not assert that a stopped, paused, or other non-ACTIVE instance
cannot migrate. The correct path depends on its state and the deployment. A
deleted or offloaded record calls for inventory/residency review, not an attempt
to migrate it.

## server.task

- An explicit null task state means observed idle and produces no finding.
- Any non-null task string produces `blocker`: review the ongoing operation
  before attempting another. This also handles newly introduced task names.
- An omitted task-state attribute produces `unknown`.

An instance may produce both a status finding and a task finding. These explain
two separate pieces of evidence; summary counts are findings, not VM counts.

## collection.issues

For version-2 evidence, each recorded collection issue produces an unknown
finding with a fixed explanation and next check. Missing host placement or IDs,
wrong-host rows, duplicate IDs, malformed task states, request failures, and
incomplete pagination cannot silently become a complete inventory. Known
host-matching rows are retained on a later failure. Collection failure before
the source host is resolved exits 3 instead of inventing a host report.

## evidence.freshness

Normal `check` commands require evidence no older than `--max-age-seconds`
(default 300). Age is measured from collection start, so a long collection does
not look newly captured at its end. A capture/completion timestamp more than 30
seconds in the future also produces unknown evidence. The threshold is a
positive integer. `--historical` skips this check for snapshot replay only; it
cannot override collection problems, status findings, or missing fields.

## Outcome and coverage

Blockers set outcome `blocked`; otherwise unknowns set `incomplete`; otherwise
warnings set `attention_required`; otherwise the outcome is `no_known_blockers`.
Exit 1 covers all three finding categories. Each report carries all findings,
even when one category determines the headline outcome.

Every report lists the four baseline `checks_run` entries. Version-2 evidence
also runs `collection.issues`; non-historical checks also run `evidence.freshness`.
A server check over an empty inventory has no server evidence to inspect.
Unimplemented migration checks are always listed in limitations, never counted
as passing checks.

## Sources

Status names and attribute meanings follow the
[Nova Compute API](https://docs.openstack.org/api-ref/compute/), reviewed on
2026-09-05. The blocker/warning classifications above are this tool's conservative
review policy for its stated workflow, not an assertion that Nova universally
rejects every operation in those states. The
[Nova live migration guide](https://docs.openstack.org/nova/latest/admin/live-migration-usage.html)
describes the separate operational checks and migration process.
