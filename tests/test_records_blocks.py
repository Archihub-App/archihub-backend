"""Editing the OCR block layout of a page.

The first section is the regression test for BACKEND_FINDINGS S19: a global
role was the only check the originals made, so any editor could rewrite the OCR
of any record in the archive - including one filed under a series they cannot
open. Keep those tests.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.records import access, blocks
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

RECORD_ID = "6a70b833497d4440325c94b1"
RESERVED_SERIES = "6a70b833497d4440325c94c1"


class FakeMongo:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.resources: dict[str, dict] = {}
        self.updates: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        key = str((filters or {}).get("_id"))
        if collection == "resources":
            return self.resources.get(key)
        return self.records.get(key)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection != "resources":
            return []
        wanted = {str(o) for o in (filters or {}).get("_id", {}).get("$in", [])}
        return [r for k, r in self.resources.items() if k in wanted]

    def update_record(self, collection, filters, update):
        self.updates.append((filters, update))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(blocks, "_mongo", lambda: fake)
    monkeypatch.setattr(access, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.access._mongo", lambda: fake)
    monkeypatch.setattr(blocks, "_call_hook", lambda *a, **k: None)
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)
    monkeypatch.setattr("archihub.api.resources.access.user_access_rights", lambda u: [])


def roles(monkeypatch, *held):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r in held)


def rights(monkeypatch, *held):
    monkeypatch.setattr("archihub.api.resources.access.user_access_rights", lambda u: list(held))


def ocr_record(pages=None, access_rights=None, parent=None):
    return {
        "_id": ObjectId(RECORD_ID),
        "accessRights": access_rights,
        "parent": parent or [],
        "processing": {
            "fileProcessing": {"type": "document", "path": "2024/03/doc"},
            "ocr": {
                "type": "ocr",
                "result": pages if pages is not None else [{"blocks": [{"text": "a"}]}],
            },
        },
    }


def body(**overrides):
    payload = {"id_doc": RECORD_ID, "slug": "ocr", "type_block": "blocks", "page": 1}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def test_an_editor_may_not_edit_blocks_of_a_record_they_cannot_open(mongo, monkeypatch):
    """The whole reason this module exists.

    The record is filed under a reserved series whose access right this editor
    does not hold, so they cannot see it in the interface - and now cannot
    rewrite its OCR through the API either.
    """
    roles(monkeypatch, "editor")
    mongo.resources[RESERVED_SERIES] = {"accessRights": "restricted", "parents": []}
    mongo.records[RECORD_ID] = ocr_record(parent=[{"id": RESERVED_SERIES}])

    payload, status = blocks.update(
        "ed", body(index=0, bbox=[0, 0, 1, 1], data={"text": "x"})
    )

    assert status == ROLE_FAILURE_STATUS
    assert mongo.updates == []


def test_an_editor_holding_the_right_may_edit(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    rights(monkeypatch, "restricted")
    mongo.resources[RESERVED_SERIES] = {"accessRights": "restricted", "parents": []}
    mongo.records[RECORD_ID] = ocr_record(parent=[{"id": RESERVED_SERIES}])

    payload, status = blocks.update(
        "ed", body(index=0, bbox=[0, 0, 1, 1], data={"text": "x"})
    )

    assert status == 200


def test_an_administrator_may_edit_regardless(mongo, monkeypatch):
    roles(monkeypatch, "admin")
    mongo.resources[RESERVED_SERIES] = {"accessRights": "restricted", "parents": []}
    mongo.records[RECORD_ID] = ocr_record(parent=[{"id": RESERVED_SERIES}])

    payload, status = blocks.update(
        "root", body(index=0, bbox=[0, 0, 1, 1], data={})
    )

    assert status == 200


def test_a_caller_who_can_read_but_holds_no_editing_role_is_refused(mongo, monkeypatch):
    """Visibility is necessary but not sufficient."""
    mongo.records[RECORD_ID] = ocr_record()

    payload, status = blocks.delete("reader", body(index=0))

    assert status == ROLE_FAILURE_STATUS
    assert mongo.updates == []


def test_a_reserved_series_is_inherited_from_an_ancestor(mongo, monkeypatch):
    """Restricting a fonds restricts the OCR of everything filed beneath it."""
    roles(monkeypatch, "editor")
    fonds = "6a70b833497d4440325c94d1"
    mongo.resources[fonds] = {
        "_id": ObjectId(fonds),
        "accessRights": "restricted",
        "parents": [],
    }
    mongo.resources[RESERVED_SERIES] = {
        "_id": ObjectId(RESERVED_SERIES),
        "accessRights": None,
        "parents": [{"id": fonds}],
    }
    mongo.records[RECORD_ID] = ocr_record(parent=[{"id": RESERVED_SERIES}])

    payload, status = blocks.update("ed", body(index=0, bbox=[0, 0, 1, 1]))

    assert status == ROLE_FAILURE_STATUS


# ---------------------------------------------------------------------------
# What may be written
# ---------------------------------------------------------------------------


def test_adding_a_block_appends_it_with_its_box(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": [{"text": "a"}]}])

    payload, status = blocks.add(
        "ed", body(bbox=[1, 2, 3, 4], data={"text": "new"})
    )

    assert status == 200
    _filters, update = mongo.updates[0]
    assert update["processing.ocr.result.0.blocks"] == [
        {"text": "a"},
        {"text": "new", "bbox": [1, 2, 3, 4]},
    ]


def test_a_client_may_not_write_arbitrary_keys_into_a_block(mongo, monkeypatch):
    """`data` used to be splatted in whole, so anything at all could be stored."""
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": []}])

    blocks.add(
        "ed",
        body(bbox=[0, 0, 1, 1], data={"text": "ok", "words": [{"x": 1}], "__proto__": "no"})
    )

    _filters, update = mongo.updates[0]
    assert update["processing.ocr.result.0.blocks"] == [{"text": "ok", "bbox": [0, 0, 1, 1]}]


def test_a_write_addresses_one_page_not_the_whole_processing_block(mongo, monkeypatch):
    """A plugin finishing another processing must not be clobbered by an edit."""
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record()

    blocks.update("ed", body(index=0, bbox=[0, 0, 1, 1]))

    _filters, update = mongo.updates[0]
    assert set(update) == {"processing.ocr.result.0.blocks", "updatedBy", "updatedAt"}


def test_updating_merges_rather_than_replacing(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": [{"text": "a", "order": 3}]}])

    blocks.update("ed", body(index=0, bbox=[9, 9, 9, 9], data={"text": "b"}))

    _filters, update = mongo.updates[0]
    assert update["processing.ocr.result.0.blocks"] == [
        {"text": "b", "order": 3, "bbox": [9, 9, 9, 9]}
    ]


def test_deleting_removes_the_addressed_block(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": [{"text": "a"}, {"text": "b"}]}])

    payload, status = blocks.delete("ed", body(index=0))

    assert status == 200
    _filters, update = mongo.updates[0]
    assert update["processing.ocr.result.0.blocks"] == [{"text": "b"}]


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index", [-1, 5, "one", None])
def test_a_block_index_outside_the_page_is_refused(mongo, monkeypatch, index):
    """`list.pop(-1)` would delete the last block on the page instead."""
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": [{"text": "a"}]}])

    payload, status = blocks.delete("ed", body(index=index))

    assert status == 400
    assert mongo.updates == []


def test_a_page_past_the_end_is_a_404(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": []}])

    payload, status = blocks.add("ed", body(page=9, bbox=[0, 0, 1, 1]))

    assert status == 404


def test_pages_are_addressed_one_indexed(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record([{"blocks": []}, {"blocks": []}])

    blocks.add("ed", body(page=2, bbox=[0, 0, 1, 1]))

    _filters, update = mongo.updates[0]
    assert "processing.ocr.result.1.blocks" in update


def test_an_unsupported_block_type_is_refused(mongo, monkeypatch):
    """The originals fell through and reported success having written nothing."""
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record()

    payload, status = blocks.add(
        "ed", body(type_block="regions", bbox=[0, 0, 1, 1])
    )

    assert status == 400
    assert mongo.updates == []


def test_an_unknown_slug_is_a_404(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record()

    payload, status = blocks.add("ed", body(slug="nosuch", bbox=[0, 0, 1, 1]))

    assert status == 404


def test_a_record_that_does_not_exist_is_a_404(mongo, monkeypatch):
    roles(monkeypatch, "editor")

    payload, status = blocks.add("ed", body(bbox=[0, 0, 1, 1]))

    assert status == 404


def test_a_chunked_result_is_refused_rather_than_written_to_the_wrong_place(mongo, monkeypatch):
    """Chunked results live in another collection; a dotted $set would miss them."""
    roles(monkeypatch, "editor")
    record = ocr_record()
    record["processing"]["ocr"] = {
        "type": "ocr",
        "result_storage": {"type": "chunked", "collection": "ocr_chunks"},
    }
    mongo.records[RECORD_ID] = record

    payload, status = blocks.add("ed", body(bbox=[0, 0, 1, 1]))

    assert status == 400
    assert mongo.updates == []


def test_adding_without_a_box_is_refused(mongo, monkeypatch):
    roles(monkeypatch, "editor")
    mongo.records[RECORD_ID] = ocr_record()

    payload, status = blocks.add("ed", body())

    assert status == 400


@pytest.mark.parametrize("missing", ["id_doc", "slug"])
def test_a_request_that_names_nothing_is_refused(mongo, monkeypatch, missing):
    roles(monkeypatch, "editor")
    payload = body(bbox=[0, 0, 1, 1])
    payload.pop(missing)

    _result, status = blocks.add("ed", payload)

    assert status == 400
