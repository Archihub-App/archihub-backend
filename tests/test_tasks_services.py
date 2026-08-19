"""Task records, and the ``has_task`` rewrite.

``has_task`` is the guard plugins use to avoid launching duplicate concurrent
jobs (``mqttHandler``, ``mailLabeler``, and any plugin via
``PluginClass.has_task``). The legacy implementation cannot work:

* it rebinds ``task`` to a Pydantic model and then subscripts it
  (``task['taskId']``) -> TypeError;
* three branches assign to an undefined name ``t`` -> NameError;
* a bare ``except Exception`` returns True, i.e. "a task is already running";
* when the task genuinely IS running, no branch matches and it returns None.

Net effect, inverted in both directions that matter: a FINISHED task blocks the
user, and a RUNNING task fails to block a duplicate. These tests pin the
corrected behaviour.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from bson.objectid import ObjectId

from archihub.api.tasks import services

TASK_OID = "6a70b833497d4440325c94b1"


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.inserted: list = []
        self.updates: list = []

    def get_record(self, collection, filters, fields=None):
        return self.records.get(collection)

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))

    def update_record(self, collection, filters, update):
        self.updates.append((collection, filters, update))


class FakeResult:
    """Stands in for celery.result.AsyncResult."""

    def __init__(self, state="PENDING", result=None, ready=False, successful=False):
        self.state = state
        self.result = result
        self._ready = ready
        self._successful = successful

    def ready(self):
        return self._ready

    def successful(self):
        return self._successful

    def failed(self):
        return self._ready and not self._successful

    @property
    def info(self):
        return self.result


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


def patch_result(monkeypatch, result: FakeResult):
    # Patch the `_result` seam rather than AsyncResult itself: the service binds
    # the Celery app explicitly, so AsyncResult is called with two arguments.
    monkeypatch.setattr(services, "_result", lambda task_id: result)


# ---------------------------------------------------------------------------
# has_task
# ---------------------------------------------------------------------------


def test_no_record_means_no_task(mongo):
    mongo.records["tasks"] = None
    assert services.has_task("alice", "plugin.job") is False


def test_running_task_blocks_a_duplicate(mongo, monkeypatch):
    """THE bug: legacy returned None here, so duplicates were allowed."""
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="STARTED", ready=False))

    assert services.has_task("alice", "plugin.job") is True


@pytest.mark.parametrize("state", ["PENDING", "STARTED", "RETRY", "PROGRESS", "RECEIVED"])
def test_all_in_flight_states_block(mongo, monkeypatch, state):
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state=state, ready=False))
    assert services.has_task("alice", "plugin.job") is True


def test_completed_task_does_not_block(mongo, monkeypatch):
    """THE other bug: legacy crashed here and the handler returned True,
    locking the user out of the feature until the record was edited by hand."""
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="SUCCESS", result="done", ready=True, successful=True))

    assert services.has_task("alice", "plugin.job") is False


def test_completed_task_is_reconciled_into_the_database(mongo, monkeypatch):
    """Otherwise the stale 'pending' row is re-examined on every call forever."""
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="SUCCESS", result="done", ready=True, successful=True))

    services.has_task("alice", "plugin.job")

    _, filters, update = mongo.updates[0]
    assert filters == {"taskId": "abc"}
    assert update["status"] == services.STATUS_COMPLETED
    assert update["result"] == "done"


def test_failed_task_does_not_block_and_is_recorded(mongo, monkeypatch):
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="FAILURE", result=RuntimeError("x"), ready=True))

    assert services.has_task("alice", "plugin.job") is False
    assert mongo.updates[0][2]["status"] == services.STATUS_FAILED


def test_failure_details_are_not_persisted(mongo, monkeypatch):
    """An exception's string form can carry connection strings and file paths.

    The tasks list is rendered in the UI, so persisting it would surface server
    internals to any user who can see their own task history.
    """
    secret = RuntimeError("mongodb://admin:hunter2@10.0.0.5:27017 refused")
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="FAILURE", result=secret, ready=True))

    services.has_task("alice", "plugin.job")

    assert mongo.updates[0][2]["result"] == ""
    assert "hunter2" not in str(mongo.updates)


def test_errors_allow_work_rather_than_blocking_it(mongo, monkeypatch):
    """Legacy's handler returned True, so a transient broker hiccup silently
    locked the user out with no indication why."""
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}

    def _explode(task_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(services, "_result", _explode)
    assert services.has_task("alice", "plugin.job") is False


def test_record_without_task_id_does_not_block(mongo):
    mongo.records["tasks"] = {"user": "alice"}
    assert services.has_task("alice", "plugin.job") is False


def test_unrecognised_state_does_not_block(mongo, monkeypatch):
    """Never leave a user permanently stuck on an unknown Celery state."""
    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="WEIRD", ready=False))
    assert services.has_task("alice", "plugin.job") is False


# ---------------------------------------------------------------------------
# add_task
# ---------------------------------------------------------------------------


def test_add_task_records_the_task(mongo):
    mongo.records["users"] = {"username": "alice"}
    services.add_task("abc", "plugin.job", "alice", "msg")

    collection, record = mongo.inserted[0]
    assert collection == "tasks"
    assert record["taskId"] == "abc"
    assert record["user"] == "alice"
    assert record["status"] == services.STATUS_PENDING


@pytest.mark.parametrize("system_user", ["automatic", "system"])
def test_system_users_may_record_tasks(mongo, system_user):
    """Hooks and the scheduler own tasks under reserved identities."""
    mongo.records["users"] = None
    services.add_task("abc", "hook.task", system_user, "hook")
    assert mongo.inserted[0][1]["user"] == "automatic"


def test_unknown_user_is_rejected_loudly(mongo):
    """Legacy returned a (dict, 404) tuple from this void function, which no
    caller inspected - so the task went unrecorded and simply never appeared."""
    mongo.records["users"] = None
    with pytest.raises(ValueError):
        services.add_task("abc", "plugin.job", "ghost", "msg")
    assert mongo.inserted == []


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------


def test_status_reports_progress_metadata(monkeypatch):
    patch_result(monkeypatch, FakeResult(state="PROGRESS", result={"percent": 42}, ready=False))
    assert services.get_task_status("abc") == {
        "status": services.STATUS_PENDING,
        "result": {"percent": 42},
    }


def test_status_of_failed_task_withholds_the_message(monkeypatch):
    patch_result(monkeypatch, FakeResult(state="FAILURE", result=RuntimeError("internals"), ready=True))
    assert services.get_task_status("abc") == {"status": services.STATUS_FAILED, "result": ""}


def test_non_serialisable_result_is_stringified(mongo, monkeypatch):
    """Mongo cannot store arbitrary objects; inserting one would raise."""

    class Weird:
        def __str__(self):
            return "weird-object"

    mongo.records["tasks"] = {"taskId": "abc", "user": "alice"}
    patch_result(monkeypatch, FakeResult(state="SUCCESS", result=Weird(), ready=True, successful=True))

    services.has_task("alice", "plugin.job")
    assert mongo.updates[0][2]["result"] == "weird-object"


# ---------------------------------------------------------------------------
# get_tasks - the wire shape the panel reads
# ---------------------------------------------------------------------------


def listing_mongo(monkeypatch, rows):
    """A FakeMongo whose `get_all_records` returns `rows`."""
    fake = FakeMongo()
    fake.get_all_records = lambda collection, filters=None, **kwargs: list(rows)
    fake.count = lambda collection, filters=None: len(rows)
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


def test_a_task_date_is_extended_json_not_a_bare_string(monkeypatch):
    """`TasksResults.tsx` renders `getSimpleDate(resource.date.$date)`.

    The read is unguarded, so a bare string leaves `.$date` undefined and every
    row in the panel reads "Invalid Date" - a 200, a full body, and a wrong
    screen with nothing logged anywhere. Legacy reached this shape by running
    the list through `parse_result`.
    """
    listing_mongo(
        monkeypatch,
        [{"_id": ObjectId(TASK_OID), "date": datetime(2026, 8, 19, 10, 30), "user": "alice"}],
    )

    rows, status = services.get_tasks("alice", {"page": 0})

    assert status == 200
    assert isinstance(rows[0]["date"], dict)
    assert "$date" in rows[0]["date"]


def test_a_task_id_is_extended_json_too(monkeypatch):
    """The same `json_util` pass legacy applied, so `_id` keeps its `$oid` form."""
    listing_mongo(
        monkeypatch,
        [{"_id": ObjectId(TASK_OID), "date": datetime(2026, 8, 19, 10, 30), "user": "alice"}],
    )

    rows, _status = services.get_tasks("alice", {"page": 0})

    assert rows[0]["_id"] == {"$oid": TASK_OID}


def test_the_listing_still_presents_automatic_as_the_system(monkeypatch):
    """Serialising must not skip the presentation the panel depends on."""
    listing_mongo(
        monkeypatch,
        [{"_id": ObjectId(TASK_OID), "date": datetime(2026, 8, 19, 10, 30), "user": "automatic"}],
    )

    rows, _status = services.get_tasks("alice", {"page": 0})

    assert rows[0]["user"] == "system"
