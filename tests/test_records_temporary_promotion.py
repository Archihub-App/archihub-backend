"""Which staged uploads a resource write may attach."""

from __future__ import annotations

from bson.objectid import ObjectId

from archihub.api.records import storage

TEMP_ID = "6a70b833497d4440325c94b1"
EXISTING_ID = "6a70b833497d4440325c94b2"


class FakeMongo:
    def __init__(self, rows):
        self.rows = rows
        self.deleted = []
        self.updated = []

    def get_record(self, collection, filters, fields=None):
        for row in self.rows:
            if all(_match(row.get(k), v) for k, v in filters.items()):
                return dict(row)
        return None

    def delete_record(self, collection, filters):
        self.deleted.append(filters)

    def update_record(self, collection, filters, update):
        self.updated.append((filters, update))


def _match(value, condition):
    if isinstance(condition, dict) and "$ne" in condition:
        return value != condition["$ne"]
    return value == condition


def _staged(owner):
    return {"_id": ObjectId(TEMP_ID), "temporary": True, "createdBy": owner,
            "hash": "abc", "filepath": "/nonexistent/x.bin"}


def _quiet(monkeypatch, fake):
    monkeypatch.setattr(storage, "_mongo", lambda: fake)
    monkeypatch.setattr(storage, "_audit", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(storage, "_add_parent", lambda *a, **k: None)


def test_another_users_staged_upload_is_not_attached(monkeypatch):
    fake = FakeMongo([_staged("bob")])
    _quiet(monkeypatch, fake)

    attached = storage.promote_temporary_records("r1", {}, [{"id": TEMP_ID}], "alice")

    assert attached == []
    assert fake.updated == [] and fake.deleted == []


def test_identical_content_the_uploader_cannot_see_is_not_reused(monkeypatch):
    existing = {"_id": ObjectId(EXISTING_ID), "hash": "abc", "status": "uploaded"}
    fake = FakeMongo([_staged("alice"), existing])
    _quiet(monkeypatch, fake)
    monkeypatch.setattr(storage, "_may_reuse", lambda record, user: False)

    attached = storage.promote_temporary_records("r1", {}, [{"id": TEMP_ID}], "alice")

    assert [a.id if hasattr(a, "id") else a["id"] for a in attached] == [TEMP_ID]
