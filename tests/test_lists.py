"""Controlled-vocabulary domain.

Lists are addressed by id (confirmed with the maintainer). The slug-based lookup
that existed in the legacy service is dead code and is not ported - `lists`
documents carry no slug field, so it could never match.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.lists import services


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.collections: dict[str, list] = {}
        self.inserted: list = []
        self.updated: list = []
        self.deleted: list = []
        self._next_id = 1

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        rows = self.collections.get(collection, [])
        if filters and "_id" in filters and "$in" in filters["_id"]:
            wanted = {str(o) for o in filters["_id"]["$in"]}
            rows = [r for r in rows if str(r["_id"]) in wanted]
        return list(rows)

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))
        oid = ObjectId(f"{self._next_id:024d}")
        self._next_id += 1
        return FakeInsertResult(oid)

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))

    def delete_record(self, collection, filters):
        self.deleted.append((collection, filters))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    monkeypatch.setattr(services, "_invalidate_role_caches", lambda *a, **k: None)
    return fake


VALID_ID = "6a70b8c3497d4440325c94c3"


# ---------------------------------------------------------------------------
# get_by_id - the error-shape fix
# ---------------------------------------------------------------------------


def test_missing_list_is_404(mongo):
    """Legacy returned HTTP 200 whose body was the array [{"msg": ...}, 404].

    The service returned a tuple to a route testing `if 'msg' in resp`;
    membership in a tuple is not key lookup, so the check never fired and the
    route fell through to `return jsonify(resp), 200`. `ListsService.getList`
    treats any 200 as success, so the component received an array where it
    expected {name, description, options}.
    """
    mongo.records["lists"] = None

    payload, status = services.get_by_id(VALID_ID)

    assert status == 404
    assert set(payload) == {"msg"}


def test_malformed_id_is_404_not_500(mongo):
    """A bad id in the URL is a client error.

    Legacy called ObjectId(id) directly, so `InvalidId` surfaced as a 500
    carrying the bson error text.
    """
    payload, status = services.get_by_id("not-an-object-id")
    assert status == 404


def test_success_shape_matches_legacy(mongo):
    mongo.records["lists"] = {
        "_id": ObjectId(VALID_ID),
        "name": "Test",
        "description": "d",
        "options": [],
    }
    payload, status = services.get_by_id(VALID_ID)

    assert status == 200
    assert set(payload) == {"name", "description", "options"}


def test_missing_description_defaults_to_empty_string(mongo):
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "Test", "options": []}
    payload, _status = services.get_by_id(VALID_ID)
    assert payload["description"] == ""


def test_options_keep_the_lists_own_order(mongo):
    """Order is meaningful - it is the presentation order.

    MongoDB does not return `$in` matches in the order of the argument, so the
    list's own id array drives the ordering.
    """
    ids = ["600000000000000000000003", "600000000000000000000001", "600000000000000000000002"]
    mongo.records["lists"] = {
        "_id": ObjectId(VALID_ID), "name": "L", "description": "", "options": ids
    }
    # Deliberately returned in a different order than requested.
    mongo.collections["options"] = [
        {"_id": ObjectId(ids[1]), "term": "second"},
        {"_id": ObjectId(ids[2]), "term": "third"},
        {"_id": ObjectId(ids[0]), "term": "first"},
    ]

    payload, _status = services.get_by_id(VALID_ID)
    assert [o["term"] for o in payload["options"]] == ["first", "second", "third"]


def test_dangling_option_ids_are_skipped(mongo):
    """A deleted option must not break the whole list."""
    mongo.records["lists"] = {
        "_id": ObjectId(VALID_ID),
        "name": "L",
        "description": "",
        "options": ["600000000000000000000001", "600000000000000000000009"],
    }
    mongo.collections["options"] = [{"_id": ObjectId("600000000000000000000001"), "term": "kept"}]

    payload, _status = services.get_by_id(VALID_ID)
    assert [o["term"] for o in payload["options"]] == ["kept"]


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_rename_without_options_succeeds(mongo):
    """The headline update bug.

    The legacy service wrapped its entire body in `if 'options' in body:`, so a
    patch that only renamed a list fell off the end and returned None - which
    Flask cannot turn into a response.
    """
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "Old"}

    payload, status = services.update_by_id(VALID_ID, {"name": "New"}, "admin")

    assert status == 200
    _collection, _filters, update = mongo.updated[0]
    assert update == {"name": "New"}


def test_update_leaves_options_alone_when_not_supplied(mongo):
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L", "options": ["x"]}

    services.update_by_id(VALID_ID, {"name": "N"}, "admin")

    _collection, _filters, update = mongo.updated[0]
    assert "options" not in update


def test_new_options_are_created_and_referenced(mongo):
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L"}

    services.update_by_id(VALID_ID, {"options": [{"term": "Nuevo"}]}, "admin")

    assert ("options", {"term": "Nuevo"}) in mongo.inserted
    _collection, _filters, update = mongo.updated[-1]
    assert len(update["options"]) == 1


def test_existing_options_are_updated_in_place(mongo):
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L"}
    option_id = "600000000000000000000001"

    services.update_by_id(
        VALID_ID, {"options": [{"id": option_id, "term": "Renamed"}]}, "admin"
    )

    assert any(
        collection == "options" and update == {"term": "Renamed"}
        for collection, _filters, update in mongo.updated
    )


def test_deleted_options_are_dropped_from_the_list(mongo):
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L"}
    keep, drop = "600000000000000000000001", "600000000000000000000002"

    services.update_by_id(
        VALID_ID,
        {"options": [{"id": keep, "term": "keep"}, {"id": drop, "term": "drop", "deleted": True}]},
        "admin",
    )

    _collection, _filters, update = mongo.updated[-1]
    assert update["options"] == [keep]


def test_update_missing_list_is_404(mongo):
    mongo.records["lists"] = None
    _payload, status = services.update_by_id(VALID_ID, {"name": "x"}, "admin")
    assert status == 404


def test_empty_patch_does_not_write_an_empty_update(mongo):
    """MongoDB rejects an empty $set."""
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L"}

    _payload, status = services.update_by_id(VALID_ID, {}, "admin")

    assert status == 200
    assert mongo.updated == []


# ---------------------------------------------------------------------------
# create / delete
# ---------------------------------------------------------------------------


def test_create_stores_options_separately(mongo):
    payload, status = services.create(
        {"name": "L", "description": "d", "options": [{"term": "a"}, {"term": "b"}]}, "admin"
    )

    assert status == 201
    option_inserts = [r for c, r in mongo.inserted if c == "options"]
    assert option_inserts == [{"term": "a"}, {"term": "b"}]

    list_insert = next(r for c, r in mongo.inserted if c == "lists")
    assert len(list_insert["options"]) == 2
    assert "_id" not in list_insert  # MongoDB assigns it


def test_delete_missing_list_is_404_and_translated(mongo):
    """Legacy returned a hardcoded Spanish string here while every sibling path
    used the translated message."""
    mongo.records["lists"] = None
    payload, status = services.delete_by_id(VALID_ID, "admin")

    assert status == 404
    assert payload["msg"] != "Listado no existe"


def test_delete_audit_id_is_serialisable(mongo):
    """Legacy passed the raw ObjectId into the audit record."""
    captured = {}
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L"}
    services._register_log = lambda user, action, metadata: captured.update(metadata)

    services.delete_by_id(VALID_ID, "admin")

    assert isinstance(captured["list"]["id"], str)


def test_slug_lookup_is_not_ported():
    """`lists` documents have no slug; the legacy lookup could never match."""
    assert not hasattr(services, "get_by_slug")
