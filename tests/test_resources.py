"""Resource listing and detail.

The archive's highest-traffic read path.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from bson.objectid import ObjectId

from archihub.api.resources import services
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

VALID_ID = "6a70b833497d4440325c94b1"


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.rows: dict[str, list] = {}
        self.last_query: dict | None = None
        self.last_fields: dict | None = None

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        self.last_query = filters
        self.last_fields = fields
        return [dict(r) for r in self.rows.get(collection, [])]

    def count(self, collection, filters=None):
        return len(self.rows.get(collection, []))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services.access, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_access_right_term", lambda v: v)
    return fake


@pytest.fixture
def as_admin(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: True)


@pytest.fixture
def as_user(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


# ---------------------------------------------------------------------------
# Detail - existence must not leak
# ---------------------------------------------------------------------------


def test_a_resource_the_caller_may_not_see_is_404_not_403(mongo, as_user):
    """A distinct status would confirm the resource exists.

    404 for both "absent" and "forbidden" means an unauthorised caller learns
    nothing from the difference.
    """
    mongo.records["resources"] = {"_id": ObjectId(VALID_ID), "accessRights": ["restricted"]}
    mongo.records["users"] = {"accessRights": ["public"]}

    payload, status = services.get_by_id(VALID_ID, "alice")

    assert status == 404
    assert payload["msg"] == services._("Resource does not exist")


def test_an_absent_resource_is_404_with_the_same_message(mongo, as_user):
    mongo.records["resources"] = None
    payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 404


def test_a_malformed_id_is_404_not_500(mongo, as_user):
    _payload, status = services.get_by_id("not-an-object-id", "alice")
    assert status == 404


def test_an_unrestricted_resource_is_visible(mongo, as_user):
    mongo.records["resources"] = {"_id": ObjectId(VALID_ID), "accessRights": None}
    mongo.records["users"] = {"accessRights": []}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 200


def test_a_matching_right_grants_access(mongo, as_user):
    mongo.records["resources"] = {"_id": ObjectId(VALID_ID), "accessRights": ["internal"]}
    mongo.records["users"] = {"accessRights": ["internal", "public"]}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 200


def test_an_admin_sees_a_restricted_resource(mongo, as_admin):
    mongo.records["resources"] = {"_id": ObjectId(VALID_ID), "accessRights": ["secret"]}

    _payload, status = services.get_by_id(VALID_ID, "admin")
    assert status == 200


# ---------------------------------------------------------------------------
# Detail - the three gates
#
# An earlier version of this port applied only the access-rights one, checked
# against the resource's own field. That was wrong in three separate ways, all
# of which widened access; these pin each of them.
# ---------------------------------------------------------------------------


PARENT_ID = "6a70b833497d4440325c94b2"


def test_access_rights_are_inherited_from_an_ancestor(mongo, as_user):
    """Restricting a fonds restricts everything filed under it.

    That is how archival access conditions are normally expressed, and reading
    only the resource's own field would make a reserved series full of public
    items - the opposite of what the archivist configured.
    """
    mongo.records["resources"] = {
        "_id": ObjectId(VALID_ID),
        "accessRights": None,
        "parents": [{"id": PARENT_ID}],
    }
    mongo.rows["resources"] = [{"_id": ObjectId(PARENT_ID), "accessRights": "reserved"}]
    mongo.records["users"] = {"accessRights": ["public"]}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 404


def test_holding_the_inherited_right_grants_access(mongo, as_user):
    mongo.records["resources"] = {
        "_id": ObjectId(VALID_ID),
        "accessRights": None,
        "parents": [{"id": PARENT_ID}],
    }
    mongo.rows["resources"] = [{"_id": ObjectId(PARENT_ID), "accessRights": "reserved"}]
    mongo.records["users"] = {"accessRights": ["reserved"]}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 200


def test_a_resource_with_a_declared_right_does_not_consult_its_ancestors(mongo, as_user):
    """Its own condition is the more specific one."""
    mongo.records["resources"] = {
        "_id": ObjectId(VALID_ID),
        "accessRights": "internal",
        "parents": [{"id": PARENT_ID}],
    }
    mongo.rows["resources"] = [{"_id": ObjectId(PARENT_ID), "accessRights": "reserved"}]
    mongo.records["users"] = {"accessRights": ["internal"]}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 200


def test_an_unusable_ancestor_id_does_not_break_the_lookup(mongo, as_user):
    mongo.records["resources"] = {
        "_id": ObjectId(VALID_ID),
        "accessRights": None,
        "parents": [{"id": "not-an-object-id"}],
    }
    mongo.records["users"] = {"accessRights": []}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 200


def test_a_deleted_resource_is_hidden_from_non_admins(mongo, as_user):
    mongo.records["resources"] = {
        "_id": ObjectId(VALID_ID),
        "accessRights": None,
        "status": "deleted",
    }
    mongo.records["users"] = {"accessRights": []}

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 404


def test_an_admin_may_open_a_deleted_resource(mongo, as_admin):
    mongo.records["resources"] = {
        "_id": ObjectId(VALID_ID),
        "accessRights": None,
        "status": "deleted",
    }

    _payload, status = services.get_by_id(VALID_ID, "admin")
    assert status == 200


def test_a_content_types_view_roles_are_enforced_on_the_detail_route(mongo, as_user):
    """The listing has always applied this; the detail route must too, or the
    restriction is one guessed URL away from being bypassed."""

    def by_collection(filters):
        return {"viewRoles": ["curator"]}

    mongo.records["resources"] = {"_id": ObjectId(VALID_ID), "accessRights": None, "post_type": "foto"}
    mongo.records["users"] = {"accessRights": []}
    mongo.records["post_types"] = by_collection

    _payload, status = services.get_by_id(VALID_ID, "alice")
    assert status == 404


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_a_non_admin_listing_is_access_constrained(mongo, as_user, monkeypatch):
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.records["users"] = {"accessRights": ["public"]}
    mongo.rows["resources"] = []

    services.get_all({"post_type": ["carpeta"], "status": "published"}, "alice")

    assert "$and" in mongo.last_query


def test_an_admin_listing_is_not_access_constrained(mongo, as_admin, monkeypatch):
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    services.get_all({"post_type": ["carpeta"], "status": "published"}, "admin")

    assert "$and" not in mongo.last_query


def test_a_hidden_content_type_is_refused(mongo, as_user, monkeypatch):
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: False)

    _payload, status = services.get_all({"post_type": ["restricted"]}, "alice")
    assert status == ROLE_FAILURE_STATUS


def test_the_listing_reports_a_file_count_not_the_files(mongo, as_admin, monkeypatch):
    """Returning the file records themselves would inflate every listing page."""
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = [
        {"_id": ObjectId(VALID_ID), "filesObj": [{"id": 1}, {"id": 2}, {"id": 3}]}
    ]

    payload, _status = services.get_all({"post_type": ["x"]}, "admin")

    assert payload["resources"][0]["files"] == 3
    assert "filesObj" not in payload["resources"][0]


def test_dates_are_serialised(mongo, as_admin, monkeypatch):
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = [{"_id": ObjectId(VALID_ID), "createdAt": datetime(2026, 1, 2, 3, 4, 5)}]

    payload, _status = services.get_all({"post_type": ["x"]}, "admin")
    assert payload["resources"][0]["createdAt"] == "2026-01-02T03:04:05"


# ---------------------------------------------------------------------------
# activeColumns
#
# The frontend sends column DESCRIPTORS - {destiny, label, sortBy} - not names.
# Every test below passes that real shape, because an earlier version of these
# tests passed bare strings, agreed with the implementation, and both were wrong
# together: the live listing raised `TypeError: unhashable type: 'dict'` on the
# first request the browser made.
# ---------------------------------------------------------------------------


def _columns(*destinies):
    """A column list shaped the way `upgrade_front` actually sends it."""
    return [{"destiny": d, "label": f"Label for {d}", "sortBy": d} for d in destinies]


def test_a_column_descriptor_is_projected_by_its_destiny(mongo, as_admin, monkeypatch):
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    services.get_all(
        {"post_type": ["x"], "activeColumns": _columns("metadata.firstLevel.date")}, "admin"
    )

    assert mongo.last_fields["metadata.firstLevel.date"] == 1
    # The label is presentation and must not reach the projection.
    assert not any("Label" in key for key in mongo.last_fields)


def test_bare_strings_are_still_accepted(mongo, as_admin, monkeypatch):
    """Hand-written callers - the diff harness, curl, another org's script."""
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    services.get_all({"post_type": ["x"], "activeColumns": ["metadata.title"]}, "admin")

    assert mongo.last_fields["metadata.title"] == 1


def test_structural_columns_are_not_projected_as_metadata(mongo, as_admin, monkeypatch):
    """`ident`/`createdAt`/`files`/`accessRights` are in the base projection
    already, and the frontend sends an empty descriptor as the first column."""
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    services.get_all(
        {"post_type": ["x"], "activeColumns": _columns("", "createdAt", "ident", "files")},
        "admin",
    )

    assert "" not in mongo.last_fields
    assert "filesObj" in mongo.last_fields  # the base projection still stands


def test_displaying_a_column_does_not_narrow_the_result_set(mongo, as_admin, monkeypatch):
    """A column picker changes what is shown, never which resources exist.

    Requiring the field to be present would drop every resource that has not
    filled it in - and move `total` with it, so the pager would disagree with
    the tree beside it.
    """
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    services.get_all(
        {"post_type": ["x"], "activeColumns": _columns("metadata.date", "metadata.title")},
        "admin",
    )

    assert "metadata.date" not in mongo.last_query
    assert "metadata.title" not in mongo.last_query


def test_a_repeated_column_is_projected_once(mongo, as_admin, monkeypatch):
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    services.get_all(
        {"post_type": ["x"], "activeColumns": _columns("metadata.title", "metadata.title")},
        "admin",
    )

    assert list(mongo.last_fields).count("metadata.title") == 1


def test_a_malformed_column_descriptor_is_ignored_not_fatal(mongo, as_admin, monkeypatch):
    """The listing is the archive's highest-traffic read path; a stale value in
    somebody's persisted Redux filters must not 500 it."""
    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    mongo.rows["resources"] = []

    payload, status = services.get_all(
        {
            "post_type": ["x"],
            "activeColumns": [{"label": "no destiny"}, {"destiny": None}, None, 42],
        },
        "admin",
    )

    assert status == 200
    assert payload["total"] == 0


# ---------------------------------------------------------------------------
# Cross-domain helpers
# ---------------------------------------------------------------------------


def test_resource_type_of_a_missing_resource_is_none_not_an_error(mongo):
    """Legacy raised. Callers enriching a list would turn one stale reference
    into a failed page."""
    mongo.records["resources"] = None
    assert services.get_resource_type(VALID_ID) is None


def test_resource_type_of_a_malformed_id_is_none(mongo):
    assert services.get_resource_type("nope") is None


def test_a_type_without_view_roles_is_visible_to_everyone(mongo, as_user):
    mongo.records["post_types"] = {"viewRoles": []}
    assert services.can_view_type("alice", "carpeta") is True


def test_a_type_with_view_roles_requires_one_of_them(mongo, as_user):
    mongo.records["post_types"] = {"viewRoles": ["curator"]}
    assert services.can_view_type("alice", "restricted") is False
