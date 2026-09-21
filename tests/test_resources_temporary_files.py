"""Tests for resource create and update with pre-uploaded temporary files."""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.resources import write

RESOURCE_ID = "6a70b833497d4440325c94b1"
TEMP_RECORD_1 = "6a70b833497d4440325c94c1"
TEMP_RECORD_2 = "6a70b833497d4440325c94c2"


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
        self._next = 0

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
            return [r for r in self.resources.values() if (r.get("parent") or {}).get("id") == parent_id]
        return list(self.resources.values())

    def insert_record(self, collection, record):
        self._next += 1
        record_id = ObjectId()
        doc = {**record, "_id": record_id}
        if collection == "resources":
            self.resources[str(record_id)] = doc
        else:
            self.records[str(record_id)] = doc
        self.inserted.append(doc)
        return Inserted(record_id)

    def update_record(self, collection, filters, update_model):
        key = str(filters.get("_id"))
        self.writes.append((collection, filters, update_model))
        target = self.resources.get(key) if collection == "resources" else self.records.get(key)
        if target:
            target.update(update_model)

    def delete_record(self, collection, filters):
        key = str(filters.get("_id"))
        if collection == "resources":
            self.resources.pop(key, None)
        else:
            self.records.pop(key, None)


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
                {"destiny": "metadata.firstLevel.title", "type": "text", "label": "Title", "required": True},
                {"destiny": "files", "type": "file", "label": "Files", "filetag": "file", "maxFiles": 2},
            ]
        },
    )
    monkeypatch.setattr("archihub.core.roles.get_access_rights", lambda: {"options": []})
    fake.user = {"accessRights": []}

    # Mock promote_temporary_records
    def fake_promote(res_id, res_meta, temp_files, user):
        attached = []
        for item in temp_files:
            rec_id = item.get("id")
            rec = fake.records.get(str(rec_id))
            if rec:
                rec["temporary"] = False
                rec["status"] = "uploaded"
                rec["parent"] = [{"id": res_id, "post_type": res_meta.get("post_type")}]
                attached.append({"id": str(rec_id), "tag": item.get("filetag") or item.get("tag") or "file", "order": item.get("order")})
        return attached

    monkeypatch.setattr("archihub.api.records.storage.promote_temporary_records", fake_promote)

    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


def test_create_resource_with_temporary_files(mongo):
    # Pre-populate temporary records in mongo
    mongo.records[TEMP_RECORD_1] = {
        "_id": ObjectId(TEMP_RECORD_1),
        "name": "doc1.pdf",
        "temporary": True,
        "status": "temporary",
    }

    body = {
        "post_type": "carpeta",
        "metadata": {"firstLevel": {"title": "Folder 1"}},
        "temporaryFiles": [{"id": TEMP_RECORD_1, "filetag": "file", "order": 0}],
    }

    result, status = write.create(body, "alice")
    assert status == 201
    resource_id = result["id"]

    resource = mongo.resources[resource_id]
    assert len(resource["filesObj"]) == 1
    assert resource["filesObj"][0]["id"] == TEMP_RECORD_1
    assert resource["filesObj"][0]["tag"] == "file"

    # Temporary record promoted
    temp_rec = mongo.records[TEMP_RECORD_1]
    assert temp_rec["temporary"] is False
    assert temp_rec["status"] == "uploaded"
    assert temp_rec["parent"] == [{"id": resource_id, "post_type": "carpeta"}]


def test_create_resource_validates_temporary_files_ceiling(mongo):
    body = {
        "post_type": "carpeta",
        "metadata": {"firstLevel": {"title": "Folder With Too Many Files"}},
        "temporaryFiles": [
            {"id": "rec1", "filetag": "file"},
            {"id": "rec2", "filetag": "file"},
            {"id": "rec3", "filetag": "file"},  # Max is 2
        ],
    }

    result, status = write.create(body, "alice")
    assert status == 400
    assert "file" in result.get("errors", {})


def test_update_resource_with_temporary_files(mongo):
    # Existing resource
    mongo.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID),
        "post_type": "carpeta",
        "createdBy": "alice",
        "metadata": {"firstLevel": {"title": "Original Folder"}},
        "filesObj": [{"id": "existing_rec", "tag": "file", "order": 0}],
        "parents": [],
    }

    # Pre-populate temporary record
    mongo.records[TEMP_RECORD_2] = {
        "_id": ObjectId(TEMP_RECORD_2),
        "name": "doc2.pdf",
        "temporary": True,
        "status": "temporary",
    }

    body = {
        "post_type": "carpeta",
        "metadata": {"firstLevel": {"title": "Updated Folder"}},
        "temporaryFiles": [{"id": TEMP_RECORD_2, "filetag": "file", "order": 1}],
    }

    result, status = write.update(RESOURCE_ID, body, "alice")
    assert status == 200

    resource = mongo.resources[RESOURCE_ID]
    file_ids = [f["id"] for f in resource["filesObj"]]
    assert "existing_rec" in file_ids
    assert TEMP_RECORD_2 in file_ids
