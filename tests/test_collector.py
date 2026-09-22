"""Exercise real SDK HTTP requests against synthetic Nova responses."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import openstack
import pytest
import requests
from keystoneauth1 import exceptions as auth_exceptions
from keystoneauth1.noauth import NoAuth
from keystoneauth1.session import Session
from openstack.connection import Connection

from openstack_maintenance_check import collector
from openstack_maintenance_check.checks import evaluate
from openstack_maintenance_check.collector import CollectionError, collect, collect_cloud
from openstack_maintenance_check.reporting import render_json
from openstack_maintenance_check.snapshot import snapshot_dict

BASE = "https://nova.example.invalid/v2.1"
HOST = "compute-demo-01"
SERVICE = {"host": HOST, "binary": "nova-compute", "state": "up", "status": "disabled"}
SERVER = {
    "id": "server-demo-01",
    "name": "demo-web",
    "OS-EXT-SRV-ATTR:host": HOST,
    "status": "ACTIVE",
    "OS-EXT-STS:task_state": None,
}
NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)


@pytest.fixture
def nova(requests_mock):
    discovery = {
        "version": {
            "id": "v2.1",
            "status": "CURRENT",
            "min_version": "2.1",
            "version": "2.100",
            "links": [{"rel": "self", "href": BASE + "/"}],
        }
    }
    requests_mock.get(BASE, json=discovery)
    requests_mock.get(BASE + "/", json=discovery)
    requests_mock.get(BASE + "/os-services", json={"services": [SERVICE]})
    requests_mock.get(BASE + "/servers/detail", json={"servers": [SERVER]})
    connection = Connection(
        session=Session(auth=NoAuth()), compute_endpoint_override=BASE, compute_api_version="2.1"
    )
    yield connection.compute
    connection.close()


def clock():
    return NOW


def test_real_adapter_contract(nova, requests_mock):
    snapshot = collect(nova, HOST, clock=clock)
    assert snapshot.servers_complete
    assert snapshot.host.state == "up"
    assert snapshot.servers[0].task_state_known
    assert snapshot.servers[0].task_state is None
    assert snapshot.collection.compute_api_version == "2.1"
    assert evaluate(snapshot, live=True, now=NOW).exit_code == 0
    requests_seen = requests_mock.request_history
    assert all(request.method == "GET" for request in requests_seen)
    server_request = requests_seen[-1]
    assert server_request.qs == {"host": [HOST], "all_tenants": ["1"], "limit": ["200"]}
    assert server_request.headers["OpenStack-API-Version"] == "compute 2.1"
    assert requests_seen[-2].qs == {"host": [HOST], "binary": ["nova-compute"]}


def test_missing_task_does_not_become_idle(nova, requests_mock):
    server = dict(SERVER)
    del server["OS-EXT-STS:task_state"]
    requests_mock.get(BASE + "/servers/detail", json={"servers": [server]})
    snapshot = collect(nova, HOST, clock=clock)
    assert snapshot.servers_complete  # Complete inventory, incomplete field evidence.
    assert not snapshot.servers[0].task_state_known
    assert evaluate(snapshot).outcome == "incomplete"
    assert "task_state" not in snapshot_dict(snapshot)["servers"][0]


def test_pagination_keeps_scope_and_ignores_remote_next_url(nova, requests_mock):
    requests_mock.get(
        BASE + "/servers/detail",
        [
            {
                "json": {
                    "servers": [SERVER],
                    "servers_links": [
                        {"rel": "next", "href": "https://untrusted.example.invalid/leak"}
                    ],
                }
            },
            {"json": {"servers": [{**SERVER, "id": "server-demo-02"}]}},
        ],
    )
    snapshot = collect(nova, HOST, clock=clock)
    assert snapshot.servers_complete and len(snapshot.servers) == 2
    request = requests_mock.request_history[-1]
    assert request.qs == {
        "host": [HOST],
        "all_tenants": ["1"],
        "limit": ["200"],
        "marker": [SERVER["id"]],
    }
    assert all(
        request.hostname == "nova.example.invalid" for request in requests_mock.request_history
    )


def test_full_page_without_links_uses_marker(nova, requests_mock, monkeypatch):
    monkeypatch.setattr(collector, "PAGE_SIZE", 1)
    requests_mock.get(
        BASE + "/servers/detail",
        [
            {"json": {"servers": [SERVER]}},
            {"json": {"servers": []}},
        ],
    )
    snapshot = collect(nova, HOST, clock=clock)
    assert snapshot.servers_complete
    assert requests_mock.request_history[-1].qs["marker"] == [SERVER["id"]]


@pytest.mark.parametrize(
    ("code", "issue"),
    [
        (401, "authentication_failed"),
        (403, "forbidden"),
        (500, "request_failed"),
        (406, "request_failed"),
    ],
)
def test_failed_later_page_retains_observed_rows(nova, requests_mock, code, issue):
    requests_mock.get(
        BASE + "/servers/detail",
        [
            {"json": {"servers": [SERVER], "servers_links": [{"rel": "next"}]}},
            {"status_code": code, "text": "sensitive-response-sentinel"},
        ],
    )
    snapshot = collect(nova, HOST, clock=clock)
    assert not snapshot.servers_complete
    assert len(snapshot.servers) == 1
    assert snapshot.collection.issues == (issue,)
    report = evaluate(snapshot)
    assert report.exit_code == 1 and report.outcome == "incomplete"
    assert "sensitive-response-sentinel" not in render_json(report)
    assert all(
        request.qs.get("all_tenants") == ["1"]
        for request in requests_mock.request_history
        if request.path.endswith("/servers/detail")
    )


def test_initial_server_denial_is_incomplete_not_tenant_fallback(nova, requests_mock):
    requests_mock.get(BASE + "/servers/detail", status_code=403)
    snapshot = collect(nova, HOST, clock=clock)
    assert not snapshot.servers and not snapshot.servers_complete
    assert snapshot.collection.issues == ("forbidden",)
    assert (
        sum(request.path.endswith("/servers/detail") for request in requests_mock.request_history)
        == 1
    )


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout("sensitive"),
        requests.ConnectionError("sensitive"),
        auth_exceptions.ConnectFailure("sensitive"),
    ],
)
def test_transport_failure_is_reported_without_raw_errors(nova, requests_mock, error):
    requests_mock.get(BASE + "/servers/detail", exc=error)
    snapshot = collect(nova, HOST, clock=clock)
    assert snapshot.collection.issues == ("connection_failed",)
    assert "sensitive" not in render_json(evaluate(snapshot))


def test_http_auth_exception_is_classified(nova, requests_mock):
    requests_mock.get(BASE + "/servers/detail", exc=auth_exceptions.Unauthorized("sensitive"))
    assert collect(nova, HOST).collection.issues == ("authentication_failed",)


@pytest.mark.parametrize(
    ("services", "code"),
    [
        ([], "host_not_found"),
        ([SERVICE, SERVICE], "ambiguous_host"),
        ([{**SERVICE, "host": "other-demo"}], "host_not_found"),
        ([{**SERVICE, "binary": "nova-scheduler"}], "host_not_found"),
        (None, "invalid_response"),
        ([None], "invalid_response"),
    ],
)
def test_exact_host_resolution(nova, requests_mock, services, code):
    requests_mock.get(BASE + "/os-services", json={"services": services})
    with pytest.raises(CollectionError) as exc:
        collect(nova, HOST)
    assert exc.value.code == code
    assert not any(
        request.path.endswith("/servers/detail") for request in requests_mock.request_history
    )


def test_service_denial_fails_before_inventory(nova, requests_mock):
    requests_mock.get(BASE + "/os-services", status_code=403, text="sensitive")
    with pytest.raises(CollectionError, match="denied") as exc:
        collect(nova, HOST)
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {**SERVER, "id": None},
        {**SERVER, "OS-EXT-SRV-ATTR:host": None},
        {**SERVER, "OS-EXT-SRV-ATTR:host": "wrong-demo"},
    ],
)
def test_unassignable_rows_never_silently_pass(nova, requests_mock, row):
    requests_mock.get(BASE + "/servers/detail", json={"servers": [row]})
    snapshot = collect(nova, HOST)
    assert not snapshot.servers_complete
    assert not snapshot.servers
    assert evaluate(snapshot).outcome == "incomplete"


@pytest.mark.parametrize("task", [False, 0, {}, ""])
def test_malformed_task_remains_unknown(nova, requests_mock, task):
    requests_mock.get(
        BASE + "/servers/detail", json={"servers": [{**SERVER, "OS-EXT-STS:task_state": task}]}
    )
    snapshot = collect(nova, HOST)
    assert not snapshot.servers[0].task_state_known
    assert snapshot.collection.issues == ("invalid_task_state",)


@pytest.mark.parametrize("body", [[], {}, {"servers": None}])
def test_bad_response_keeps_incomplete_state(nova, requests_mock, body):
    requests_mock.get(BASE + "/servers/detail", json=body)
    assert not collect(nova, HOST).servers_complete


def test_non_json_response(nova, requests_mock):
    requests_mock.get(BASE + "/servers/detail", text="sensitive html")
    assert collect(nova, HOST).collection.issues == ("invalid_response",)


@pytest.mark.parametrize("links", [None, {}, [None], [{}]])
def test_malformed_pagination(nova, requests_mock, links):
    requests_mock.get(BASE + "/servers/detail", json={"servers": [SERVER], "servers_links": links})
    assert collect(nova, HOST).collection.issues == ("pagination_incomplete",)


def test_repeated_page_cannot_loop_or_succeed(nova, requests_mock):
    requests_mock.get(
        BASE + "/servers/detail", json={"servers": [SERVER], "servers_links": [{"rel": "next"}]}
    )
    snapshot = collect(nova, HOST)
    assert len(snapshot.servers) == 1
    assert snapshot.collection.issues == ("duplicate_server", "pagination_incomplete")


def test_next_page_without_marker(nova, requests_mock):
    requests_mock.get(
        BASE + "/servers/detail", json={"servers": [], "servers_links": [{"rel": "next"}]}
    )
    assert collect(nova, HOST).collection.issues == ("pagination_incomplete",)


def test_page_budget_is_explicitly_incomplete(nova, requests_mock, monkeypatch):
    monkeypatch.setattr(collector, "MAX_PAGES", 1)
    requests_mock.get(
        BASE + "/servers/detail", json={"servers": [SERVER], "servers_links": [{"rel": "next"}]}
    )
    assert collect(nova, HOST).collection.issues == ("pagination_incomplete",)


def test_redirect_not_followed(nova, requests_mock):
    requests_mock.get(
        BASE + "/servers/detail",
        status_code=302,
        headers={"Location": "https://untrusted.example.invalid/"},
    )
    assert collect(nova, HOST).collection.issues == ("request_failed",)
    assert all(
        request.hostname == "nova.example.invalid" for request in requests_mock.request_history
    )


def test_collection_times_span_both_requests(nova):
    times = iter([NOW, NOW + timedelta(seconds=3)])
    snapshot = collect(nova, HOST, clock=lambda: next(times))
    assert snapshot.captured_at == NOW == snapshot.collection.started_at
    assert snapshot.collection.completed_at == NOW + timedelta(seconds=3)


def test_clock_going_backwards_fails(nova):
    times = iter([NOW, NOW - timedelta(seconds=1)])
    with pytest.raises(CollectionError):
        collect(nova, HOST, clock=lambda: next(times))


def test_connection_uses_named_profile_and_closes(nova, monkeypatch):
    connection = Mock(compute=nova)
    connect = Mock(return_value=connection)
    monkeypatch.setattr(openstack, "connect", connect)
    snapshot = collect_cloud("demo-profile", HOST)
    assert snapshot.servers_complete
    assert connect.call_args.kwargs["cloud"] == "demo-profile"
    assert connect.call_args.kwargs["load_envvars"] is False
    assert connect.call_args.kwargs["api_timeout"] == 30
    assert connect.call_args.kwargs["compute_api_version"] == "2.1"
    connection.close.assert_called_once()


def test_configuration_error_never_exposes_sdk_message(monkeypatch):
    monkeypatch.setattr(openstack, "connect", Mock(side_effect=RuntimeError("sensitive-value")))
    with pytest.raises(CollectionError, match="clouds.yaml") as exc:
        collect_cloud("demo-profile", HOST)
    assert "sensitive-value" not in str(exc.value)


def test_missing_sdk_help_is_actionable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "openstack", None)
    with pytest.raises(CollectionError, match=r"\[live\]"):
        collect_cloud("demo-profile", HOST)


def test_cleanup_error_does_not_mask_report(nova, monkeypatch):
    connection = Mock(compute=nova)
    connection.close.side_effect = RuntimeError("sensitive-value")
    monkeypatch.setattr(openstack, "connect", Mock(return_value=connection))
    assert collect_cloud("demo-profile", HOST).servers_complete


def test_export_allowlist_excludes_raw_sensitive_fields(nova, requests_mock):
    requests_mock.get(
        BASE + "/servers/detail",
        json={
            "servers": [
                {
                    **SERVER,
                    "metadata": {"internal": "sensitive-value"},
                    "user_data": "sensitive-value",
                    "addresses": {"private": "sensitive-value"},
                    "adminPass": "sensitive-value",
                }
            ]
        },
    )
    report = render_json(evaluate(collect(nova, HOST)))
    assert "sensitive-value" not in report
    assert "adminPass" not in report


def test_fresh_request_bypasses_sdk_cache():
    response = Mock(status_code=200)
    response.json.side_effect = [{"services": [SERVICE]}, {"servers": []}]
    compute = Mock()
    compute.get.return_value = response
    assert collect(compute, HOST).servers_complete
    assert all(call.kwargs["skip_cache"] for call in compute.get.call_args_list)
