import socket

import pytest


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Offline evaluation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


@pytest.fixture
def inventory():
    return {
        "schema_version": 1,
        "captured_at": "2026-09-05T12:00:00+04:00",
        "host": {"name": "compute-demo-01", "state": "up", "status": "disabled"},
        "servers_complete": True,
        "servers": [
            {
                "id": "server-demo-01",
                "name": "demo-web",
                "host": "compute-demo-01",
                "status": "ACTIVE",
                "task_state": None,
            }
        ],
    }
