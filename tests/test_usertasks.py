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
    # Targets and assignees are valid unless a test says otherwise.
    monkeypatch.setattr(services, "may_open_target", lambda field, value, user: True)
    monkeypatch.setattr(services, "_is_assignable", lambda username: True)
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
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "in_progress"}

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "please review"}, "lead", is_team_lead=True
    )
    assert status == 400
    assert mongo.inserted == []


def test_a_task_is_created_when_the_target_is_free(mongo):
    mongo.records["usertasks"] = None

    payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "please review"}, "lead", is_team_lead=True
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
    _payload, status = services.create_task(body, "lead", is_team_lead=True)
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


def test_a_team_lead_may_return_a_task_to_pending(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "review",
        "user": "editor@x.test",
        "comment": [],
    }

    payload, status = services.update_task(
        VALID_ID, {"status": "rejected", "comment": "needs corrections"}, "lead@x.test", is_team_lead=True
    )

    assert status == 200
    _c, _f, update = mongo.updated[0]
    assert update["status"] == "pending"
    assert payload["status"] == "pending"
    assert update["comment"][0]["comment"] == "needs corrections"

    # Returning explicitly with status="pending" also resets to pending.
    mongo.records["usertasks"]["status"] = "review"
    payload2, status2 = services.update_task(
        VALID_ID, {"status": "pending", "comment": "try again"}, "lead@x.test", is_team_lead=True
    )
    assert status2 == 200
    assert payload2["status"] == "pending"


def test_an_editor_cannot_reject_or_return_a_task(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "review",
        "user": "editor@x.test",
        "comment": [],
    }

    for return_status in ("rejected", "pending"):
        _payload, status = services.update_task(
            VALID_ID, {"status": return_status}, "editor@x.test", is_team_lead=False
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


def test_open_task_can_be_retrieved_in_review_or_in_progress_state(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res123",
        "status": "review",
        "user": "ed",
        "comment": [{"user": "ed", "comment": "done"}],
    }
    payload, status = services.get_resource_tasks("res123", "ed")
    assert status == 200
    assert payload["status"] == "review"
    assert "ed: done" in payload["comment"]

    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "recordId": "rec123",
        "status": "in_progress",
        "user": "ed",
        "comment": [{"user": "lead", "comment": "start"}],
    }
    payload, status = services.get_record_tasks("rec123", "ed")
    assert status == 200
    assert payload["status"] == "in_progress"
    assert "lead: start" in payload["comment"]


def test_create_task_records_audit_and_hook(mongo, monkeypatch):
    audits = []
    hooks = []
    monkeypatch.setattr(services, "_audit", lambda u, action, d: audits.append((u, action, d)))
    monkeypatch.setattr(services, "_call_hook", lambda name, p: hooks.append((name, p)))
    mongo.records["usertasks"] = None

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "check this"}, "lead", is_team_lead=True
    )

    assert status == 201
    assert len(audits) == 1
    assert audits[0][1] == "usertask_create"
    assert audits[0][2]["assignee"] == "e@x.test"
    assert audits[0][2]["resource"] == "r1"
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
    from archihub.api.usertasks.router import router
    from archihub.core.security.jwt import CurrentUser, get_current_user
    from archihub.api.users import services as user_services

    app = FastAPI()
    app.include_router(router)

    # Simulate editor caller
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="editor@x.test", claims={})
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


def test_listing_tasks_as_admin_or_team_lead_allows_all(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from archihub.api.usertasks.router import router
    from archihub.core.security.jwt import CurrentUser, get_current_user
    from archihub.api.users import services as user_services

    app = FastAPI()
    app.include_router(router)

    # Simulate admin caller
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="admin@x.test", claims={})
    monkeypatch.setattr(user_services, "has_role", lambda user, role: role in ("admin", "team_lead"))

    called_filters = []
    monkeypatch.setattr(
        services, "get_all_tasks", lambda f: (called_filters.append(f) or {"results": [], "total": 0}, 200)
    )

    client = TestClient(app)

    # 1. Admin querying without user filter -> sees all tasks
    resp = client.post("/usertasks/tasks", json={"status": ["pending"]})
    assert resp.status_code == 200
    assert called_filters[-1]["user"] is None
    assert called_filters[-1]["include_created"] is False

    # 2. Admin querying someone else's tasks -> allowed
    resp = client.post("/usertasks/tasks", json={"status": ["pending"], "user": "editor@x.test"})
    assert resp.status_code == 200
    assert called_filters[-1]["user"] == "editor@x.test"


def test_get_all_tasks_query_filters_by_created_or_assigned(mongo):
    queries = []
    mongo.get_all_records = lambda coll, q, **kw: queries.append(q) or []
    mongo.count = lambda coll, q: 0

    # Editor query: includes assigned and created
    services.get_all_tasks({"status": ["pending"], "user": "ed@x.test", "include_created": True})
    assert len(queries) == 1
    assert queries[0]["status"] == {"$in": ["pending"]}
    assert "$or" in queries[0]
    assert {"user": "ed@x.test"} in queries[0]["$or"]
    assert {"createdBy": "ed@x.test"} in queries[0]["$or"]

    # Admin query without user: matches all
    services.get_all_tasks({"status": ["pending"]})
    assert len(queries) == 2
    assert "$or" not in queries[1]
    assert queries[1]["user"] == {"$exists": True}


def test_creator_of_task_can_comment_and_update(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "pending",
        "user": "assignee@x.test",
        "createdBy": "creator@x.test",
        "comment": [],
    }

    payload, status = services.update_task(
        VALID_ID, {"comment": "check notes"}, "creator@x.test", is_team_lead=False
    )
    assert status == 200
    assert len(mongo.updated) == 1


def test_create_task_records_created_by_field(mongo):
    mongo.records["usertasks"] = None
    created, status = services.create_task(
        {"resourceId": "r1", "user": "assignee@x.test", "comment": "work on this"},
        "creator@x.test",
        is_team_lead=True,
    )
    assert status == 201
    assert created["createdBy"] == "creator@x.test"
    _coll, record = mongo.inserted[0]
    assert record["createdBy"] == "creator@x.test"


def test_a_task_on_a_target_the_creator_cannot_open_is_refused(mongo, monkeypatch):
    monkeypatch.setattr(services, "may_open_target", lambda field, value, user: False)

    _payload, status = services.create_task(
        {"resourceId": "res1", "user": "ed", "comment": "check"}, "lead", is_team_lead=True
    )

    assert status == 404
    assert mongo.inserted == []


def test_a_task_is_assigned_only_to_someone_who_can_take_it(mongo, monkeypatch):
    monkeypatch.setattr(services, "_is_assignable", lambda username: False)

    _payload, status = services.create_task(
        {"resourceId": "res1", "user": "nobody", "comment": "check"}, "lead", is_team_lead=True
    )

    assert status == 400
    assert mongo.inserted == []


def test_an_assignee_needs_an_assignable_role(mongo, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(services, "_mongo", lambda: mongo)
    seen = {}

    def users(filters):
        seen.update(filters)
        return None

    mongo.records["users"] = users

    assert services._is_assignable("ed") is False
    assert seen == {"username": "ed", "roles": {"$in": services.ASSIGNABLE_ROLES}}


def test_a_missing_or_binned_resource_cannot_be_a_task_target(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)

    assert services.may_open_target("resourceId", VALID_ID, "ed") is False
    assert services.may_open_target("resourceId", "not-an-id", "ed") is False

    fake.records["resources"] = {"_id": ObjectId(VALID_ID), "status": "deleted"}
    assert services.may_open_target("resourceId", VALID_ID, "ed") is False


def test_a_task_is_not_shown_to_someone_outside_it(mongo, monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)
    monkeypatch.setattr("archihub.api.users.services.has_permission", lambda u, p: False)
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID), "resourceId": "res1", "status": "pending",
        "user": "ed", "createdBy": "lead", "comment": [{"user": "lead", "comment": "x"}],
    }

    assert services.get_resource_tasks("res1", "stranger")[1] == 404
    assert services.get_resource_tasks("res1", "ed")[1] == 200


def test_a_task_is_not_shown_when_its_target_cannot_be_opened(mongo, monkeypatch):
    monkeypatch.setattr(services, "may_open_target", lambda field, value, user: False)
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID), "recordId": "rec1", "status": "pending", "user": "ed",
    }

    assert services.get_record_tasks("rec1", "ed")[1] == 404


# ---------------------------------------------------------------------------
# Self-assignment and full 4-state workflow
# ---------------------------------------------------------------------------


def test_any_user_can_assign_a_task_to_themselves(mongo, monkeypatch):
    mongo.records["usertasks"] = None
    # Self-assignment does not require an editor/transcriber role.
    monkeypatch.setattr(services, "_is_assignable", lambda username: False)

    payload, status = services.create_task(
        {"resourceId": "r1", "user": "plain_user", "comment": "taking this"},
        "plain_user",
        is_team_lead=False,
    )

    assert status == 201
    assert payload["status"] == "pending"
    assert payload["user"] == "plain_user"
    assert payload["createdBy"] == "plain_user"


def test_non_lead_user_cannot_assign_a_task_to_someone_else(mongo):
    mongo.records["usertasks"] = None

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "other_user", "comment": "do this"},
        "plain_user",
        is_team_lead=False,
    )

    assert status == ROLE_FAILURE_STATUS
    assert mongo.inserted == []


def test_opening_pending_task_as_assignee_moves_it_to_in_progress(mongo, monkeypatch):
    audits = []
    hooks = []
    monkeypatch.setattr(services, "_audit", lambda u, action, d: audits.append((u, action, d)))
    monkeypatch.setattr(services, "_call_hook", lambda name, p: hooks.append((name, p)))
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res1",
        "status": "pending",
        "user": "ed",
        "createdBy": "lead",
        "comment": [{"user": "lead", "comment": "check"}],
    }

    payload, status = services.get_resource_tasks("res1", "ed")

    assert status == 200
    assert payload["status"] == "in_progress"
    assert mongo.updated == [("usertasks", {"_id": ObjectId(VALID_ID)}, {"status": "in_progress"})]
    assert len(audits) == 1
    assert audits[0][1] == "usertask_update"
    assert audits[0][2]["status"] == "in_progress"
    assert len(hooks) == 1
    assert hooks[0][0] == "usertask_update"

    # Opening again while already in_progress does not write a second update.
    payload2, status2 = services.get_resource_tasks("res1", "ed")
    assert status2 == 200
    assert payload2["status"] == "in_progress"
    assert len(mongo.updated) == 1


def test_opening_pending_task_as_non_assignee_keeps_it_pending(mongo, monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r == "team_lead")
    monkeypatch.setattr("archihub.api.users.services.has_permission", lambda u, p: False)
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "recordId": "rec1",
        "status": "pending",
        "user": "ed",
        "createdBy": "lead",
        "comment": [{"user": "lead", "comment": "check"}],
    }

    payload, status = services.get_record_tasks("rec1", "lead")

    assert status == 200
    assert payload["status"] == "pending"
    assert mongo.updated == []


def test_task_in_review_cannot_be_pulled_back_by_assignee(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "status": "review",
        "user": "ed",
        "comment": [],
    }

    for target_status in ("in_progress", "pending", "approved"):
        _payload, status = services.update_task(
            VALID_ID, {"status": target_status}, "ed", is_team_lead=False
        )
        assert status == ROLE_FAILURE_STATUS
        assert mongo.updated == []


def test_returned_task_restarts_from_pending_and_reenters_in_progress_on_open(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res1",
        "status": "review",
        "user": "ed",
        "createdBy": "lead",
        "comment": [{"user": "lead", "comment": "initial"}],
    }

    # Team lead returns task -> resets to pending ("por hacer").
    returned, status = services.update_task(
        VALID_ID, {"status": "rejected", "comment": "fix section 2"}, "lead", is_team_lead=True
    )
    assert status == 200
    assert returned["status"] == "pending"

    # Assigned user opens it again -> transitions back to in_progress.
    reopened, status = services.get_resource_tasks("res1", "ed")
    assert status == 200
    assert reopened["status"] == "in_progress"

    # Assigned user sends it to review again.
    reviewed, status = services.update_task(
        VALID_ID, {"status": "review", "comment": "fixed"}, "ed", is_team_lead=False
    )
    assert status == 200
    assert reviewed["status"] == "review"

    # Team lead approves -> completed ("approved").
    approved, status = services.update_task(
        VALID_ID, {"status": "approved"}, "lead", is_team_lead=True
    )
    assert status == 200
    assert approved["status"] == "approved"


def test_plain_user_self_assign_and_lifecycle_via_router(mongo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from archihub.api.usertasks.router import router
    from archihub.core.errors import register_exception_handlers
    from archihub.core.security.jwt import CurrentUser, get_current_user
    from archihub.api.users import services as user_services

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="plain@x.test", claims={})
    monkeypatch.setattr(user_services, "has_role", lambda user, role: False)
    monkeypatch.setattr(services, "_is_assignable", lambda username: False)

    client = TestClient(app)

    # Assigning someone else is forbidden for a plain user.
    resp = client.post(
        "/usertasks",
        json={"resourceId": "res1", "user": "other@x.test", "comment": "nope"},
    )
    assert resp.status_code == 403

    # Listing assignable editors is forbidden for a plain user.
    assert client.get("/usertasks/editors").status_code == 403

    # Self-assignment succeeds with initial status "pending".
    resp = client.post(
        "/usertasks",
        json={"resourceId": "res1", "user": "plain@x.test", "comment": "mine"},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"

    # Simulate persisted task and verify open -> in_progress -> review via routes.
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res1",
        "status": "pending",
        "user": "plain@x.test",
        "createdBy": "plain@x.test",
        "comment": [{"user": "plain@x.test", "comment": "mine"}],
    }
    open_resp = client.get("/usertasks/res1")
    assert open_resp.status_code == 200
    assert open_resp.json()["status"] == "in_progress"

    review_resp = client.put(f"/usertasks/{VALID_ID}", json={"status": "review", "comment": "ready"})
    assert review_resp.status_code == 200
    assert review_resp.json()["status"] == "review"
