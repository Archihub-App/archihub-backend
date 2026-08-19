"""Conversation history — a 200 that renders as "No results".

`AImessaging.tsx` reads this route in a way that fails silently in four separate
places, and an earlier revision of this port broke all four at once:

* ``Array.isArray(response) ? response : []`` — an envelope is read as *empty*
* ``item._id.$oid`` — the row is opened and deleted by that exact shape
* ``item.messages[0].content`` — the first message is what labels the row
* the record is named ``id`` in the request, not ``record_id``

None of them produces an error. The panel shows "No results" to a user who has
conversations, which is what was reported.

The tests below assert the wire shape rather than the internals, because the
shape is the contract and it is what drifted.
"""

from __future__ import annotations

import datetime

import pytest

from archihub.api.aiservices import conversations

USER = "admin@test.com"
RECORD_ID = "690b9435a8c89c9b3a7d1a98"


def _stored(**overrides):
    from bson.objectid import ObjectId

    row = {
        "_id": ObjectId("6903d6f224d4135eb1ca339d"),
        "user": USER,
        "type": "transcription",
        "record_id": RECORD_ID,
        "processing_slug": "transcribeWhisperX",
        "created_at": datetime.datetime(2026, 8, 1),
        "updated_at": datetime.datetime(2026, 8, 2),
        "messages": [
            {"role": "user", "content": "give me a summary"},
            {"role": "assistant", "content": "here it is"},
            {"role": "user", "content": "and the speakers?"},
        ],
    }
    row.update(overrides)
    return row


class FakeMongo:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[dict, dict, list]] = []

    def get_all_records(self, collection, filters, fields=None, sort=None, **kwargs):
        self.queries.append((filters, fields or {}, sort or []))
        self.extra_kwargs = kwargs
        return list(self.rows)

    def get_record(self, collection, filters, fields=None):
        return self.rows[0] if self.rows else None


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo([_stored()])
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake


@pytest.fixture
def visible(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible",
        lambda record_id, user: ({"_id": record_id}, None),
    )


# ---------------------------------------------------------------------------
# The wire shape
# ---------------------------------------------------------------------------


def test_history_is_a_bare_array(mongo, visible):
    """An envelope is read by the component as *no results*."""
    rows, status = conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    assert status == 200
    assert isinstance(rows, list), f"expected a list, got {type(rows).__name__}"


def test_a_row_keeps_its_id_in_extended_json(mongo, visible):
    """The component opens and deletes by `item._id.$oid`."""
    rows, _status = conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    assert "_id" in rows[0], "renaming _id to id leaves every click throwing"
    assert rows[0]["_id"] == {"$oid": "6903d6f224d4135eb1ca339d"}
    assert "id" not in rows[0]


def test_a_row_carries_exactly_one_message(mongo, visible):
    """`item.messages[0].content` labels the row; the rest is a whole transcript."""
    rows, _status = conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    assert len(rows[0]["messages"]) == 1
    assert rows[0]["messages"][0]["content"] == "give me a summary"


def test_a_conversation_with_no_messages_does_not_explode(monkeypatch, visible):
    fake = FakeMongo([_stored(messages=[])])
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)

    rows, status = conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    assert status == 200
    assert rows[0]["messages"] == []


# ---------------------------------------------------------------------------
# What it filters on
# ---------------------------------------------------------------------------


def test_the_record_is_named_id_in_the_request(mongo, visible):
    """The component sends `id`. Reading `record_id` filters on nothing."""
    conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    filters, _fields, _sort = mongo.queries[0]
    assert filters["record_id"] == RECORD_ID
    assert filters["user"] == USER


def test_a_record_conversation_is_not_narrowed_to_one_kind(mongo, visible):
    """`record` means every conversation about it, as in the legacy route."""
    conversations.history({"type": "record", "id": RECORD_ID}, USER)

    filters, _fields, _sort = mongo.queries[0]
    assert "type" not in filters


def test_the_processing_slug_narrows_when_given(mongo, visible):
    conversations.history(
        {"type": "document", "id": RECORD_ID, "processing_slug": "ocrProcessingHF"}, USER
    )

    filters, _fields, _sort = mongo.queries[0]
    assert filters["type"] == "document"
    assert filters["processing_slug"] == "ocrProcessingHF"


def test_a_gallery_conversation_hangs_off_the_resource(mongo, visible):
    conversations.history({"type": "image_gallery", "id": "abc"}, USER)

    filters, _fields, _sort = mongo.queries[0]
    assert filters == {"user": USER, "resource_id": "abc", "type": "image_gallery"}


def test_atlas_is_not_filtered_by_a_record(mongo, visible):
    conversations.history({"type": "atlas", "processing_slug": "atlas"}, USER)

    filters, _fields, _sort = mongo.queries[0]
    assert filters == {"user": USER, "type": "atlas", "processing_slug": "atlas"}


def test_newest_first(mongo, visible):
    conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    _filters, _fields, sort = mongo.queries[0]
    assert sort == [("updated_at", -1)]


def test_it_is_not_paginated(mongo, visible):
    """The panel has no paging control, so a cap hides conversations for good.

    Asserted on the query the driver actually receives. An earlier version of
    this test scanned the source for the word "limit" and failed on the
    docstring explaining why there isn't one - a guard that reads the prose
    rather than the behaviour.
    """
    conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    assert mongo.extra_kwargs == {}


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_an_unknown_kind_is_an_empty_list_not_an_error(mongo, visible):
    """The panel asks on open; a 400 replaces empty history with a dialog."""
    rows, status = conversations.history({"type": "nonsense", "id": RECORD_ID}, USER)

    assert (rows, status) == ([], 200)


def test_no_record_id_is_an_empty_list(mongo, visible):
    rows, status = conversations.history({"type": "transcription"}, USER)

    assert (rows, status) == ([], 200)
    assert mongo.queries == []


def test_a_record_the_caller_cannot_see_keeps_its_status(monkeypatch, mongo):
    """Their own conversations, but the record may have been reserved since."""
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible",
        lambda record_id, user: (None, ({"msg": "nope"}, 403)),
    )

    payload, status = conversations.history({"type": "transcription", "id": RECORD_ID}, USER)

    assert status == 403
    assert mongo.queries == []


# ---------------------------------------------------------------------------
# Opening one
# ---------------------------------------------------------------------------


def test_opening_a_conversation_keeps_its_id(mongo):
    """`response._id.$oid` tells the component which thread it is continuing.

    Renaming it to `id` loads the messages and then sends the next turn as a
    NEW conversation - the thread forks silently on the first reply.
    """
    payload, status = conversations.get("6903d6f224d4135eb1ca339d", USER)

    assert status == 200
    assert payload["_id"] == {"$oid": "6903d6f224d4135eb1ca339d"}
    assert "id" not in payload


def test_opening_someone_elses_conversation_is_refused(mongo):
    payload, status = conversations.get("6903d6f224d4135eb1ca339d", "someone.else@test.com")

    assert status in (401, 403)
