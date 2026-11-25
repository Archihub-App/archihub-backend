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
        self.deleted: list = []

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

    def update_records(self, collection, filters, update):
        self.updated.append((collection, filters, update))
        modified = 0
        for row in self.rows.get(collection, []):
            if all(
                (row.get(k) in v["$in"] if isinstance(v, dict) and "$in" in v else row.get(k) == v)
                for k, v in filters.items()
            ):
                row.update(update)
                modified += 1
        return type("Result", (), {"modified_count": modified})()

    def delete_record(self, collection, filters):
        self.deleted.append((collection, filters))
        if collection in self.records:
            self.records[collection] = None

    def delete_records(self, collection, filters):
        self.deleted.append((collection, filters))
        if collection in self.rows:
            self.rows[collection] = [
                row for row in self.rows[collection]
                if not all(row.get(k) == v for k, v in filters.items())
            ]


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


def test_a_task_stores_its_details_and_not_a_link(mongo):
    mongo.records["usertasks"] = None
    body = {
        "resourceId": "r1", "user": "e@x.test", "comment": "c",
        "title": "  Revisar fechas  ", "instruction": "Las del acta", "dueDate": "2026-12-01",
        "locationType": "record", "locationLabel": "Acta 3", "locationPath": "Fondo / Serie",
        "resourceTitle": "Acta 3", "locationHref": "javascript:alert(1)", "assignedBy": "someone-else",
    }

    payload, status = services.create_task(body, "lead", is_team_lead=True)

    assert status == 201
    _collection, stored = mongo.inserted[0]
    assert stored["title"] == "Revisar fechas"
    assert stored["instruction"] == "Las del acta"
    assert stored["dueDate"] == "2026-12-01"
    assert stored["locationType"] == "record"
    assert stored["locationPath"] == "Fondo / Serie"
    assert stored["isPersonal"] is False
    assert "locationHref" not in stored
    assert "assignedBy" not in stored
    assert payload["title"] == "Revisar fechas"


def test_a_self_assigned_task_is_personal(mongo):
    mongo.records["usertasks"] = None
    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "e@x.test", "comment": "c"}, "e@x.test", is_team_lead=False
    )
    assert status == 201
    assert mongo.inserted[0][1]["isPersonal"] is True


@pytest.mark.parametrize(
    "extra",
    [
        {"dueDate": "mañana"},
        {"dueDate": "2026-13-01"},
        {"dueDate": 20261201},
        {"title": ["a"]},
        {"title": "x" * 201},
        {"instruction": "x" * 5001},
        {"locationType": "elsewhere"},
    ],
)
def test_malformed_task_details_are_refused(mongo, extra):
    mongo.records["usertasks"] = None
    body = {"resourceId": "r1", "user": "e@x.test", "comment": "c", **extra}
    _payload, status = services.create_task(body, "lead", is_team_lead=True)
    assert status == 400
    assert mongo.inserted == []


# ---------------------------------------------------------------------------
# Collection and upload work
# ---------------------------------------------------------------------------


@pytest.fixture
def work(mongo, monkeypatch):
    """A collection ``actas`` that exists and that ``ed`` may see and edit."""
    state = {"editors": {"ed"}, "types": {"actas"}, "visible": {"actas"}, "editable": {"actas"}}
    monkeypatch.setattr(services, "_holds_role", lambda u, role: role == "editor" and u in state["editors"])
    monkeypatch.setattr(services, "_type_exists", lambda slug: slug in state["types"])
    monkeypatch.setattr(services, "may_view_type", lambda u, slug: slug in state["visible"])
    monkeypatch.setattr(services, "may_edit_type", lambda u, slug: slug in state["editable"])
    mongo.records["usertasks"] = None
    return state


def _work_task(location_type, **extra):
    return {"locationType": location_type, "user": "ed", "comment": "c", **extra}


def test_a_collection_task_stores_its_type_and_no_target(mongo, work):
    payload, status = services.create_task(
        _work_task("collection", resourceType=" actas "), "lead", is_team_lead=True
    )

    assert status == 201
    stored = mongo.inserted[0][1]
    assert stored["postType"] == "actas"
    assert "resourceId" not in stored and "recordId" not in stored
    assert payload["postType"] == "actas"


def test_an_upload_task_has_no_target(mongo, work):
    _payload, status = services.create_task(_work_task("upload"), "lead", is_team_lead=True)

    assert status == 201
    stored = mongo.inserted[0][1]
    assert stored["locationType"] == "upload"
    assert not {"resourceId", "recordId", "postType"} & set(stored)


def test_several_collection_tasks_may_be_open_at_once(mongo, work):
    # An open task on the same collection does not block another one.
    mongo.records["usertasks"] = {"_id": ObjectId(VALID_ID), "status": "in_progress", "postType": "actas"}

    _payload, status = services.create_task(
        _work_task("collection", resourceType="actas"), "lead", is_team_lead=True
    )
    assert status == 201


@pytest.mark.parametrize(
    "body, change, expected",
    [
        (_work_task("collection"), None, 400),                             # no collection
        (_work_task("collection", resourceType=["actas"]), None, 400),     # not text
        (_work_task("collection", resourceType="otra"), None, 404),        # does not exist
        (_work_task("collection", resourceType="actas"), "hidden", 404),   # creator cannot see it
        (_work_task("collection", resourceType="actas"), "readonly", 400), # assignee cannot edit it
        (_work_task("collection", resourceType="actas"), "transcriber", 400),
        (_work_task("upload"), "transcriber", 400),                        # not an editor
    ],
)
def test_collection_and_upload_work_needs_an_editor_who_may_edit(mongo, work, body, change, expected):
    if change == "hidden":
        work["visible"].clear()
    elif change == "readonly":
        work["editable"].clear()
    elif change == "transcriber":
        work["editors"].clear()

    _payload, status = services.create_task(body, "lead", is_team_lead=True)

    assert status == expected
    assert mongo.inserted == []


def test_the_listing_names_a_collection_tasks_type(mongo):
    mongo.rows["usertasks"] = [
        {"_id": ObjectId(VALID_ID), "status": "pending", "postType": "actas", "locationType": "collection"}
    ]
    payload, status = services.get_all_tasks({"status": ["pending"], "page": 0})

    assert status == 200
    assert payload["results"][0]["resourceType"] == "actas"


def test_may_edit_type_needs_both_view_and_edit_rights(monkeypatch):
    import archihub.api.resources.access as access

    monkeypatch.setattr(services, "_holds_role", lambda u, role: False)
    monkeypatch.setattr(services, "may_view_type", lambda u, slug: True)
    monkeypatch.setattr(access, "holds_edit_role", lambda u, slug, is_admin: False)
    assert services.may_edit_type("ed", "actas") is False

    monkeypatch.setattr(access, "holds_edit_role", lambda u, slug, is_admin: True)
    assert services.may_edit_type("ed", "actas") is True

    monkeypatch.setattr(services, "may_view_type", lambda u, slug: False)
    assert services.may_edit_type("ed", "actas") is False


def test_editors_list_carries_their_assignable_roles(mongo):
    mongo.rows["users"] = [
        {"name": "Ana", "username": "ana", "roles": ["editor", "team_lead"]},
        {"username": "tom", "roles": ["transcriber"]},
    ]
    options, status = services.get_editors()

    assert status == 200
    assert options == [
        {"label": "Ana", "value": "ana", "roles": ["editor"]},
        {"label": "tom", "value": "tom", "roles": ["transcriber"]},
    ]


def test_rejected_tasks_are_migrated_to_pending(mongo):
    mongo.rows["usertasks"] = [
        {"_id": 1, "status": "rejected"},
        {"_id": 2, "status": "review"},
        {"_id": 3, "status": "rejected"},
    ]

    assert services.migrate_legacy_statuses() == 2
    assert [row["status"] for row in mongo.rows["usertasks"]] == ["pending", "review", "pending"]
    assert services.migrate_legacy_statuses() == 0


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


def test_the_listing_carries_comments_and_task_details(mongo):
    """Every participant sees the title, due date and thread, not just the creator."""
    mongo.rows["usertasks"] = [
        {
            "_id": ObjectId(VALID_ID), "status": "pending", "createdAt": None,
            "title": "Revisar fechas", "dueDate": "2026-12-01",
            "comment": [{"user": "lead", "comment": "Las del acta"}],
        }
    ]
    payload, status = services.get_all_tasks({"status": ["pending"], "page": 0})

    assert status == 200
    row = payload["results"][0]
    assert row["title"] == "Revisar fechas"
    assert row["dueDate"] == "2026-12-01"
    assert row["comment"] == "lead: Las del acta"
    for field in ("comment", "dueDate", "title", "instruction", "locationType"):
        assert field in services._LIST_PROJECTION


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


def test_editor_or_transcriber_can_assign_a_task_to_themselves(mongo):
    mongo.records["usertasks"] = None

    payload, status = services.create_task(
        {"resourceId": "r1", "user": "ed@x.test", "comment": "taking this"},
        "ed@x.test",
        is_team_lead=False,
    )

    assert status == 201
    assert payload["status"] == "pending"
    assert payload["user"] == "ed@x.test"
    assert payload["createdBy"] == "ed@x.test"


def test_user_without_assignable_role_cannot_self_assign(mongo, monkeypatch):
    mongo.records["usertasks"] = None
    monkeypatch.setattr(services, "_is_assignable", lambda username: False)

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "plain_user", "comment": "taking this"},
        "plain_user",
        is_team_lead=False,
    )

    assert status == ROLE_FAILURE_STATUS
    assert mongo.inserted == []


def test_non_lead_user_cannot_assign_a_task_to_someone_else(mongo):
    mongo.records["usertasks"] = None

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "other_user", "comment": "do this"},
        "ed@x.test",
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


def test_self_assign_and_lifecycle_via_router(mongo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from archihub.api.usertasks.router import router
    from archihub.core.errors import register_exception_handlers
    from archihub.core.security.jwt import CurrentUser, get_current_user
    from archihub.api.users import services as user_services

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)

    # 1. Plain user (neither editor nor transcriber) cannot self-assign.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="plain@x.test", claims={})
    monkeypatch.setattr(user_services, "has_role", lambda user, role: False)
    monkeypatch.setattr(services, "_is_assignable", lambda username: False)

    resp = client.post(
        "/usertasks",
        json={"resourceId": "res1", "user": "plain@x.test", "comment": "nope"},
    )
    assert resp.status_code == 403

    # 2. Editor or transcriber can self-assign, but cannot assign others or list editors.
    for role_name in ("editor", "transcriber"):
        username = f"{role_name}@x.test"
        app.dependency_overrides[get_current_user] = lambda u=username: CurrentUser(username=u, claims={})
        monkeypatch.setattr(user_services, "has_role", lambda user, role, r=role_name: role == r)
        monkeypatch.setattr(services, "_is_assignable", lambda u, target=username: u == target)

        assert client.post(
            "/usertasks",
            json={"resourceId": "res1", "user": "other@x.test", "comment": "nope"},
        ).status_code == 403
        assert client.get("/usertasks/editors").status_code == 403

        create_resp = client.post(
            "/usertasks",
            json={"resourceId": "res1", "user": username, "comment": "mine"},
        )
        assert create_resp.status_code == 201
        assert create_resp.json()["status"] == "pending"

    # 3. Verify open -> in_progress -> review via routes for transcriber.
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res1",
        "status": "pending",
        "user": "transcriber@x.test",
        "createdBy": "transcriber@x.test",
        "comment": [{"user": "transcriber@x.test", "comment": "mine"}],
    }
    open_resp = client.get("/usertasks/res1")
    assert open_resp.status_code == 200
    assert open_resp.json()["status"] == "in_progress"

    review_resp = client.put(f"/usertasks/{VALID_ID}", json={"status": "review", "comment": "ready"})
    assert review_resp.status_code == 200
    assert review_resp.json()["status"] == "review"


# ---------------------------------------------------------------------------
# Deletion (creator / assigner only)
# ---------------------------------------------------------------------------


def test_creator_may_delete_their_task_and_record_audit_and_hook(mongo, monkeypatch):
    audits = []
    hooks = []
    monkeypatch.setattr(services, "_audit", lambda u, action, d: audits.append((u, action, d)))
    monkeypatch.setattr(services, "_call_hook", lambda name, p: hooks.append((name, p)))
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "r1",
        "status": "pending",
        "user": "editor@x.test",
        "createdBy": "lead@x.test",
        "comment": [{"user": "lead@x.test", "comment": "check"}],
    }

    payload, status = services.delete_task(VALID_ID, "lead@x.test")

    assert status == 200
    assert "msg" in payload
    assert mongo.deleted == [
        ("usertasks", {"_id": ObjectId(VALID_ID)}),
        ("usertask_notifications", {"taskId": VALID_ID}),
    ]
    assert audits == [("lead@x.test", "usertask_delete", {"resource": "r1", "task": VALID_ID, "assignee": "editor@x.test"})]
    assert len(hooks) == 1
    assert hooks[0][0] == "usertask_delete"
    assert hooks[0][1]["_id"] == VALID_ID


def test_legacy_task_without_created_by_uses_first_comment_author(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "recordId": "rec1",
        "status": "pending",
        "user": "editor@x.test",
        "comment": [{"user": "lead@x.test", "comment": "check"}],
    }

    _payload, status = services.delete_task(VALID_ID, "lead@x.test")
    assert status == 200
    assert mongo.deleted == [
        ("usertasks", {"_id": ObjectId(VALID_ID)}),
        ("usertask_notifications", {"taskId": VALID_ID}),
    ]


def test_assignee_and_other_users_cannot_delete_someone_elses_task(mongo, monkeypatch):
    monkeypatch.setattr(services, "_is_admin", lambda user: False)
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "r1",
        "status": "pending",
        "user": "editor@x.test",
        "createdBy": "lead@x.test",
        "comment": [{"user": "lead@x.test", "comment": "check"}],
    }

    for caller in ("editor@x.test", "other_lead@x.test"):
        _payload, status = services.delete_task(VALID_ID, caller)
        assert status == ROLE_FAILURE_STATUS
        assert mongo.deleted == []


def test_task_without_creator_cannot_be_deleted_by_a_non_admin(mongo, monkeypatch):
    monkeypatch.setattr(services, "_is_admin", lambda user: False)
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "r1",
        "status": "pending",
        "user": "editor@x.test",
        "comment": [],
    }

    _payload, status = services.delete_task(VALID_ID, "editor@x.test")
    assert status == ROLE_FAILURE_STATUS
    assert mongo.deleted == []


@pytest.mark.parametrize("creator", ["lead@x.test", None])
def test_an_admin_may_delete_any_task(mongo, monkeypatch, creator):
    """Including one whose creator is gone, which nobody else could remove."""
    monkeypatch.setattr(services, "_is_admin", lambda user: user == "admin@x.test")
    monkeypatch.setattr(services, "_audit", lambda *a: None)
    monkeypatch.setattr(services, "_call_hook", lambda *a: None)
    task = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "r1",
        "status": "pending",
        "user": "editor@x.test",
        "comment": [],
    }
    if creator:
        task["createdBy"] = creator
    mongo.records["usertasks"] = task

    _payload, status = services.delete_task(VALID_ID, "admin@x.test")

    assert status == 200
    assert ("usertasks", {"_id": ObjectId(VALID_ID)}) in mongo.deleted


def test_delete_missing_or_malformed_task_is_not_found(mongo):
    mongo.records["usertasks"] = None
    assert services.delete_task(VALID_ID, "lead@x.test")[1] == 404
    assert services.delete_task("not-an-id", "lead@x.test")[1] == 404


def test_delete_task_via_router(mongo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from archihub.api.usertasks.router import router
    from archihub.core.errors import register_exception_handlers
    from archihub.core.security.jwt import CurrentUser, get_current_user

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    client = TestClient(app)

    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "res1",
        "status": "pending",
        "user": "editor@x.test",
        "createdBy": "lead@x.test",
        "comment": [{"user": "lead@x.test", "comment": "check"}],
    }

    # Assignee cannot delete
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="editor@x.test", claims={})
    assert client.delete(f"/usertasks/{VALID_ID}").status_code == 403
    assert mongo.deleted == []

    # Another user cannot delete
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="admin@x.test", claims={})
    assert client.delete(f"/usertasks/{VALID_ID}").status_code == 403
    assert mongo.deleted == []

    # Creator / assigner can delete
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="lead@x.test", claims={})
    resp = client.delete(f"/usertasks/{VALID_ID}")
    assert resp.status_code == 200
    assert "msg" in resp.json()
    assert mongo.deleted == [
        ("usertasks", {"_id": ObjectId(VALID_ID)}),
        ("usertask_notifications", {"taskId": VALID_ID}),
    ]


# ---------------------------------------------------------------------------
# Task Notifications
# ---------------------------------------------------------------------------


def test_creating_a_task_for_someone_else_creates_a_notification(mongo):
    mongo.records["usertasks"] = None

    payload, status = services.create_task(
        {"resourceId": "r1", "user": "editor@x.test", "comment": "please review"},
        "lead@x.test",
        is_team_lead=True,
    )

    assert status == 201
    notifs = [rec for coll, rec in mongo.inserted if coll == services.NOTIFICATIONS_COLLECTION]
    assert len(notifs) == 1
    assert notifs[0]["user"] == "editor@x.test"
    assert notifs[0]["actor"] == "lead@x.test"
    assert notifs[0]["type"] == "created"
    assert notifs[0]["taskId"] == payload["_id"]
    assert notifs[0]["active"] is True


def test_self_assigning_a_task_does_not_notify_the_creator(mongo):
    mongo.records["usertasks"] = None

    _payload, status = services.create_task(
        {"resourceId": "r1", "user": "editor@x.test", "comment": "self"},
        "editor@x.test",
        is_team_lead=False,
    )

    assert status == 201
    notifs = [rec for coll, rec in mongo.inserted if coll == services.NOTIFICATIONS_COLLECTION]
    assert notifs == []


def test_updating_task_notifies_other_participants_only(mongo):
    mongo.records["usertasks"] = {
        "_id": ObjectId(VALID_ID),
        "resourceId": "r1",
        "status": "in_progress",
        "user": "editor@x.test",
        "createdBy": "lead@x.test",
        "comment": [{"user": "lead@x.test", "comment": "initial"}],
    }

    _payload, status = services.update_task(
        VALID_ID, {"comment": "ready for review", "status": "review"}, "editor@x.test", is_team_lead=False
    )

    assert status == 200
    notifs = [rec for coll, rec in mongo.inserted if coll == services.NOTIFICATIONS_COLLECTION]
    assert len(notifs) == 1
    assert notifs[0]["user"] == "lead@x.test"
    assert notifs[0]["actor"] == "editor@x.test"
    assert notifs[0]["type"] == "update"
    assert notifs[0]["status"] == "review"
    assert notifs[0]["comment"] == "ready for review"


def test_get_and_deactivate_notifications_via_router(mongo):
    from datetime import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from archihub.api.usertasks.router import router
    from archihub.core.errors import register_exception_handlers
    from archihub.core.security.jwt import CurrentUser, get_current_user

    nid1 = "6a70b8c3497d4440325c94c4"
    nid2 = "6a70b8c3497d4440325c94c5"
    mongo.rows[services.NOTIFICATIONS_COLLECTION] = [
        {
            "_id": ObjectId(nid1),
            "taskId": VALID_ID,
            "user": "editor@x.test",
            "actor": "lead@x.test",
            "type": "comment",
            "status": "pending",
            "active": True,
            "createdAt": datetime(2026, 11, 24, 10, 0, 0),
        },
        {
            "_id": ObjectId(nid2),
            "taskId": VALID_ID,
            "user": "editor@x.test",
            "actor": "lead@x.test",
            "type": "status",
            "status": "approved",
            "active": True,
            "createdAt": datetime(2026, 11, 24, 10, 5, 0),
        },
    ]

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="editor@x.test", claims={})
    client = TestClient(app)

    # Listing returns active notifications
    resp = client.get("/usertasks/notifications")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    assert len(resp.json()["results"]) == 2

    # Deactivating a single notification belonging to someone else is refused
    mongo.records[services.NOTIFICATIONS_COLLECTION] = {
        "_id": ObjectId(nid1),
        "user": "someone_else@x.test",
        "active": True,
    }
    assert client.put(f"/usertasks/notifications/{nid1}/read").status_code == 403

    # Deactivating own single notification succeeds
    mongo.records[services.NOTIFICATIONS_COLLECTION] = mongo.rows[services.NOTIFICATIONS_COLLECTION][0]
    single_resp = client.put(f"/usertasks/notifications/{nid1}/read")
    assert single_resp.status_code == 200
    assert single_resp.json()["active"] is False

    # Bulk deactivate remaining notifications
    bulk_resp = client.put("/usertasks/notifications/read", json={})
    assert bulk_resp.status_code == 200
    assert all(row["active"] is False for row in mongo.rows[services.NOTIFICATIONS_COLLECTION])
