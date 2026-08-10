"""User listing, profile and favourites.

The favourites routes take a ``type`` that selects which MongoDB collection is
read. These tests pin that it is a fixed enumeration, checked in two independent
places, so a caller cannot choose what the server discloses.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.users import services


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.collections: dict[str, list] = {}
        self.queried: list = []
        self.operators: list = []

    def get_record(self, collection, filters, fields=None):
        self.queried.append((collection, filters))
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        self.queried.append((collection, filters))
        return list(self.collections.get(collection, []))

    def count(self, collection, filters=None):
        return len(self.collections.get(collection, []))

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.operators.append((collection, filters, operator))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)

    # `get_all` resolves role and access-right ids to display terms through
    # `core.roles`, which reads the `system` collection with its own `_mongo`.
    # Without these the test reaches a real database - it passed for a while
    # only because one happened to be running, which is exactly what
    # conftest.py says must never be true. Individual tests override them.
    monkeypatch.setattr("archihub.core.roles._mongo", lambda: fake)
    monkeypatch.setattr("archihub.core.roles.get_roles", lambda: {"options": []})
    monkeypatch.setattr("archihub.core.roles.get_access_rights", lambda: {"options": []})
    return fake


VALID_ID = "6a70b8c3497d4440325c94c3"


# ---------------------------------------------------------------------------
# The collection allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_type", ["users", "system", "logs", "tasks", "", None, "resources ", "Resources"])
def test_only_allowlisted_collections_can_be_read(mongo, bad_type):
    """`type` names a collection, so it must be a fixed set - not free text.

    Checked in the service as well as the request schema, because these
    functions are importable and a future caller may not come through a route.
    """
    payload, status = services.get_favorites("alice", {"type": bad_type, "page": 0})
    assert status == 400
    assert mongo.queried == [], "a rejected type must not reach a query"


@pytest.mark.parametrize("good_type", ["resources", "records", "snaps"])
def test_allowlisted_collections_are_accepted(mongo, good_type):
    mongo.records["users"] = {"favorites": []}
    _payload, status = services.get_favorites("alice", {"type": good_type, "page": 0})
    assert status == 200


def test_setting_a_favorite_rejects_an_unlisted_collection(mongo):
    payload, status = services.set_favorite("alice", {"type": "users", "id": VALID_ID})
    assert status == 400
    assert mongo.queried == []
    assert mongo.operators == []


def test_service_and_schema_allowlists_agree():
    """Two checks, one source of truth - they must not drift apart."""
    from typing import get_args

    from archihub.api.users.schemas import FavoriteType

    assert set(get_args(FavoriteType)) == services.FAVORITE_COLLECTIONS


# ---------------------------------------------------------------------------
# Favourites behaviour
# ---------------------------------------------------------------------------


def test_only_published_resources_can_be_favorited(mongo):
    mongo.records["resources"] = {"_id": ObjectId(VALID_ID), "status": "draft"}
    _payload, status = services.set_favorite("alice", {"type": "resources", "id": VALID_ID})
    assert status == 400


def test_collections_without_a_status_field_are_not_rejected(mongo):
    """Only `resources` carries a status.

    The legacy code subscripted `fav['status']` before checking the type, so a
    record or snap - which have no such field - raised KeyError.
    """
    mongo.records["records"] = {"_id": ObjectId(VALID_ID)}
    _payload, status = services.set_favorite("alice", {"type": "records", "id": VALID_ID})
    assert status == 200


def test_malformed_id_is_not_found_rather_than_an_error(mongo):
    _payload, status = services.set_favorite("alice", {"type": "records", "id": "not-an-id"})
    assert status == 404


def test_absent_target_is_404(mongo):
    mongo.records["records"] = None
    _payload, status = services.set_favorite("alice", {"type": "records", "id": VALID_ID})
    assert status == 404


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filters",
    [
        {"password": {"$ne": None}},          # operator injection
        {"$where": "1==1"},
        {"roles": "admin"},                    # not an allowed field
        {"username": {"$regex": ".*"}},        # allowed field, disallowed shape
        "not-a-dict",
    ],
)
def test_filters_are_reduced_to_an_allowlist(filters):
    """Only string equality on allowlisted fields survives."""
    cleaned = services.sanitize_user_filters(filters)
    assert set(cleaned) <= services.ALLOWED_USER_FILTER_FIELDS
    assert all(isinstance(v, str) for v in cleaned.values())


def test_allowed_string_filters_survive():
    assert services.sanitize_user_filters({"username": "alice", "name": "Alice"}) == {
        "username": "alice",
        "name": "Alice",
    }


def test_listing_never_returns_credentials(mongo):
    """Credentials and API keys must not appear in a listing."""
    mongo.collections["users"] = [
        {"_id": ObjectId(VALID_ID), "username": "alice", "roles": [], "accessRights": []}
    ]
    users, status = services.get_all({"page": 0, "filters": {}}, "admin")

    assert status == 200
    for field in ("password", "token", "adminToken", "nodeToken", "vizToken"):
        assert field not in users[0]


def test_unknown_role_ids_are_dropped_not_shown_raw(mongo, monkeypatch):
    """A stale id is not a display term and would render as noise."""
    monkeypatch.setattr(
        "archihub.core.roles.get_roles", lambda: {"options": [{"id": "admin", "term": "Administrador"}]}
    )
    monkeypatch.setattr("archihub.core.roles.get_access_rights", lambda: {"options": []})
    mongo.collections["users"] = [
        {"_id": ObjectId(VALID_ID), "username": "a", "roles": ["admin", "deleted-role"], "accessRights": []}
    ]

    users, _status = services.get_all({}, "admin")
    assert users[0]["roles"] == ["Administrador"]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_profile_omits_the_password_hash(mongo):
    mongo.records["users"] = {"_id": ObjectId(VALID_ID), "username": "alice", "password": "hash"}
    profile, status = services.get_profile("alice")

    assert status == 200
    assert "password" not in profile


def test_absent_profile_is_400_not_500(mongo):
    """Legacy popped `password` on the line above its own existence check, so an
    absent account raised AttributeError and surfaced as a 500."""
    mongo.records["users"] = None
    _payload, status = services.get_profile("ghost")
    assert status == 400
