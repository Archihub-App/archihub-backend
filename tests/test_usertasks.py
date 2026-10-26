"""Editorial review tasks.

The rule worth protecting here is that an editor cannot approve their own work -
that is the entire purpose of the review step.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.usertasks import services
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

VALID_ID = "6a70b8c3497d4440325c94c3"


class FakeInsert:
    inserted_id = ObjectId(VALID_ID)


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.rows: dict[str, list] = {}
        self.inserted: list = []
        self.updated: list = []

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return list(self.rows.get(collection, []))

    def count(self, collection, filters=None):
        return len(self.rows.get(collection, []))

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))
        return FakeInsert()

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))
        if collection in self.records and isinstance(self.records[collection], dict):
            self.records[collection].update(update)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Approval is a team-lead action
# ---------------------------------------------------------------------------


def test_an_editor_cannot_approve_a_task(mongo):
    """An editor signing off their own review would defeat the review step."""
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "pending", "comment": []}

    payload, status = services.update_task(
        VALID_ID, {"status": "approved"}, "editor@x.test", is_team_lead=False
    )

    assert status == ROLE_FAILURE_STATUS
    assert mongo.updated == []


def test_a_team_lead_may_approve(mongo):
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "pending", "comment": []}

    _payload, status = services.update_task(
        VALID_ID, {"status": "approved"}, "lead@x.test", is_team_lead=True
    )

    assert status == 200
    _c, _f, update = mongo.updated[0]
    assert update["status"] == "approved"
    assert update["approvedBy"] == "lead@x.test"


def test_an_editor_may_still_comment(mongo):
    """They are doing the work; they need to record progress on it."""
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "pending", "comment": []}

    _payload, status = services.update_task(
        VALID_ID, {"comment": "done"}, "editor@x.test", is_team_lead=False
    )

    assert status == 200
    _c, _f, update = mongo.updated[0]
    assert update["comment"][0]["comment"] == "done"


def test_an_approved_task_cannot_be_reopened(mongo):
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "approved", "comment": []}

    _payload, status = services.update_task(
        VALID_ID, {"comment": "more"}, "lead@x.test", is_team_lead=True
    )
    assert status == 400


def test_a_malformed_task_id_is_not_found(mongo):
    _payload, status = services.update_task("not-an-id", {}, "u", is_team_lead=True)
    assert status == 404


# ---------------------------------------------------------------------------
# One open task per target
# ---------------------------------------------------------------------------


def test_a_second_task_for_the_same_resource_is_refused(mongo):
    """Two editors asked to review the same thing would produce conflicting
    outcomes."""
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "pending"}

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "please review"}, "lead"
    )
    assert status == 400
    assert mongo.inserted == []


def test_a_task_is_created_when_the_target_is_free(mongo):
    mongo.records["usertasks"] = None

    payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "please review"}, "lead"
    )

    assert status == 201
    assert payload["status"] == "pending"
    assert payload["comment"][0]["user"] == "lead"


@pytest.mark.parametrize(
    "body",
    [
        {"user": "e", "comment": "c"},                       # no target
        {"resourceId": "r", "comment": "c"},                 # no assignee
        {"resourceId": "r", "user": "e"},                    # no comment
        {"resourceId": "  ", "user": "e", "comment": "c"},   # blank target
        {"resourceId": "r", "user": "  ", "comment": "c"},   # blank assignee
    ],
)
def test_incomplete_assignments_are_rejected(mongo, body):
    mongo.records["usertasks"] = None
    _payload, status = services.create_task(body, "lead")
    assert status == 400


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_an_absent_user_filter_matches_everyone(mongo):
    """Expressed as a presence check so the query shape stays constant."""
    mongo.rows["usertasks"] = []
    payload, status = services.get_all_tasks({"status": ["pending"], "user": None, "page": 0})

    assert status == 200
    assert payload == {"results": [], "total": 0}


def test_dates_are_serialised(mongo):
    from datetime import datetime

    mongo.rows["usertasks"] = [
        {"_id": ObjectId(VALID_ID), "status": "pending", "createdAt": datetime(2026, 1, 2, 3, 4, 5)}
    ]
    payload, _status = services.get_all_tasks({"status": ["pending"], "page": 0})

    assert payload["results"][0]["createdAt"] == "2026-01-02 03:04:05"


def test_missing_resource_domain_does_not_break_the_listing(mongo):
    """resourceType is an enrichment; the list must render without it."""
    mongo.rows["usertasks"] = [
        {"_id": ObjectId(VALID_ID), "status": "pending", "createdAt": None, "resourceId": "r1"}
    ]
    payload, status = services.get_all_tasks({"status": ["pending"], "page": 0})

    assert status == 200
    assert payload["results"][0]["resourceId"] == "r1"


def test_comments_flatten_to_readable_text():
    text = services.process_comments(
        [{"user": "a", "comment": "first"}, {"user": "b", "comment": "second"}]
    )
    assert "a: first" in text and "b: second" in text


# ---------------------------------------------------------------------------
# Status Workflow Lifecycle & Authorization
# ---------------------------------------------------------------------------


def test_an_editor_may_submit_task_for_review(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "pending",
        "user": "editor@x.test",
        "comment": [],
    }

    _payload, status = services.update_task(
        VALID_ID, {"status": "review"}, "editor@x.test", is_team_lead=False
    )

    assert status == 200
    _c, _f, update = mongo.updated[0]
    assert update["status"] == "review"


def test_a_team_lead_may_reject_a_task(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "review",
        "user": "editor@x.test",
        "comment": [],
    }

    _payload, status = services.update_task(
        VALID_ID, {"status": "rejected", "comment": "needs corrections"}, "lead@x.test", is_team_lead=True
    )

    assert status == 200
    _c, _f, update = mongo.updated[0]
    assert update["status"] == "rejected"
    assert update["comment"][0]["comment"] == "needs corrections"


def test_an_editor_cannot_reject_a_task(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "review",
        "user": "editor@x.test",
        "comment": [],
    }

    _payload, status = services.update_task(
        VALID_ID, {"status": "rejected"}, "editor@x.test", is_team_lead=False
    )

    assert status == ROLE_FAILURE_STATUS
    assert mongo.updated == []


def test_an_editor_cannot_update_another_users_task(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "pending",
        "user": "other_editor@x.test",
        "comment": [],
    }

    _payload, status = services.update_task(
        VALID_ID, {"comment": "unauthorized edit"}, "editor@x.test", is_team_lead=False
    )

    assert status == ROLE_FAILURE_STATUS
    assert mongo.updated == []


def test_open_task_can_be_retrieved_in_review_or_rejected_state(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res123",
        "status": "review",
        "comment": [{"user": "ed", "comment": "done"}],
    }
    payload, status = services.get_resource_tasks("res123")
    assert status == 200
    assert payload["status"] == "review"
    assert "ed: done" in payload["comment"]

    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "recordId": "rec123",
        "status": "rejected",
        "comment": [{"user": "lead", "comment": "redo"}],
    }
    payload, status = services.get_record_tasks("rec123")
    assert status == 200
    assert payload["status"] == "rejected"
    assert "lead: redo" in payload["comment"]


def test_create_task_records_audit_and_hook(mongo, monkeypatch):
    audits = []
    hooks = []
    monkeypatch.setattr(services, "_audit", lambda u, action, d: audits.append((u, action, d)))
    monkeypatch.setattr(services, "_call_hook", lambda name, p: hooks.append((name, p)))
    mongo.records["usertasks"] = None

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "check this"}, "lead"
    )

    assert status == 201
    assert len(audits) == 1
    assert audits[0][1] == "usertask_create"
    assert audits[0][2]["assignedUser"] == "e@x.test"
    assert audits[0][2]["resourceId"] == "r1"
    assert len(hooks) == 1
    assert hooks[0][0] == "usertask_create"


def test_update_task_records_audit_and_hook(mongo, monkeypatch):
    audits = []
    hooks = []
    monkeypatch.setattr(services, "_audit", lambda u, action, d: audits.append((u, action, d)))
    monkeypatch.setattr(services, "_call_hook", lambda name, p: hooks.append((name, p)))
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "pending",
        "user": "e@x.test",
        "resourceId": "r1",
        "comment": [],
    }

    _payload, status = services.update_task(
        VALID_ID, {"status": "review"}, "e@x.test", is_team_lead=False
    )

    assert status == 200
    assert len(audits) == 1
    assert audits[0][1] == "usertask_update"
    assert audits[0][2]["status"] == "review"
    assert len(hooks) == 1
    assert hooks[0][0] == "usertask_update"


def test_listing_tasks_as_editor_constrains_to_self(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from archihub.api.usertasks.router import router, require_task_reader
    from archihub.core.security.jwt import CurrentUser
    from archihub.api.users import services as user_services

    app = FastAPI()
    app.include_router(router)

    # Simulate editor caller
    app.dependency_overrides[require_task_reader] = lambda: CurrentUser(username="editor@x.test", claims={})
    monkeypatch.setattr(user_services, "has_role", lambda user, role: False)

    called_filters = []
    monkeypatch.setattr(
        services, "get_all_tasks", lambda f: (called_filters.append(f) or {"results": [], "total": 0}, 200)
    )

    client = TestClient(app)

    # 1. Editor querying own tasks -> allowed
    resp = client.post("/usertasks/tasks", json={"status": ["pending"], "user": "editor@x.test"})
    assert resp.status_code == 200
    assert called_filters[-1]["user"] == "editor@x.test"

    # 2. Editor omitting user -> automatically scoped to self
    resp = client.post("/usertasks/tasks", json={"status": ["pending"]})
    assert resp.status_code == 200
    assert called_filters[-1]["user"] == "editor@x.test"

    # 3. Editor querying someone else's tasks -> 403 Forbidden
    resp = client.post("/usertasks/tasks", json={"status": ["pending"], "user": "someone_else"})
    assert resp.status_code == 403

