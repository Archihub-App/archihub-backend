"""The public, unauthenticated view of a record.

The rule under test is `access.is_public`: a record is public only when it
restricts nothing itself and every resource it is filed under is *published* and
unrestricted. The published half is  - the legacy public
route checked access rights but never publication state, so a file attached to
an unpublished draft was served anonymously.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.records import access, public, services

RECORD_ID = "6a70b833497d4440325c94b1"
RESOURCE_ID = "6a70b833497d4440325c94c1"
FONDS_ID = "6a70b833497d4440325c94d1"


class FakeMongo:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.resources: dict[str, dict] = {}
        self.types: list[dict] = []

    def get_record(self, collection, filters=None, fields=None):
        key = str((filters or {}).get("_id"))
        if collection == "resources":
            return self.resources.get(key)
        if collection == "post_types":
            slug = (filters or {}).get("slug")
            return next((t for t in self.types if t.get("slug") == slug), None)
        return self.records.get(key)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "post_types":
            return list(self.types)
        if collection == "resources":
            wanted = {str(o) for o in (filters or {}).get("_id", {}).get("$in", [])}
            return [r for k, r in self.resources.items() if k in wanted]
        return []


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    for module in (services, access, public):
        monkeypatch.setattr(module, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.access._mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.hierarchy._mongo", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def no_type_restrictions(monkeypatch):
    """Content types declare no viewRoles unless a test says otherwise."""
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.type_roles",
        lambda slug: {"viewRoles": [], "editRoles": []},
    )


def resource(status="published", access_rights=None, parents=None, post_type="doc"):
    return {
        "_id": ObjectId(RESOURCE_ID),
        "status": status,
        "accessRights": access_rights,
        "parents": parents or [],
        "post_type": post_type,
        "metadata": {"firstLevel": {"title": "A published series"}},
    }


def record(access_rights=None, parents=None):
    return {
        "_id": ObjectId(RECORD_ID),
        "name": "scan.jpg",
        "displayName": "Scan",
        "accessRights": access_rights,
        "parent": parents if parents is not None else [{"id": RESOURCE_ID}],
        "filepath": "2024/03/abc.jpg",
        "processing": {"fileProcessing": {"type": "image", "path": "2024/03/abc"}},
    }


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_record_under_a_published_unrestricted_resource_is_public(mongo):
    mongo.resources[RESOURCE_ID] = resource()

    assert access.is_public(record()) is True


def test_a_record_under_an_unpublished_draft_is_not_public(mongo):
    """The legacy rule never looked at publication state.

    A cataloguer's work in progress, with no access right set because none has
    been chosen yet, was downloadable by anyone who knew the record id.
    """
    mongo.resources[RESOURCE_ID] = resource(status="draft")

    assert access.is_public(record()) is False


def test_a_record_under_a_deleted_resource_is_not_public(mongo):
    mongo.resources[RESOURCE_ID] = resource(status="deleted")

    assert access.is_public(record()) is False


def test_a_record_with_its_own_access_right_is_not_public(mongo):
    mongo.resources[RESOURCE_ID] = resource()

    assert access.is_public(record(access_rights="restricted")) is False


def test_a_record_under_a_restricted_resource_is_not_public(mongo):
    mongo.resources[RESOURCE_ID] = resource(access_rights="restricted")

    assert access.is_public(record()) is False


def test_a_restriction_inherited_from_a_fonds_makes_it_not_public(mongo):
    """Restricting a fonds withdraws every file beneath it from the public site."""
    mongo.resources[FONDS_ID] = {
        "_id": ObjectId(FONDS_ID),
        "status": "published",
        "accessRights": "restricted",
        "parents": [],
        "post_type": "fonds",
    }
    mongo.resources[RESOURCE_ID] = resource(parents=[{"id": FONDS_ID}])

    assert access.is_public(record()) is False


def test_a_type_with_view_roles_is_not_public(mongo, monkeypatch):
    """A type restricted to a role cannot be visible to someone holding none."""
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.type_roles",
        lambda slug: {"viewRoles": ["editor"], "editRoles": []},
    )
    mongo.resources[RESOURCE_ID] = resource()

    assert access.is_public(record()) is False


def test_one_non_public_parent_is_enough_to_withhold_it(mongo):
    """A file reachable from a reserved series is reserved, however it was reached."""
    other = "6a70b833497d4440325c94e1"
    mongo.resources[RESOURCE_ID] = resource()
    mongo.resources[other] = {
        "_id": ObjectId(other),
        "status": "draft",
        "accessRights": None,
        "parents": [],
        "post_type": "doc",
    }

    assert access.is_public(record(parents=[{"id": RESOURCE_ID}, {"id": other}])) is False


def test_a_record_filed_nowhere_is_not_public(mongo):
    """Narrower than the legacy rule, deliberately.

    An orphan is reachable through no public resource, so nothing publishes it.
    The legacy loop simply had nothing to iterate and returned the record.
    """
    assert access.is_public(record(parents=[])) is False


def test_a_dangling_parent_reference_does_not_publish_a_record(mongo):
    """Unlike the authenticated rule, where a dangling parent cannot deny access.

    The two differ on purpose: authenticated readers already hold rights, so a
    stale reference must not lock them out; anonymously, an unresolvable parent
    is simply not evidence that anything was published.
    """
    assert access.is_public(record(parents=[{"id": RESOURCE_ID}])) is False


# ---------------------------------------------------------------------------
# What the routes return
# ---------------------------------------------------------------------------


def test_a_non_public_record_is_a_404_not_a_401(mongo):
    """A public endpoint must never confirm that a record exists."""
    mongo.resources[RESOURCE_ID] = resource(status="draft")
    mongo.records[RECORD_ID] = record()

    payload, status = public.get_by_id(RECORD_ID)

    assert status == 404
    assert payload == {"msg": "Record does not exist"}


def test_a_missing_record_returns_the_identical_response(mongo):
    missing, missing_status = public.get_by_id("000000000000000000000000")
    mongo.resources[RESOURCE_ID] = resource(status="draft")
    mongo.records[RECORD_ID] = record()
    refused, refused_status = public.get_by_id(RECORD_ID)

    assert (missing, missing_status) == (refused, refused_status)


def test_a_public_record_omits_its_filepath(mongo):
    mongo.resources[RESOURCE_ID] = resource()
    mongo.records[RECORD_ID] = record()

    payload, status = public.get_by_id(RECORD_ID)

    assert status == 200
    assert "filepath" not in payload


def test_a_public_record_summarises_its_processing(mongo):
    """Raw plugin output does not leave the authenticated API; less so this one."""
    mongo.resources[RESOURCE_ID] = resource()
    full = record()
    full["processing"]["fileProcessing"]["metadata"] = {
        "Make": "Canon",
        "GPSLatitude": 4.6,
        "OwnerName": "someone",
    }
    mongo.records[RECORD_ID] = full

    payload, _status = public.get_by_id(RECORD_ID)

    assert payload["processing"]["fileProcessing"]["metadata"] == {"Make": "Canon"}


def test_a_malformed_id_is_a_404(mongo):
    payload, status = public.get_by_id("not-an-object-id")

    assert status == 404


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------


def test_the_public_routes_are_not_shadowed_by_the_parameterised_ones():
    """`/records/public/{id}` must not be captured by `/records/{record_id}`.

    A shadowed public route does not error - it authenticates, so an anonymous
    caller gets 401 where the contract promises a document. The app factory
    asserts this at startup; this pins the assertion itself.
    """
    from archihub.core.app_factory import _would_capture

    assert _would_capture("/records/{record_id}", "/records/public") is True
    assert _would_capture("/records/{record_id}/pages", "/records/public/x/pages") is False
    assert _would_capture("/records/{record_id}/stream", "/records/public/stream") is True
    assert _would_capture("/records/{record_id}", "/records/a/b") is False
