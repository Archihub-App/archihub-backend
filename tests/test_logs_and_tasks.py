"""Audit log and task listing."""

from __future__ import annotations

import pytest

from archihub.api.logs import services as logs
from archihub.api.tasks import services as tasks


class FakeMongo:
    def __init__(self):
        self.rows: dict[str, list] = {}
        self.inserted: list = []
        self.updated: list = []
        self.fail = False

    def get_record(self, collection, filters, fields=None):
        rows = self.rows.get(collection, [])
        return rows[0] if rows else None

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return list(self.rows.get(collection, []))

    def count(self, collection, filters=None):
        return len(self.rows.get(collection, []))

    def insert_record(self, collection, record):
        if self.fail:
            raise ConnectionError("write failed")
        self.inserted.append((collection, record))

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(logs, "_mongo", lambda: fake)
    monkeypatch.setattr(tasks, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_an_action_key_is_normalised_to_its_stored_value(mongo):
    """Both spellings must store the same value.

    The audit filter vocabulary comes from the same mapping, so an entry stored
    under the lowercase key would be invisible to every filter.
    """
    logs.register_log("alice", "type_create", {})
    logs.register_log("bob", "TYPE_CREATE", {})

    assert {record["action"] for _c, record in mongo.inserted} == {"TYPE_CREATE"}


def test_an_audit_failure_never_fails_the_operation(mongo):
    """The work already happened.

    Turning a successful edit into an error because its log entry could not be
    written would be worse than the missing entry.
    """
    mongo.fail = True
    logs.register_log("alice", "type_create", {})  # must not raise


def test_a_missing_username_is_recorded_as_system(mongo):
    logs.register_log(None, "type_create", {})
    assert mongo.inserted[0][1]["username"] == "system"


def test_article_bodies_are_stripped_from_audit_details():
    """They can be megabytes of HTML, duplicated on every save."""
    details = logs.build_details(
        "RESOURCE_ARTICLE_UPDATE",
        {
            "articleBody": "x" * 10_000,
            "resource": {"articleBody": "y" * 10_000, "metadata": {"articleBody": "z" * 10_000}},
        },
    )

    assert "articleBody" not in details
    assert "articleBody" not in details["resource"]
    assert "articleBody" not in details["resource"]["metadata"]


def test_no_matches_is_an_empty_list_not_a_404(mongo):
    """The legacy 404 could never fire - it tested a pymongo cursor for
    emptiness, and a cursor is always truthy. "Nothing matched this filter" is a
    successful query with no results anyway."""
    mongo.rows["logs"] = []
    payload, status = logs.filter_logs({"page": 0, "filters": {}})

    assert status == 200
    assert payload == []


@pytest.mark.parametrize(
    "filters",
    [{"$where": "1==1"}, {"metadata": {"$ne": None}}, {"username": {"$regex": ".*"}}, "nope"],
)
def test_audit_filters_are_reduced_to_an_allowlist(filters):
    cleaned = logs.sanitize_log_filters(filters)
    assert set(cleaned) <= logs.ALLOWED_LOG_FILTER_FIELDS
    assert all(isinstance(v, str) for v in cleaned.values())


def test_metadata_is_replaced_by_presentable_details(mongo):
    mongo.rows["logs"] = [{"action": "SEARCH", "metadata": {"filters": {"keyword": "x"}, "page": 2}}]
    payload, _status = logs.filter_logs({"page": 0, "filters": {}})

    assert "metadata" not in payload[0]
    assert payload[0]["details"]["filters"]["keyword"] == "x"


# ---------------------------------------------------------------------------
# Task access control
# ---------------------------------------------------------------------------


def test_a_user_may_read_their_own_tasks():
    assert tasks.may_read_tasks_of("alice", "alice", is_admin=False) is True


def test_a_user_may_not_read_someone_elses_tasks():
    """The headline fix.

    The legacy guard only refused when the requested user was literally
    'automatic', so any authenticated caller could read another person's task
    list by passing their username.
    """
    assert tasks.may_read_tasks_of("alice", "bob", is_admin=False) is False


def test_a_user_may_not_read_the_system_task_list():
    assert tasks.may_read_tasks_of("alice", "automatic", is_admin=False) is False


@pytest.mark.parametrize("target", ["alice", "bob", "automatic", "system"])
def test_an_admin_may_read_anyones_tasks(target):
    assert tasks.may_read_tasks_of("admin", target, is_admin=True) is True


# ---------------------------------------------------------------------------
# Task listing
# ---------------------------------------------------------------------------


def test_async_result_is_bound_to_our_app():
    """A bare AsyncResult resolves against Celery's process-global default app.

    An unclaimed default carries a DisabledBackend whose every state query
    raises, which made task polling silently depend on which module was imported
    first. Verified by checking the bound app rather than the import.
    """
    from archihub.worker.celery_app import celery_app

    assert tasks._result("some-id").app is celery_app


def test_the_system_identity_is_presented_as_such(mongo, monkeypatch):
    mongo.rows["tasks"] = [
        {"_id": "1", "taskId": "t", "user": "automatic", "status": "completed", "date": None}
    ]
    payload, status = tasks.get_tasks("automatic", {"page": 0})

    assert status == 200
    assert payload[0]["user"] == "system"


def test_finished_tasks_are_reconciled_on_read(mongo, monkeypatch):
    """A stale 'pending' row would otherwise stay pending forever."""
    mongo.rows["tasks"] = [
        {"_id": "1", "taskId": "t", "user": "alice", "status": "pending", "date": None}
    ]
    monkeypatch.setattr(
        tasks, "get_task_status", lambda task_id: {"status": "completed", "result": "done"}
    )
    monkeypatch.setattr(tasks, "_sync_finished_task", lambda task_id, result: None)
    monkeypatch.setattr(tasks, "_result", lambda task_id: None)

    payload, _status = tasks.get_tasks("alice", {"page": 0})
    assert payload[0]["status"] == "completed"
