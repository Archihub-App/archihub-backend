"""Tests for temporary file staging, promotion, deletion, cleanup, and access."""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest
from bson.objectid import ObjectId

from archihub.api.records import access, storage
from archihub.core import files as filestore

RESOURCE_ID = "6a70b833497d4440325c94b1"


class Inserted:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeMongo:
    def __init__(self):
        self.records: list[dict] = []
        self.resources: dict[str, dict] = {}
        self.updates: list[tuple[dict, dict]] = []
        self._next = 0

    def get_record(self, collection, filters=None, fields=None):
        if collection == "resources":
            return self.resources.get(str(filters.get("_id")))
        if "hash" in (filters or {}):
            matches = [r for r in self.records if r["hash"] == filters["hash"]]
            if filters.get("temporary") == {"$ne": True}:
                matches = [r for r in matches if not r.get("temporary")]
            if filters.get("status") == {"$ne": "deleted"}:
                matches = [r for r in matches if r.get("status") != "deleted"]
            return matches[0] if matches else None
        target_id = filters.get("_id")
        matches = [r for r in self.records if r.get("_id") == target_id]
        if filters.get("temporary") is True:
            matches = [r for r in matches if r.get("temporary") is True]
        return matches[0] if matches else None

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection != "records":
            return []
        res = list(self.records)
        if filters:
            if filters.get("temporary") is True:
                res = [r for r in res if r.get("temporary") is True]
            if "createdAt" in filters and "$lt" in filters["createdAt"]:
                cutoff = filters["createdAt"]["$lt"]
                res = [r for r in res if r.get("createdAt") < cutoff]
        return res

    def insert_record(self, collection, record):
        self._next += 1
        record_id = ObjectId()
        self.records.append({**record, "_id": record_id})
        return Inserted(record_id)

    def update_record(self, collection, filters, update_model):
        self.updates.append((filters, update_model))
        target_id = filters.get("_id")
        for record in self.records:
            if record.get("_id") == target_id:
                record.update(update_model)

    def delete_record(self, collection, filters):
        target_id = filters.get("_id")
        self.records = [r for r in self.records if r.get("_id") != target_id]


@pytest.fixture
def mongo(monkeypatch, tmp_path):
    fake = FakeMongo()
    fake.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID),
        "post_type": "carpeta",
        "parents": [{"id": "root", "post_type": "fondo"}],
    }
    monkeypatch.setattr(storage, "_mongo", lambda: fake)
    monkeypatch.setattr(storage, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(storage, "_call_hook", lambda *a, **k: None)

    settings = storage.get_settings()
    monkeypatch.setattr(settings, "original_files_path", str(tmp_path / "originals"), raising=False)
    monkeypatch.setattr(settings, "temporal_files_path", str(tmp_path / "temporal"), raising=False)
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(filestore, "sniff_media_type", lambda path: "image/jpeg")

    fake.root = tmp_path
    return fake


def upload(content: bytes, filename: str = "temp.jpg", **kwargs) -> storage.IncomingFile:
    return storage.IncomingFile(filename=filename, stream=io.BytesIO(content), **kwargs)


def test_store_temporary_file_creates_staged_record_and_file(mongo):
    content = b"temporary document bytes"
    incoming = upload(content, filename="report.pdf")

    result = storage.store_temporary_file(incoming, "alice")

    assert result["id"]
    assert result["name"] == "report.pdf"
    assert result["size"] == len(content)

    assert len(mongo.records) == 1
    record = mongo.records[0]
    assert record["temporary"] is True
    assert record["status"] == "temporary"
    assert record["createdBy"] == "alice"
    assert record["parent"] == []
    assert record["parents"] == []
    # File exists in temporal directory
    assert Path(record["filepath"]).is_file()
    assert "temporal" in record["filepath"]


def test_promote_temporary_record_moves_file_and_updates_status(mongo):
    incoming = upload(b"unique content for test", filename="data.csv")
    temp_result = storage.store_temporary_file(incoming, "alice")
    temp_id = temp_result["id"]

    resource = {"post_type": "expediente", "parents": [{"id": "parent1", "post_type": "serie"}]}
    attached = storage.promote_temporary_records(
        RESOURCE_ID,
        resource,
        [{"id": temp_id, "tag": "main_file", "order": 1}],
        "alice",
    )

    assert len(attached) == 1
    assert attached[0]["id"] == str(temp_id)
    assert attached[0]["tag"] == "main_file"
    assert attached[0]["order"] == 1

    record = mongo.records[0]
    assert record["temporary"] is False
    assert record["status"] == "uploaded"
    assert record["parent"] == [{"id": RESOURCE_ID, "post_type": "expediente"}]
    assert record["parents"] == [{"id": "parent1", "post_type": "serie"}]

    # The file should now exist in originals directory, not temporal
    orig_path = Path(storage.get_settings().original_files_path) / record["filepath"]
    assert orig_path.is_file()


def test_promote_temporary_record_deduplicates_with_existing_permanent(mongo):
    content = b"same duplicate bytes"
    # Existing permanent record
    perm_attached = storage.attach_files(RESOURCE_ID, [upload(content, "original.jpg")], "alice")
    perm_id = perm_attached[0]["id"]

    # Now upload same content temporarily
    temp_result = storage.store_temporary_file(upload(content, "temp_duplicate.jpg"), "bob")
    temp_id = temp_result["id"]
    assert len(mongo.records) == 2

    # Promote to another resource
    OTHER_RESOURCE = "6a70b833497d4440325c94b2"
    resource = {"post_type": "documento", "parents": []}
    attached = storage.promote_temporary_records(
        OTHER_RESOURCE,
        resource,
        [{"id": temp_id, "tag": "doc_file"}],
        "bob",
    )

    # Should have re-used the permanent record ID
    assert len(attached) == 1
    assert attached[0]["id"] == perm_id
    assert attached[0]["tag"] == "doc_file"

    # Temporary record document should be deleted
    assert len(mongo.records) == 1
    perm_record = mongo.records[0]
    parent_ids = [p["id"] for p in perm_record["parent"]]
    assert RESOURCE_ID in parent_ids
    assert OTHER_RESOURCE in parent_ids


def test_delete_temporary_record_removes_file_and_document(mongo):
    temp_result = storage.store_temporary_file(upload(b"to be deleted"), "alice")
    temp_id = temp_result["id"]
    filepath = Path(mongo.records[0]["filepath"])
    assert filepath.is_file()

    msg, status = storage.delete_temporary_record(temp_id, user="alice")
    assert status == 200
    assert len(mongo.records) == 0
    assert not filepath.is_file()


def test_delete_temporary_record_permission_denied_for_other_user(mongo):
    temp_result = storage.store_temporary_file(upload(b"private bytes"), "alice")
    temp_id = temp_result["id"]

    msg, status = storage.delete_temporary_record(temp_id, user="bob", is_admin=False)
    assert status == 403
    assert len(mongo.records) == 1


def test_cleanup_temporary_records_removes_old_files_and_keeps_recent(mongo):
    now = datetime.datetime.now(datetime.timezone.utc)
    # Old file (>24h ago)
    res_old = storage.store_temporary_file(upload(b"old file"), "alice")
    mongo.records[0]["createdAt"] = now - datetime.timedelta(hours=25)
    old_path = Path(mongo.records[0]["filepath"])

    # Recent file (1h ago)
    res_recent = storage.store_temporary_file(upload(b"recent file"), "bob")
    mongo.records[1]["createdAt"] = now - datetime.timedelta(hours=1)
    recent_path = Path(mongo.records[1]["filepath"])

    assert len(mongo.records) == 2
    assert old_path.is_file()
    assert recent_path.is_file()

    cleaned = storage.cleanup_temporary_records(max_age_seconds=86400)
    assert cleaned == 1
    assert len(mongo.records) == 1
    assert mongo.records[0]["_id"] == ObjectId(res_recent["id"])
    assert not old_path.is_file()
    assert recent_path.is_file()


def test_access_may_view_temporary_record():
    record = {
        "temporary": True,
        "createdBy": "alice",
        "parent": [],
    }

    assert access.may_view_record("alice", record, is_admin=False) is True
    assert access.may_view_record("admin_user", record, is_admin=True) is True
    assert access.may_view_record("charlie", record, is_admin=False) is False
