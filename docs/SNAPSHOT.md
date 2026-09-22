# Normalized snapshot formats, versions 1 and 2

This is the tool's input contract, not a raw Nova API response. The current
release reads both versions. The live collector exports version 2, which adds
collection timing and issue codes. Existing version-1 files remain readable.

```json
{
  "schema_version": 1,
  "captured_at": "2026-09-05T08:00:00Z",
  "host": {
    "name": "compute-demo-01",
    "state": "up",
    "status": "disabled"
  },
  "servers_complete": true,
  "servers": [
    {
      "id": "server-demo-idle",
      "name": "demo-web",
      "host": "compute-demo-01",
      "status": "ACTIVE",
      "task_state": null
    }
  ]
}
```

## Fields

| Field | Required | Type and semantics |
| --- | --- | --- |
| `schema_version` | Yes | Integer 1 or 2; booleans and floating-point versions are rejected |
| `captured_at` | Yes | ISO 8601 timestamp with timezone; rendered in UTC |
| `host` | Yes | Object describing exactly one source compute service |
| `host.name` | Yes | Non-empty compute service host name |
| `host.state` | No | `up` or `down`; missing/null/unrecognized means unknown |
| `host.status` | No | `enabled` or `disabled`; missing/null/unrecognized means unknown |
| `servers_complete` | Yes | Boolean: producer asserts all workloads on this host are listed |
| `servers` | Yes | Array; may be empty if inventory is genuinely empty |
| `servers[].id` | Yes | Non-empty unique server identifier |
| `servers[].host` | Yes | Must exactly match `host.name` |
| `servers[].name` | No | Non-empty display name or null; not used for decisions |
| `servers[].status` | No | Nova status string; missing/null/unrecognized means unknown |
| `servers[].task_state` | No | Non-empty task string, or null for **observed idle** |

**Task state is the exception to null-as-unknown:** an explicit `task_state: null`
means the API attribute was observed and idle. Omit the key entirely if the
attribute was unavailable. This creates an unknown finding. Do not use `.get()`
to normalize an absent API task-state attribute into a null value.

`servers_complete: true` is a producer assertion. A tenant-scoped listing is not
sufficient evidence of a complete host inventory. The live collector sets it only
after the all-project host listing finishes without collection issues. This is
not an independent census of the cloud; policy and concurrent changes can affect
visibility. An offline reader cannot independently verify this assertion.

## Version 2 collection metadata

Version 2 requires a `collection` object; version 1 forbids it. All other fields
and the missing-vs-null task-state distinction are unchanged.

```json
{
  "started_at": "2026-09-22T08:00:00+00:00",
  "completed_at": "2026-09-22T08:00:02+00:00",
  "compute_api_version": "2.1",
  "issues": []
}
```

`started_at` must equal `captured_at`, and completion must be at or after start.
Both timestamps require timezones. `compute_api_version` records the requested
microversion; this collector explicitly requests `2.1` and does not fall back.
`issues` contains unique recognized codes from `models.COLLECTION_ISSUES`, never
exception messages, URLs, or arbitrary response text. If any issues are present,
`servers_complete` must be false. Unknown code strings are rejected.

Export retains only host/server identifiers, display names, state observations,
timestamps, API version, and issue codes. Credentials, profile names, endpoints,
metadata, user data, addresses, and unrelated API fields are not copied. Names
and identifiers may themselves be sensitive; allowlisting is not anonymization.

## Validation and interpretation

- UTF-8 JSON, optional UTF-8 BOM when loading a file, maximum file size 5 MiB.
- Unknown fields and duplicate object keys are rejected. This catches typos and
  prevents accidental import of unrelated API payloads.
- All non-null strings must be non-empty. State values are case-sensitive;
  unfamiliar values produce unknown findings instead of case conversion.
- Server IDs must be unique; wrong-host records are rejected rather than silently
  filtered. The caller must normalize the source host inventory first.
- Malformed types, unsupported schema versions, and timestamps without timezone
  produce input error exit 3. Valid but incomplete evidence produces a report
  and exit 1. These are distinct conditions.
- `check` evaluates freshness by default: five minutes from `captured_at`,
  adjustable using `--max-age-seconds`. More than 30 seconds of future clock skew
  also produces unknown evidence. `--historical` skips freshness only; all other
  checks still run. Demo commands explicitly use historical analysis.
- Input order does not affect report ordering. Servers sort by ID; findings sort
  by severity (blocker, unknown, warning), resource, rule ID, and reason.

Optional fields with incorrect types are input errors, not unknowns. Unknown
means absent/null/unrecognized evidence, not a license to ignore malformed data.

## Minimal incomplete inventory

```json
{
  "schema_version": 1,
  "captured_at": "2026-09-05T08:00:00+00:00",
  "host": {"name": "compute-demo-01"},
  "servers_complete": false,
  "servers": []
}
```

This yields unknown findings for inventory completeness, service health, and
scheduling status. Empty inventory never overrides missing evidence.

## Output is a separate contract

The JSON report uses `report_schema_version: 2`, not `schema_version`, and cannot
be fed directly into `check`. It includes `task_state_known` in each normalized
server so consumers can distinguish unavailable evidence from observed idle.
Counts are counts of findings, not unique affected servers. A blocked report can
also contain warnings and unknowns; the summary retains every category.

Report version 2 adds `collection`, `freshness`, and explicit `analysis_mode`
values `live`, `snapshot`, and `historical`. Version-1 report consumers must
recognize this schema change; input version-1 compatibility is independent.

Snapshots and reports can contain internal names and identifiers. Store them
outside tracked files (for example `snapshots/` and `reports/`). The bundled
examples use only synthetic names and identifiers.
