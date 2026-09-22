# Live Nova collection

Phase 2 adds a read-only collector. Its API contract is tested against the actual
openstacksdk HTTP adapter using synthetic responses. A real OpenStack lab run is
still required before claiming deployment compatibility.

## Authentication and supported contract

Install `.[live]`, configure a named `clouds.yaml` profile, and run:

```sh
maintenance-check check --cloud lab-admin --host compute-demo-01 --format json
```

The tool calls `openstack.connect(cloud=..., load_envvars=False,
api_timeout=30, compute_api_version="2.1")`. It uses normal SDK profile discovery,
authentication, TLS verification, and service-catalog endpoint discovery.
It does not add an insecure mode or accept credentials on its command line.
`OS_*` environment settings do not replace the named profile.

All inventory requests use the SDK's authenticated `connection.compute.get`
adapter with microversion **2.1**. This is intentionally a bounded initial
contract; higher microversions and other services belong to subsequent phases.
An unsupported version fails visibly without changing the request scope.

| Request | Parameters | Interpretation |
| --- | --- | --- |
| `GET /os-services` | `host=HOST`, `binary=nova-compute` | Resolve exactly one matching service; inspect state/status |
| `GET /servers/detail` | `host=HOST`, `all_tenants=1`, `limit=200` | Inspect original host, status, and task-state JSON fields |
| Additional server pages | Same parameters plus final row ID as `marker` | Preserve all-project scope on every request |

Authentication may exchange tokens with Keystone; no Nova resource mutation is
issued. Service resolution does not use a hypervisor hostname or fuzzy search.
Requests bypass SDK response caching and disable automatic redirect following.
Page links indicate whether more results exist; their URLs are never followed.
A full page without a link is followed by a marker request as a fallback.
Repeated rows/markers, malformed pagination, and the 1,000-page cap make the
inventory incomplete instead of allowing a loop or silently truncating it.

The request timeout is 30 seconds per SDK request, not a whole-command deadline.
Large inventories may take longer. Network/status retries are disabled for the
inventory requests; the authentication library may renew an expired token.

## Why preserve raw response keys?

In SDK resources, an absent task-state attribute and an explicit null both read
as `None`; `Resource.to_dict()` also materializes defaults. Phase 1 depends on
distinguishing unavailable evidence from observed idle. The collector therefore
uses the public authenticated adapter and allowlists original response keys
instead of SDK private resource fields or default-filled resource dictionaries.

## Permissions, partial results, and limits

The profile needs access to compute service inventory, host-filtered all-project
server details, and extended host/task attributes. Exact policy roles depend on
the deployment. The collector never falls back to a project-only request.

- Configuration/authentication/host-resolution failure: safe stderr message,
  exit 3, no report.
- Server-list failures after host resolution: retain previously observed rows,
  record an issue, mark inventory incomplete, return a report with exit 1.
- Missing task state: preserve the server with `task_state_known: false` and an
  unknown finding. It is not changed to observed idle.
- Missing ID or placement: omit the unassignable row but add an explicit issue;
  wrong-host rows are similarly excluded with an issue. Issues are deduplicated
  categories, not counts of omitted resources.
- Duplicate IDs: preserve the first observation and report ambiguous inventory.
- Malformed optional state/status observations become unknown, never healthy.

Successful collection means the requested listing completed without observable
collection issues. Nova policy and failures in cells can affect visibility;
filtered/paginated listings can omit inaccessible cells depending on deployment
configuration. Service resolution and server listing are separate observations,
not an atomic transaction. A host can gain/lose workloads during collection.
These reports do not certify that a host is empty or safe to reboot.

## Freshness and export

Capture time is collection start. Reports include start/end time, duration,
requested API version, issue codes, and evidence age. Normal `check` commands
flag evidence older than 300 seconds or more than 30 seconds into the future.
Use `--max-age-seconds` to change the age threshold, and `--historical` only when
replaying a file for past-state analysis.

```sh
mkdir snapshots
maintenance-check check --cloud lab-admin --host compute-demo-01 --export-snapshot snapshots/host.json
maintenance-check check --snapshot snapshots/host.json
maintenance-check check --snapshot snapshots/host.json --historical
```

Export is opt-in, uses a new file exclusively, and never overwrites existing
files. Parent directories must exist. POSIX creation permissions are 0600;
Windows uses the directory's ACLs. A failed write may leave a partial file;
choose a new path on retry. The 5 MiB limit matches the snapshot reader.
Allowlisted data can still contain sensitive names and IDs. Files under
`snapshots/` and `reports/` are ignored by Git.

## Opt-in disposable lab comparison

No test in normal CI needs credentials or contacts a real cloud. To validate
deployment behavior, use a disposable lab and an administrative profile. Install
the OpenStack CLI separately, then collect both inventories close together while
workloads are stable:

```sh
mkdir snapshots
maintenance-check check --cloud lab-admin --host compute-demo-01 --export-snapshot snapshots/tool.json
openstack --os-cloud lab-admin --os-compute-api-version 2.1 server list --all-projects --host compute-demo-01 -f json -c ID > snapshots/cli.json
python scripts/compare_inventory.py snapshots/tool.json snapshots/cli.json
```

An exit 1 from the tool can mean maintenance findings even with complete
inventory. The comparison requires complete collection and compares the server
ID sets; it exits 1 for differences/incomplete collection, 2 for invalid inputs,
and 0 for a match. Both lists can share policy limitations; matching IDs does not
prove migration feasibility or independent global visibility.

Record the Nova release, SDK version, configured policy scope, approximate VM
count, results, and collection duration without committing raw snapshots.
Check at least an idle host, a host with workloads, a profile lacking the required
visibility, and a lab-selected unavailable host. Do not inject failures or change
cloud resources merely to run this read-only comparison.

## References reviewed 2026-09-22

- [SDK Compute API](https://docs.openstack.org/openstacksdk/latest/user/proxies/compute.html)
- [SDK connection](https://docs.openstack.org/openstacksdk/latest/user/connection.html)
- [Keystone authenticated adapter](https://docs.openstack.org/keystoneauth/latest/api/keystoneauth1.adapter.html)
- [Nova Compute API](https://docs.openstack.org/api-ref/compute/)
- [Nova down-cell visibility limitations](https://docs.openstack.org/api-guide/compute/down_cells.html)
- [OpenStack CLI server listing](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#server-list)

Local contract tests use synthetic `example.invalid` endpoints. They establish
request/normalization behavior, not cloud-release certification.
