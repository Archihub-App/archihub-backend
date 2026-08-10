"""Creating, updating, deleting and restoring resources.

The last piece of the domain, and mostly about ordering: what is validated
before what is written, and what happens to a batch when permission fails
partway through it.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.resources import write

RESOURCE_ID = "6a70b833497d4440325c94b1"
CHILD_ID = "6a70b833497d4440325c94b2"
RECORD_ID = "6a70b833497d4440325c94c1"


class Inserted:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeMongo:
    def __init__(self):
        self.resources: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.types: dict[str, dict] = {}
        self.user: dict | None = None
        self.writes: list[tuple[str, dict, dict]] = []
        self.inserted: list[dict] = []

    def get_record(self, collection, filters=None, fields=None):
        key = str((filters or {}).get("_id"))
        if collection == "post_types":
            return self.types.get((filters or {}).get("slug"))
        if collection == "users":
            return self.user
        if collection == "records":
            return self.records.get(key)
        return self.resources.get(key)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "post_types":
            return list(self.types.values())
        if collection != "resources":
            return []
        parent_id = (filters or {}).get("parent.id")
        if parent_id is not None:
            return [
                r for r in self.resources.values()
                if any(p.get("id") == parent_id for p in (r.get("parent") or []))
            ]
        ids = (filters or {}).get("_id", {})
        if isinstance(ids, dict) and "$in" in ids:
            wanted = {str(o) for o in ids["$in"]}
            return [r for k, r in self.resources.items() if k in wanted]
        return list(self.resources.values())

    def insert_record(self, collection, record):
        new_id = f"new{len(self.inserted) + 1}"
        self.inserted.append({**record, "_id": new_id})
        self.resources[new_id] = self.inserted[-1]
        return Inserted(new_id)

    def delete_record(self, collection, filters):
        self.resources.pop(str(filters.get("_id")), None)

    def update_record(self, collection, filters, update_model):
        key = str(filters.get("_id"))
        self.writes.append((collection, filters, update_model))
        target = self.records if collection == "records" else self.resources
        if key not in target:
            return
        for field, value in update_model.items():
            # Mongo's `$set` interprets a dotted key as a path into the
            # document; the reciprocal-relation writes depend on that.
            _set_path(target[key], field, value)


def _set_path(document: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    current = document
    for key in keys[:-1]:
        nxt = current.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            current[key] = nxt
        current = nxt
    current[keys[-1]] = value


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    fake.types["carpeta"] = {
        "slug": "carpeta", "hierarchical": True, "parentType": [],
        "editRoles": [], "viewRoles": [],
    }
    for module in (
        write, write.access, write.hierarchy, write.validation,
        "archihub.api.records.storage",
        # `hierarchy.validate_parent` asks `types.services.is_hierarchical`,
        # which holds its own `_mongo`. Missing it sent the parent check to a
        # real database.
        "archihub.api.types.services",
    ):
        if isinstance(module, str):
            monkeypatch.setattr(module + "._mongo", lambda: fake)
        else:
            monkeypatch.setattr(module, "_mongo", lambda: fake)

    monkeypatch.setattr(write, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(write, "_call_hook", lambda name, payload: payload)
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: {
            "fields": [
                {"destiny": "metadata.firstLevel.title", "type": "text", "label": "Title",
                 "required": True},
            ]
        },
    )
    monkeypatch.setattr("archihub.core.roles.get_access_rights", lambda: {"options": []})
    fake.user = {"accessRights": []}
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


def with_roles(monkeypatch, *roles):
    held = set(roles)
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r in held)


def _incoming_file(tag: str = "file"):
    from archihub.api.records.storage import IncomingFile

    return IncomingFile(filename="a.txt", stream=None, tag=tag)


def body(**overrides):
    return {
        "post_type": "carpeta",
        "metadata": {"firstLevel": {"title": "Expediente"}},
        "status": "draft",
        **overrides,
    }


def resource(**overrides):
    return {
        "_id": ObjectId(RESOURCE_ID),
        "post_type": "carpeta",
        "createdBy": "alice",
        "status": "draft",
        "accessRights": None,
        "parent": [],
        "parents": [],
        "filesObj": [],
        "metadata": {"firstLevel": {"title": "Expediente"}},
        **overrides,
    }


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_a_resource_is_created(mongo):
    payload, status = write.create(body(), "alice")

    assert status == 201
    assert payload["id"] == "new1"
    assert mongo.inserted[0]["createdBy"] == "alice"


def test_the_content_type_is_required(mongo):
    _payload, status = write.create({"metadata": {}}, "alice")
    assert status == 400


def test_the_metadata_is_required(mongo):
    _payload, status = write.create({"post_type": "carpeta"}, "alice")
    assert status == 400


def test_a_draft_may_be_incomplete(mongo):
    _payload, status = write.create(body(metadata={}), "alice")
    assert status == 201


def test_publishing_an_incomplete_resource_is_refused(mongo, monkeypatch):
    with_roles(monkeypatch, "publisher")
    payload, status = write.create(body(status="published", metadata={}), "alice")

    assert status == 400
    assert "metadata.firstLevel.title" in payload["errors"]


def test_publishing_requires_the_publisher_role(mongo):
    _payload, status = write.create(body(status="published"), "alice")
    assert status == write.LEGACY_ROLE_FAILURE_STATUS


def test_an_admin_may_publish(mongo, monkeypatch):
    with_roles(monkeypatch, "admin")
    _payload, status = write.create(body(status="published"), "admin")
    assert status == 201


def test_creating_requires_the_content_types_edit_role(mongo):
    mongo.types["carpeta"]["editRoles"] = ["curator"]
    _payload, status = write.create(body(), "alice")
    assert status == write.LEGACY_ROLE_FAILURE_STATUS


def test_server_owned_fields_cannot_be_set_by_the_client(mongo):
    """`createdBy` records who made it; `favCount` belongs to the favourites
    routes; `filesObj` to the file pipeline."""
    write.create(
        body(createdBy="someone-else", favCount=999, filesObj=[{"id": "x"}]), "alice"
    )
    created = mongo.inserted[0]

    assert created["createdBy"] == "alice"
    assert created["favCount"] == 0
    assert created["filesObj"] == []


def test_an_absent_status_defaults_to_draft(mongo):
    write.create({"post_type": "carpeta", "metadata": {"firstLevel": {"title": "x"}}}, "alice")
    assert mongo.inserted[0]["status"] == write.STATUS_DRAFT


def test_a_files_parent_is_the_real_resource_id(mongo, monkeypatch):
    """Found by a live smoke test, not by these tests.

    Files were attached before the resource was inserted, so every record was
    filed under a placeholder id and nothing could ever find it again - which
    surfaced as deletion failing to retire a resource's files.
    """
    captured = {}

    def fake_attach(resource_id, incoming, user, resource=None):
        captured["resource_id"] = resource_id
        return [{"id": "rec1", "tag": "file"}]

    monkeypatch.setattr("archihub.api.records.storage.attach_files", fake_attach)

    payload, _status = write.create(body(), "alice", [_incoming_file()])

    assert captured["resource_id"] == payload["id"]


def test_a_rejected_upload_leaves_no_resource_behind(mongo, monkeypatch):
    """The user was told the save failed; an empty resource must not survive it."""
    from archihub.core.files import UploadTooLarge

    def explode(*a, **k):
        raise UploadTooLarge(10)

    monkeypatch.setattr("archihub.api.records.storage.attach_files", explode)

    with pytest.raises(UploadTooLarge):
        write.create(body(), "alice", [_incoming_file()])

    assert mongo.resources == {}


def test_the_ancestry_is_derived_not_accepted(mongo):
    """`parents` is computed from `parent`; a client cannot assert it."""
    write.create(body(parents=[{"id": "made-up"}]), "alice")
    assert mongo.inserted[0]["parents"] == []


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_a_resource_is_updated(mongo):
    mongo.resources[RESOURCE_ID] = resource()

    _payload, status = write.update(
        RESOURCE_ID, body(metadata={"firstLevel": {"title": "Nuevo"}}), "alice"
    )

    assert status == 200
    assert mongo.resources[RESOURCE_ID]["metadata"]["firstLevel"]["title"] == "Nuevo"


def test_updating_a_missing_resource_is_404(mongo):
    _payload, status = write.update(RESOURCE_ID, body(), "alice")
    assert status == 404


def test_a_stranger_cannot_update(mongo):
    mongo.resources[RESOURCE_ID] = resource(createdBy="owner")
    _payload, status = write.update(RESOURCE_ID, body(), "alice")
    assert status == write.LEGACY_ROLE_FAILURE_STATUS


def test_publishing_through_update_requires_the_publisher_role(mongo):
    mongo.resources[RESOURCE_ID] = resource()
    _payload, status = write.update(RESOURCE_ID, body(status="published"), "alice")
    assert status == write.LEGACY_ROLE_FAILURE_STATUS


def test_a_deleted_file_is_dropped_from_the_resource(mongo):
    mongo.resources[RESOURCE_ID] = resource(
        filesObj=[{"id": "f1", "tag": "file"}, {"id": "f2", "tag": "file"}]
    )

    write.update(RESOURCE_ID, body(deletedFiles=["f1"]), "alice")

    assert [f["id"] for f in mongo.resources[RESOURCE_ID]["filesObj"]] == ["f2"]


def test_file_positions_can_be_updated(mongo):
    mongo.resources[RESOURCE_ID] = resource(filesObj=[{"id": "f1", "tag": "file", "order": 0}])

    write.update(RESOURCE_ID, body(updatedFiles=[{"id": "f1", "order": 5}]), "alice")

    assert mongo.resources[RESOURCE_ID]["filesObj"][0]["order"] == 5


def test_duplicate_file_entries_are_collapsed_by_id(mongo):
    """The original de-duplicated by comparing whole dicts, so two entries for
    the same file differing only in `order` both survived and the viewer showed
    it twice."""
    mongo.resources[RESOURCE_ID] = resource(
        filesObj=[{"id": "f1", "order": 0}, {"id": "f1", "order": 1}]
    )

    write.update(RESOURCE_ID, body(), "alice")

    assert len(mongo.resources[RESOURCE_ID]["filesObj"]) == 1


def test_moving_a_resource_rewrites_its_descendants_ancestry(mongo):
    mongo.resources[RESOURCE_ID] = resource()
    mongo.resources[CHILD_ID] = resource(
        _id=ObjectId(CHILD_ID), parent=[{"id": RESOURCE_ID, "post_type": "carpeta"}]
    )
    mongo.resources["root"] = {"_id": "root", "post_type": "carpeta", "parent": [], "parents": []}

    write.update(RESOURCE_ID, body(parent=[{"id": "root", "post_type": "carpeta"}]), "alice")

    written = [w for w in mongo.writes if str(w[1].get("_id")) == CHILD_ID]
    assert written and "parents" in written[-1][2]


def test_a_resource_cannot_be_moved_under_its_own_descendant(mongo):
    """The cycle guard from S15, reached through the real update path."""
    mongo.resources[RESOURCE_ID] = resource()
    mongo.resources[CHILD_ID] = resource(
        _id=ObjectId(CHILD_ID), parent=[{"id": RESOURCE_ID, "post_type": "carpeta"}]
    )

    _payload, status = write.update(
        RESOURCE_ID, body(parent=[{"id": CHILD_ID, "post_type": "carpeta"}]), "alice"
    )
    assert status == 400


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_deleting_moves_a_resource_to_the_recycle_bin(mongo):
    mongo.resources[RESOURCE_ID] = resource()

    payload, status = write.delete([RESOURCE_ID], "alice")

    assert (status, payload["ids"]) == (200, [RESOURCE_ID])
    assert mongo.resources[RESOURCE_ID]["status"] == write.STATUS_DELETED


def test_deleting_cascades_to_descendants(mongo):
    mongo.resources[RESOURCE_ID] = resource()
    mongo.resources[CHILD_ID] = resource(
        _id=ObjectId(CHILD_ID), parent=[{"id": RESOURCE_ID, "post_type": "carpeta"}]
    )

    write.delete([RESOURCE_ID], "alice")

    assert mongo.resources[CHILD_ID]["status"] == write.STATUS_DELETED


def test_a_failed_permission_check_leaves_the_whole_batch_untouched(mongo):
    """THE structural fix.

    The original checked and acted in the same loop and returned on the first
    refusal, so a caller who selected twelve resources and lacked rights on the
    ninth got eight deleted and an error saying nothing had happened.
    """
    mongo.resources[RESOURCE_ID] = resource()
    mongo.resources[CHILD_ID] = resource(_id=ObjectId(CHILD_ID), createdBy="someone-else")

    _payload, status = write.delete([RESOURCE_ID, CHILD_ID], "alice")

    assert status == write.LEGACY_ROLE_FAILURE_STATUS
    assert mongo.resources[RESOURCE_ID]["status"] == "draft"


def test_a_missing_id_anywhere_in_the_batch_stops_it(mongo):
    mongo.resources[RESOURCE_ID] = resource()

    _payload, status = write.delete([RESOURCE_ID, "6a70b833497d4440325c94ff"], "alice")

    assert status == 404
    assert mongo.resources[RESOURCE_ID]["status"] == "draft"


@pytest.mark.parametrize("bad", ["not-a-list", [1, 2], [None], {"ids": []}])
def test_delete_requires_a_list_of_string_ids(mongo, bad):
    _payload, status = write.delete(bad, "alice")
    assert status == 400


def test_deleting_retires_the_files_nothing_else_holds(mongo):
    """THE dead-code bug (F28).

    The original read `resource['files']`, but the stored field is `filesObj` -
    `files` is not a field of the Resource model at all, so the condition was
    always false and the cleanup never ran. Records stayed `uploaded`, pointing
    at a deleted resource.
    """
    mongo.resources[RESOURCE_ID] = resource(filesObj=[{"id": RECORD_ID, "tag": "file"}])
    mongo.records[RECORD_ID] = {
        "_id": ObjectId(RECORD_ID), "status": "uploaded",
        "parent": [{"id": RESOURCE_ID, "post_type": "carpeta"}],
    }

    write.delete([RESOURCE_ID], "alice")

    assert mongo.records[RECORD_ID]["status"] == "deleted"
    assert mongo.records[RECORD_ID]["parent"] == []


def test_a_file_held_by_another_resource_survives(mongo):
    """Deduplication means one record can belong to several resources; only the
    last reference going away retires it."""
    mongo.resources[RESOURCE_ID] = resource(filesObj=[{"id": RECORD_ID, "tag": "file"}])
    mongo.records[RECORD_ID] = {
        "_id": ObjectId(RECORD_ID), "status": "uploaded",
        "parent": [{"id": RESOURCE_ID}, {"id": "another-resource"}],
    }

    write.delete([RESOURCE_ID], "alice")

    assert mongo.records[RECORD_ID]["status"] == "uploaded"
    assert [p["id"] for p in mongo.records[RECORD_ID]["parent"]] == ["another-resource"]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restoring_returns_a_resource_as_a_draft(mongo):
    """Never straight to published: something withdrawn from the catalogue
    should not silently reappear in the public one."""
    mongo.resources[RESOURCE_ID] = resource(status=write.STATUS_DELETED)

    payload, status = write.restore([RESOURCE_ID], "alice")

    assert (status, payload["ids"]) == (200, [RESOURCE_ID])
    assert mongo.resources[RESOURCE_ID]["status"] == write.STATUS_DRAFT


def test_restore_does_not_touch_descendants_by_default(mongo):
    mongo.resources[RESOURCE_ID] = resource(status=write.STATUS_DELETED)
    mongo.resources[CHILD_ID] = resource(
        _id=ObjectId(CHILD_ID), status=write.STATUS_DELETED,
        parent=[{"id": RESOURCE_ID, "post_type": "carpeta"}],
    )

    write.restore([RESOURCE_ID], "alice")

    assert mongo.resources[CHILD_ID]["status"] == write.STATUS_DELETED


def test_a_recursive_restore_brings_descendants_back(mongo):
    mongo.resources[RESOURCE_ID] = resource(status=write.STATUS_DELETED)
    mongo.resources[CHILD_ID] = resource(
        _id=ObjectId(CHILD_ID), status=write.STATUS_DELETED,
        parent=[{"id": RESOURCE_ID, "post_type": "carpeta"}],
    )

    payload, _status = write.restore([RESOURCE_ID], "alice", recursive=True)

    assert CHILD_ID in payload["ids"]
    assert mongo.resources[CHILD_ID]["status"] == write.STATUS_DRAFT


def test_a_recursive_restore_skips_descendants_that_were_never_deleted(mongo):
    """They were removed separately, or never removed at all; bringing them
    back to draft would un-publish them."""
    mongo.resources[RESOURCE_ID] = resource(status=write.STATUS_DELETED)
    mongo.resources[CHILD_ID] = resource(
        _id=ObjectId(CHILD_ID), status="published",
        parent=[{"id": RESOURCE_ID, "post_type": "carpeta"}],
    )

    write.restore([RESOURCE_ID], "alice", recursive=True)

    assert mongo.resources[CHILD_ID]["status"] == "published"


def test_restoring_revives_the_resources_files(mongo):
    mongo.resources[RESOURCE_ID] = resource(
        status=write.STATUS_DELETED, filesObj=[{"id": RECORD_ID}]
    )
    mongo.records[RECORD_ID] = {"_id": ObjectId(RECORD_ID), "status": "deleted", "parent": []}

    write.restore([RESOURCE_ID], "alice")

    assert mongo.records[RECORD_ID]["status"] == "uploaded"


def test_a_revived_file_with_derivatives_returns_to_processed(mongo):
    mongo.resources[RESOURCE_ID] = resource(
        status=write.STATUS_DELETED, filesObj=[{"id": RECORD_ID}]
    )
    mongo.records[RECORD_ID] = {
        "_id": ObjectId(RECORD_ID), "status": "deleted", "parent": [],
        "processing": {"files": ["a.jpg"]},
    }

    write.restore([RESOURCE_ID], "alice")

    assert mongo.records[RECORD_ID]["status"] == "processed"


@pytest.mark.parametrize("bad", ["nope", [1], None])
def test_restore_requires_a_list_of_string_ids(mongo, bad):
    _payload, status = write.restore(bad, "alice")
    assert status == 400


def test_recursive_must_be_a_boolean(mongo):
    _payload, status = write.restore([RESOURCE_ID], "alice", recursive="yes")
    assert status == 400


# ---------------------------------------------------------------------------
# Reciprocal relations
# ---------------------------------------------------------------------------


@pytest.fixture
def related(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: {
            "fields": [
                {"destiny": "metadata.firstLevel.title", "type": "text", "label": "Title"},
                {"destiny": "metadata.related", "type": "relation", "label": "Related",
                 "relation_type": "carpeta"},
            ]
        },
    )
    mongo.resources[CHILD_ID] = resource(_id=ObjectId(CHILD_ID))
    return mongo


def test_creating_with_a_relation_links_the_other_side(related):
    """THE F22 fix, reached through the real path.

    The original ran this *before* the insert and dereferenced `body['_id']`,
    which did not exist yet - so creating a resource with a populated same-type
    relation raised KeyError and returned 500.
    """
    payload, status = write.create(
        body(metadata={"firstLevel": {"title": "A"}, "related": [{"id": CHILD_ID}]}), "alice"
    )

    assert status == 201
    back = related.resources[CHILD_ID]["metadata"]["related"]
    assert [r["id"] for r in back] == [payload["id"]]


def test_removing_a_relation_unlinks_the_other_side(related):
    related.resources[RESOURCE_ID] = resource(
        metadata={"firstLevel": {"title": "A"}, "related": [{"id": CHILD_ID}]}
    )
    related.resources[CHILD_ID]["metadata"]["related"] = [{"id": RESOURCE_ID}]

    write.update(RESOURCE_ID, body(metadata={"firstLevel": {"title": "A"}}), "alice")

    assert related.resources[CHILD_ID]["metadata"]["related"] == []


def test_a_reciprocal_update_touches_only_that_field(related):
    """The original rebuilt the entire target document and wrote it back, so a
    reciprocal update rewrote every field of a resource nobody had asked to
    change."""
    write.create(
        body(metadata={"firstLevel": {"title": "A"}, "related": [{"id": CHILD_ID}]}), "alice"
    )

    reciprocal = [w for w in related.writes if str(w[1].get("_id")) == CHILD_ID]
    assert set(reciprocal[-1][2]) == {"metadata.related", "updatedAt", "updatedBy"}
