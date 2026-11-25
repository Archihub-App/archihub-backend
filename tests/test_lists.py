"""Controlled-vocabulary domain.

Lists are addressed by id: `lists` documents carry no slug field.
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
    # No authorisation vocabularies unless a test names one; the real lookup
    # reads the `system` collection.
    monkeypatch.setattr("archihub.core.roles.get_roles_id", lambda: None)
    monkeypatch.setattr("archihub.core.roles.get_access_rights_id", lambda: None)
    return fake


VALID_ID = "6a70b8c3497d4440325c94c3"


# ---------------------------------------------------------------------------
# get_by_id - the error-shape fix
# ---------------------------------------------------------------------------


def test_missing_list_is_404(mongo):
    """A real 404 with a `msg`, not a 200 carrying an error.

    `ListsService.getList` treats any 200 as success.
    """
    mongo.records["lists"] = None

    payload, status = services.get_by_id(VALID_ID)

    assert status == 404
    assert set(payload) == {"msg"}


def test_malformed_id_is_404_not_500(mongo):
    """A bad id in the URL is a client error.

    It is never a 500 carrying bson's error text.
    """
    payload, status = services.get_by_id("not-an-object-id")
    assert status == 404


def test_success_shape_is_name_description_options(mongo):
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
    """A patch may only rename a list.
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
    option_id = "600000000000000000000001"
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L", "options": [option_id]}

    services.update_by_id(
        VALID_ID, {"options": [{"id": option_id, "term": "Renamed"}]}, "admin"
    )

    assert any(
        collection == "options" and update == {"term": "Renamed"}
        for collection, _filters, update in mongo.updated
    )


def test_deleted_options_are_dropped_from_the_list(mongo):
    keep, drop = "600000000000000000000001", "600000000000000000000002"
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L", "options": [keep, drop]}

    services.update_by_id(
        VALID_ID,
        {"options": [{"id": keep, "term": "keep"}, {"id": drop, "term": "drop", "deleted": True}]},
        "admin",
    )

    _collection, _filters, update = mongo.updated[-1]
    assert update["options"] == [keep]


@pytest.mark.parametrize("deleted", [False, True])
def test_an_option_of_another_list_cannot_be_touched(mongo, deleted):
    """An update changes only the edited list's own options."""
    own, foreign = "600000000000000000000001", "600000000000000000000009"
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L", "options": [own]}

    payload, status = services.update_by_id(
        VALID_ID,
        {"name": "N", "options": [{"id": own, "term": "a"},
                                  {"id": foreign, "term": "admin", "deleted": deleted}]},
        "editor",
    )

    assert status == 400
    assert foreign in payload["msg"]
    assert mongo.updated == []
    assert mongo.inserted == []


ROLES_LIST = "6a70b8c3497d4440325c9400"
SHARED = "600000000000000000000005"


@pytest.fixture
def shared_role_option(mongo, monkeypatch):
    """A list that shares an option with the roles vocabulary, labelled ``curator``."""
    monkeypatch.setattr("archihub.core.roles.get_roles_id", lambda: ROLES_LIST)
    lists = {
        VALID_ID: {"_id": ObjectId(VALID_ID), "name": "L", "options": [SHARED]},
        ROLES_LIST: {"_id": ObjectId(ROLES_LIST), "name": "Roles", "options": [SHARED]},
    }
    mongo.records["lists"] = lambda filters: lists.get(str(filters["_id"]))
    mongo.collections["options"] = [{"_id": ObjectId(SHARED), "term": "curator"}]
    return mongo


@pytest.fixture
def builtin_manager(monkeypatch):
    held: set[str] = set()
    monkeypatch.setattr(
        "archihub.api.users.services.has_role", lambda username, role: role in held
    )
    return held


def test_a_shared_role_option_cannot_be_relabelled_by_an_added_role(
    shared_role_option, builtin_manager
):
    builtin_manager.add("curator")
    payload, status = services.update_by_id(
        VALID_ID, {"options": [{"id": SHARED, "term": "admin"}]}, "someone"
    )
    assert status == 403
    assert shared_role_option.updated == []


@pytest.mark.parametrize(
    "options",
    [
        [{"id": SHARED, "term": "admin"}, {"id": SHARED, "term": "curator"}],
        [{"id": SHARED, "term": "curator"}, {"id": SHARED, "term": "curator"}],
        [{"id": SHARED, "term": "admin"}, {"id": SHARED, "deleted": True}],
    ],
)
def test_an_option_named_twice_is_refused(shared_role_option, builtin_manager, options):
    builtin_manager.add("editor")
    _payload, status = services.update_by_id(VALID_ID, {"options": options}, "someone")
    assert status == 400
    assert shared_role_option.updated == []
    assert shared_role_option.inserted == []


def test_a_shared_role_option_may_keep_its_label_or_be_dropped(
    shared_role_option, builtin_manager
):
    _payload, status = services.update_by_id(
        VALID_ID, {"options": [{"id": SHARED, "term": "curator"}]}, "someone"
    )
    assert status == 200
    _payload, status = services.update_by_id(
        VALID_ID, {"options": [{"id": SHARED, "term": "x", "deleted": True}]}, "someone"
    )
    assert status == 200


def test_a_built_in_list_manager_may_relabel_a_shared_option(shared_role_option, builtin_manager):
    builtin_manager.add("editor")
    _payload, status = services.update_by_id(
        VALID_ID, {"options": [{"id": SHARED, "term": "Curador"}]}, "someone"
    )
    assert status == 200


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
    # The caller learns the id it assigned.
    assert payload["id"]


def test_delete_missing_list_is_404_and_translated(mongo):
    """The translated message, like every sibling path."""
    mongo.records["lists"] = None
    payload, status = services.delete_by_id(VALID_ID, "admin")

    assert status == 404
    assert payload["msg"] != "Listado no existe"


def test_delete_audit_id_is_serialisable(mongo):
    """The audit record carries the id as a string."""
    captured = {}
    mongo.records["lists"] = {"_id": ObjectId(VALID_ID), "name": "L"}
    services._register_log = lambda user, action, metadata: captured.update(metadata)

    services.delete_by_id(VALID_ID, "admin")

    assert isinstance(captured["list"]["id"], str)


def test_slug_lookup_is_not_ported():
    """`lists` documents have no slug."""
    assert not hasattr(services, "get_by_slug")


# ---------------------------------------------------------------------------
# get_all - preview options, option counts, and form usage
# ---------------------------------------------------------------------------


def test_get_all_returns_first_three_options_total_count_and_forms(mongo):
    opt_ids = [f"60000000000000000000000{i}" for i in range(1, 6)]
    list_id = ObjectId(VALID_ID)
    mongo.collections["lists"] = [
        {
            "_id": list_id,
            "name": "Languages",
            "options": [opt_ids[3], opt_ids[0], opt_ids[2], opt_ids[1], opt_ids[4]],
            "forms": [{"slug": "isadg", "name": "ISAD-G"}],
        }
    ]
    mongo.collections["options"] = [
        {"_id": ObjectId(opt_ids[i]), "term": f"Term {i + 1}"} for i in range(5)
    ]

    payload, status = services.get_all()

    assert status == 200
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == VALID_ID
    assert item["name"] == "Languages"
    assert item["optionsCount"] == 5
    assert item["options"] == [
        {"id": opt_ids[3], "term": "Term 4"},
        {"id": opt_ids[0], "term": "Term 1"},
        {"id": opt_ids[2], "term": "Term 3"},
    ]
    assert item["forms"] == [{"slug": "isadg", "name": "ISAD-G"}]
    assert mongo.updated == []


def test_get_all_skips_dangling_preview_options_and_handles_empty_lists(mongo):
    opt_ids = [f"60000000000000000000000{i}" for i in range(1, 5)]
    missing_id = "600000000000000000000099"
    first_id = ObjectId("6a70b8c3497d4440325c94c1")
    second_id = ObjectId("6a70b8c3497d4440325c94c2")

    mongo.collections["lists"] = [
        {
            "_id": first_id,
            "name": "Empty",
            "options": [],
            "forms": [],
        },
        {
            "_id": second_id,
            "name": "With Dangling",
            "options": [missing_id, opt_ids[0], opt_ids[1], opt_ids[2], opt_ids[3]],
            "forms": [],
        },
    ]
    mongo.collections["options"] = [
        {"_id": ObjectId(opt_ids[i]), "term": f"T{i + 1}"} for i in range(4)
    ]

    payload, status = services.get_all()

    assert status == 200
    assert payload[0]["options"] == []
    assert payload[0]["optionsCount"] == 0
    assert payload[1]["options"] == [
        {"id": opt_ids[0], "term": "T1"},
        {"id": opt_ids[1], "term": "T2"},
        {"id": opt_ids[2], "term": "T3"},
    ]
    assert payload[1]["optionsCount"] == 5


def test_get_all_backfills_forms_lazily_when_missing(mongo):
    list_id = ObjectId(VALID_ID)
    other_id = ObjectId("6a70b8c3497d4440325c94c4")
    mongo.collections["lists"] = [
        {"_id": list_id, "name": "Used", "options": []},
        {"_id": other_id, "name": "Unused", "options": []},
    ]
    mongo.collections["forms"] = [
        {
            "slug": "dublin-core",
            "name": "Dublin Core",
            "fields": [
                {"label": "Language", "type": "select", "list": VALID_ID},
                {
                    "label": "Group",
                    "type": "repeater",
                    "subfields": [{"label": "Sub", "type": "select", "list": VALID_ID}],
                },
            ],
        }
    ]

    payload, status = services.get_all()

    assert status == 200
    by_id = {entry["id"]: entry for entry in payload}
    assert by_id[VALID_ID]["forms"] == [{"slug": "dublin-core", "name": "Dublin Core"}]
    assert by_id[str(other_id)]["forms"] == []
    assert ("lists", {"_id": list_id}, {"forms": [{"slug": "dublin-core", "name": "Dublin Core"}]}) in mongo.updated
    assert ("lists", {"_id": other_id}, {"forms": []}) in mongo.updated


def test_sync_all_forms_usage_is_idempotent(mongo):
    list_id = ObjectId(VALID_ID)
    expected_forms = [{"slug": "isadg", "name": "ISAD-G"}]
    mongo.collections["lists"] = [
        {"_id": list_id, "name": "L", "options": [], "forms": expected_forms},
    ]
    mongo.collections["forms"] = [
        {
            "slug": "isadg",
            "name": "ISAD-G",
            "fields": [{"label": "Type", "type": "select-multiple2", "list": VALID_ID}],
        }
    ]

    services.sync_all_forms_usage()

    assert mongo.updated == []


def test_sync_form_usage_adds_renames_and_removes_without_touching_unrelated(mongo):
    kept_id = ObjectId("6a70b8c3497d4440325c94c1")
    added_id = ObjectId("6a70b8c3497d4440325c94c2")
    removed_id = ObjectId("6a70b8c3497d4440325c94c3")
    unrelated_id = ObjectId("6a70b8c3497d4440325c94c4")

    mongo.collections["lists"] = [
        {"_id": kept_id, "name": "Kept", "forms": [{"slug": "form-a", "name": "Old Name"}]},
        {"_id": added_id, "name": "Added", "forms": []},
        {"_id": removed_id, "name": "Removed", "forms": [{"slug": "form-a", "name": "Old Name"}]},
        {"_id": unrelated_id, "name": "Unrelated", "forms": [{"slug": "other", "name": "Other"}]},
    ]

    services.sync_form_usage("form-a", "New Name", [str(kept_id), str(added_id)])

    assert (
        "lists",
        {"_id": kept_id},
        {"forms": [{"slug": "form-a", "name": "New Name"}]},
    ) in mongo.updated
    assert (
        "lists",
        {"_id": added_id},
        {"forms": [{"slug": "form-a", "name": "New Name"}]},
    ) in mongo.updated
    assert ("lists", {"_id": removed_id}, {"forms": []}) in mongo.updated
    assert all(filters != {"_id": unrelated_id} for _c, filters, _u in mongo.updated)


def test_create_initializes_empty_forms_list(mongo):
    _payload, status = services.create({"name": "Fresh", "options": []}, "admin")

    assert status == 201
    list_insert = next(r for c, r in mongo.inserted if c == "lists")
    assert list_insert["forms"] == []
