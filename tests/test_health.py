"""Health endpoints and the live-route inventory.

The readiness payload shape is a contract: ``ArchiHUBTestRunner`` preflight reads
it, and the container healthcheck depends on the status codes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from archihub.api.health import services


@pytest.fixture
def client() -> TestClient:
    from main import app

    return TestClient(app, raise_server_exceptions=False)


def test_live_is_unauthenticated_and_cheap(client: TestClient):
    """/health/live must not touch any dependency - it answers even when
    MongoDB, Redis and everything else is down."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"alive": True}


def test_ready_reports_503_when_a_dependency_is_down(client: TestClient, monkeypatch):
    monkeypatch.setattr(services, "check_mongo", lambda: (False, "connection refused"))
    monkeypatch.setattr(services, "check_redis", lambda: (True, None))
    monkeypatch.setattr(services, "check_elasticsearch", lambda *a: (services.DISABLED, None))
    monkeypatch.setattr(services, "check_qdrant", lambda *a: (services.DISABLED, None))
    monkeypatch.setattr(services, "check_celery", lambda: (True, None))

    response = client.get("/health/ready")
    assert response.status_code == 503

    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["mongo"] == {"status": "error", "error": "connection refused"}
    assert body["checks"]["redis"] == {"status": "ok"}


def test_disabled_features_do_not_block_readiness(client: TestClient, monkeypatch):
    """An instance that never enabled indexing is still ready.

    Elasticsearch and Qdrant report 'disabled' rather than failing, so their
    absence must not produce a 503.
    """
    monkeypatch.setattr(services, "check_mongo", lambda: (True, None))
    monkeypatch.setattr(services, "load_index_management", lambda: {"data": []})
    monkeypatch.setattr(services, "check_redis", lambda: (True, None))
    monkeypatch.setattr(services, "check_elasticsearch", lambda *a: (services.DISABLED, None))
    monkeypatch.setattr(services, "check_qdrant", lambda *a: (services.DISABLED, None))
    monkeypatch.setattr(services, "check_celery", lambda: (True, None))

    response = client.get("/health/ready")
    assert response.status_code == 200

    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["elasticsearch"]["status"] == "disabled"
    assert body["checks"]["qdrant"]["status"] == "disabled"


def test_unreachable_mongo_is_not_probed_repeatedly(monkeypatch):
    """A dead MongoDB must cost exactly one connection timeout, not three.

    The Elasticsearch and Qdrant checks are gated on flags stored in Mongo. An
    earlier version read that document inside each check, so an unreachable
    database was dialled three times and /health/ready took over 40 seconds -
    long enough for a Docker healthcheck to time out and blame the wrong thing.
    """
    calls: list[str] = []

    def _record_and_fail():
        calls.append("mongo")
        return False, "connection refused"

    def _must_not_be_called():
        calls.append("settings")
        raise AssertionError("index_management must not be read when Mongo is down")

    monkeypatch.setattr(services, "check_mongo", _record_and_fail)
    monkeypatch.setattr(services, "load_index_management", _must_not_be_called)
    monkeypatch.setattr(services, "check_redis", lambda: (True, None))
    monkeypatch.setattr(services, "check_celery", lambda: (True, None))

    payload, status_code = services.get_readiness()

    assert status_code == 503
    assert calls == ["mongo"]
    assert payload["checks"]["elasticsearch"]["status"] == "disabled"
    assert payload["checks"]["qdrant"]["status"] == "disabled"


def test_readiness_payload_keys_match_legacy_contract(monkeypatch):
    monkeypatch.setattr(services, "check_mongo", lambda: (True, None))
    monkeypatch.setattr(services, "load_index_management", lambda: {"data": []})
    monkeypatch.setattr(services, "check_redis", lambda: (True, None))
    monkeypatch.setattr(services, "check_elasticsearch", lambda *a: (True, None))
    monkeypatch.setattr(services, "check_qdrant", lambda *a: (True, None))
    monkeypatch.setattr(services, "check_celery", lambda: (True, None))

    payload, status_code = services.get_readiness()
    assert status_code == 200
    assert set(payload) == {"ready", "checks"}
    assert set(payload["checks"]) == {"mongo", "redis", "elasticsearch", "qdrant", "celery"}


def test_route_inventory_matches_openapi_spec():
    """The inventory must agree with the generated spec, exactly.

    This is the property ArchiHUBTestRunner's swagger-inventory suite asserts.
    It is also a regression guard for the routing trap documented in
    archihub/core/routing.py: walking app.routes naively silently under-reports
    on modern FastAPI, which would make this comparison vacuous.
    """
    from main import app

    from archihub.api.health.testcontrol_services import get_routes

    payload, status_code = get_routes(app)
    assert status_code == 200

    live = {(route["path"], method) for route in payload["routes"] for method in route["methods"]}
    spec = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }

    assert live == spec
    # Guard against the wrapper bug returning a near-empty inventory that
    # happens to match an equally empty spec.
    assert len(live) >= 6


def test_starting_a_reset_queues_it_and_returns_something_to_poll(monkeypatch):
    from archihub.api.health import testcontrol_services
    from archihub.worker.tasks import testcontrol as task_module

    queued = {}

    class Queued:
        id = "task-1"

    def fake_delay(run_id):
        queued["run_id"] = run_id
        return Queued()

    monkeypatch.setattr(task_module.reset_task, "delay", fake_delay)
    monkeypatch.setattr(
        "archihub.api.tasks.services.add_task", lambda *args, **kwargs: None
    )

    payload, status_code = testcontrol_services.start_reset()

    assert status_code == 202
    assert payload["task_id"] == "task-1"
    # The run id is generated here and handed to the task, so the caller can
    # correlate the result it eventually reads with the reset it asked for.
    assert payload["run_id"] == queued["run_id"]


def test_a_reset_that_cannot_be_queued_says_so_rather_than_returning_an_id(monkeypatch):
    """A task id that will never resolve makes a runner wait for its timeout."""
    from archihub.api.health import testcontrol_services
    from archihub.worker.tasks import testcontrol as task_module

    def explode(run_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(task_module.reset_task, "delay", explode)

    payload, status_code = testcontrol_services.start_reset()

    assert status_code == 503
    assert "task_id" not in payload


def test_a_reset_still_starts_when_its_bookkeeping_row_cannot_be_written(monkeypatch):
    """The destructive part is already queued; failing the request now would
    tell the caller it had not started, and they would start it again."""
    from archihub.api.health import testcontrol_services
    from archihub.worker.tasks import testcontrol as task_module

    class Queued:
        id = "task-2"

    monkeypatch.setattr(task_module.reset_task, "delay", lambda run_id: Queued())

    def explode(*args, **kwargs):
        raise RuntimeError("mongo down")

    monkeypatch.setattr("archihub.api.tasks.services.add_task", explode)

    payload, status_code = testcontrol_services.start_reset()

    assert (status_code, payload["task_id"]) == (202, "task-2")
