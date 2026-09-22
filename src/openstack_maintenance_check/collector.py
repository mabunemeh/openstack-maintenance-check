"""Read-only Nova inventory through the SDK's authenticated Compute adapter.

Use original JSON keys: Resource.to_dict() cannot distinguish an absent task
state from an observed null. Endpoint discovery and authentication stay with
openstacksdk. Pagination requests always preserve the administrative host scope.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from . import __version__
from .models import COLLECTION_ISSUES, Collection, Host, Server, Snapshot

COMPUTE_MICROVERSION = "2.1"
PAGE_SIZE = 200
MAX_PAGES = 1000


class ComputeAdapter(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...


class CollectionError(RuntimeError):
    """Safe code-only error. Never expose original SDK/configuration messages."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(COLLECTION_ISSUES[code])


def _error_code(exc: Exception) -> str:
    status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
    return {401: "authentication_failed", 403: "forbidden"}.get(status, "connection_failed")


@contextmanager
def connect_compute(cloud: str) -> Iterator[ComputeAdapter]:
    """Load only a named profile. No SDK/config imports occur during offline use."""
    try:
        import openstack
    except ImportError:
        raise CollectionError("sdk_unavailable") from None
    connection = None
    try:
        try:
            connection = openstack.connect(
                cloud=cloud,
                load_envvars=False,
                api_timeout=30,
                compute_api_version=COMPUTE_MICROVERSION,
                app_name="openstack-maintenance-check",
                app_version=__version__,
            )
            compute = connection.compute
        except Exception:
            # SDK errors can include secrets and authentication response bodies.
            # This catch is deliberately confined to the external setup boundary.
            raise CollectionError("configuration_failed") from None
        yield compute
    finally:
        if connection is not None:
            # Cleanup must not dump SDK messages or mask the collection result.
            with suppress(Exception):
                connection.close()


def _get(compute: ComputeAdapter, path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = compute.get(
            path,
            params=params,
            microversion=COMPUTE_MICROVERSION,
            raise_exc=False,
            redirect=False,
            log=False,
            connect_retries=0,
            status_code_retries=0,
            skip_cache=True,
        )
    except Exception as exc:
        raise CollectionError(_error_code(exc)) from None
    if response.status_code != 200:
        code = {401: "authentication_failed", 403: "forbidden"}.get(
            response.status_code, "request_failed"
        )
        raise CollectionError(code)
    try:
        data = response.json()
    except (ValueError, RecursionError):
        raise CollectionError("invalid_response") from None
    if not isinstance(data, dict):
        raise CollectionError("invalid_response")
    return data


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _resolve_host(compute: ComputeAdapter, host: str) -> Host:
    data = _get(compute, "/os-services", {"host": host, "binary": "nova-compute"})
    services = data.get("services")
    if not isinstance(services, list) or any(not isinstance(item, dict) for item in services):
        raise CollectionError("invalid_response")
    matches = [
        item
        for item in services
        if item.get("host") == host and item.get("binary") == "nova-compute"
    ]
    if not matches:
        raise CollectionError("host_not_found")
    if len(matches) != 1:
        raise CollectionError("ambiguous_host")
    service = matches[0]
    return Host(host, _text(service.get("state")), _text(service.get("status")))


def _server(item: Any, host: str, issues: set[str]) -> Server | None:
    if not isinstance(item, dict):
        issues.add("invalid_response")
        return None
    server_id = _text(item.get("id"))
    placement = _text(item.get("OS-EXT-SRV-ATTR:host"))
    if not server_id or not placement:
        issues.add("missing_identity")
        return None
    if placement != host:
        issues.add("host_mismatch")
        return None
    task_known = "OS-EXT-STS:task_state" in item
    task = item.get("OS-EXT-STS:task_state")
    if task_known and task is not None and _text(task) is None:
        task_known = False
        task = None
        issues.add("invalid_task_state")
    return Server(
        server_id,
        _text(item.get("name")),
        placement,
        _text(item.get("status")),
        task,
        task_known,
    )


def collect(
    compute: ComputeAdapter,
    host: str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Snapshot:
    """Preserve observed rows on server-list failures; never retry tenant-only.

    Service resolution must succeed first. Failure there is a fatal collection
    error; failure afterwards returns an explicitly incomplete snapshot.
    """
    clock = clock or (lambda: datetime.now(UTC))
    started = clock()
    source = _resolve_host(compute, host)
    servers: dict[str, Server] = {}
    seen: set[str] = set()
    issues: set[str] = set()
    params: dict[str, Any] = {"host": host, "all_tenants": 1, "limit": PAGE_SIZE}
    for _ in range(MAX_PAGES):
        try:
            data = _get(compute, "/servers/detail", params)
        except CollectionError as exc:
            issues.add(exc.code)
            break
        page = data.get("servers")
        if not isinstance(page, list):
            issues.add("invalid_response")
            break
        repeated = False
        for item in page:
            server_id = _text(item.get("id")) if isinstance(item, dict) else None
            if server_id in seen:
                issues.add("duplicate_server")
                repeated = True
                continue
            if server_id:
                seen.add(server_id)
            server = _server(item, host, issues)
            if server:
                servers[server.id] = server
        links = data.get("servers_links", [])
        if not isinstance(links, list) or any(
            not isinstance(link, dict) or not isinstance(link.get("rel"), str) for link in links
        ):
            issues.add("pagination_incomplete")
            break
        more = any(link["rel"] == "next" for link in links) or len(page) >= PAGE_SIZE
        if not more:
            break
        # Never follow server-supplied URLs. Nova uses the final row ID as marker;
        # retain the same path, microversion, all-project scope, host, and limit.
        marker = _text(page[-1].get("id")) if page and isinstance(page[-1], dict) else None
        if not marker or repeated or marker == params.get("marker"):
            issues.add("pagination_incomplete")
            break
        params = {**params, "marker": marker}
    else:
        issues.add("pagination_incomplete")
    completed = clock()
    if completed < started:
        raise CollectionError("request_failed")
    return Snapshot(
        started,
        source,
        not issues,
        tuple(servers[key] for key in sorted(servers)),
        Collection(started, completed, COMPUTE_MICROVERSION, tuple(sorted(issues))),
    )


def collect_cloud(cloud: str, host: str) -> Snapshot:
    with connect_compute(cloud) as compute:
        return collect(compute, host)
