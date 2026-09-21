"""Resolving the references an article's blocks embed.

The published view renders snaps and uploaded records from ids held in the
block's HTML. Reading those ids is the whole job: when nothing is read, every
such block is published empty and the reader sees a bare placeholder chip.
"""

from __future__ import annotations

import pytest

from archihub.api.resources import article, public

RECORD_A = "6aa80a2ddfaf9e57d8f75914"
RECORD_B = "6aa80a2ddfaf9e57d8f7592a"
SNAP_A = "6aa818cc868c10c25a0628b0"


def _records_block(*ids, quote="'"):
    """The fragment ArticleEditor writes for an uploaded-records block."""
    import json

    payload = json.dumps([{"id": i, "name": f"{i}.CR2"} for i in ids])
    return f"<div data-records={quote}{payload}{quote}>{len(ids)} archivos</div>"


# ---------------------------------------------------------------------------
# Reading ids out of a block
# ---------------------------------------------------------------------------


def test_ids_are_read_from_the_fragment_the_editor_writes():
    assert article.extract_ids(_records_block(RECORD_A, RECORD_B), "data-records") == [RECORD_A, RECORD_B]


def test_a_double_quoted_attribute_is_read_too():
    import html

    fragment = f'<div data-records="{html.escape(_records_block(RECORD_A).split(chr(39))[1])}">x</div>'

    assert article.extract_ids(fragment, "data-records") == [RECORD_A]


def test_snap_entries_are_read_by_their_id():
    fragment = f"""<div data-snaps='[{{"id":"{SNAP_A}","title":"IMG_7674.CR2","view":"image"}}]'>1 snap</div>"""

    assert article.extract_ids(fragment, "data-snaps") == [SNAP_A]


def test_bare_ids_are_still_accepted():
    assert article.extract_ids(f"""<div data-records='["{RECORD_A}"]'></div>""", "data-records") == [RECORD_A]


@pytest.mark.parametrize(
    "fragment",
    [
        "<div data-records='not json'></div>",
        "<div data-records='[{\"name\": \"no id\"}]'></div>",
        "<div>no attribute</div>",
        None,
        ["not", "a", "string"],
    ],
)
def test_an_unreadable_block_yields_no_ids_rather_than_raising(fragment):
    assert article.extract_ids(fragment, "data-records") == []


def test_the_attribute_asked_for_is_the_one_read():
    assert article.extract_ids(_records_block(RECORD_A), "data-snaps") == []


# ---------------------------------------------------------------------------
# Hydrating a published article
# ---------------------------------------------------------------------------


@pytest.fixture
def published(monkeypatch):
    """Records and snaps in the database, and which records are public."""
    from bson.objectid import ObjectId

    public_records = {
        RECORD_A: {"_id": ObjectId(RECORD_A), "name": "a.CR2", "processing": {"fileProcessing": {"type": "image"}}},
        RECORD_B: {"_id": ObjectId(RECORD_B), "name": "b.CR2", "processing": {"fileProcessing": {"type": "image"}}},
    }

    def load_public(record_id):
        record = public_records.get(str(record_id))
        return (record, None) if record else (None, ({"msg": "not found"}, 404))

    class FakeMongo:
        def get_all_records(self, collection, filters=None, fields=None, **kwargs):
            wanted = {str(oid) for oid in filters["_id"]["$in"]}
            snaps = [{"_id": ObjectId(SNAP_A), "record_id": RECORD_A, "type": "image", "data": {"x": 1}}]
            return [s for s in snaps if str(s["_id"]) in wanted]

    monkeypatch.setattr("archihub.api.records.public.load_public", load_public)
    monkeypatch.setattr(public, "_mongo", lambda: FakeMongo())
    return public_records


def test_a_published_records_block_carries_its_records(published):
    blocks = public.hydrate_article([{"type": "uploadedRecords", "content": _records_block(RECORD_A, RECORD_B)}])

    assert blocks[0]["content"] == [
        {"id": RECORD_A, "name": "a.CR2", "type": "image"},
        {"id": RECORD_B, "name": "b.CR2", "type": "image"},
    ]


def test_a_published_snap_block_carries_its_snaps(published):
    fragment = f"""<div data-snaps='[{{"id":"{SNAP_A}","title":"t","view":"image"}}]'>1</div>"""

    blocks = public.hydrate_article([{"type": "snap", "content": fragment}])

    assert blocks[0]["content"] == [
        {"id": SNAP_A, "recordId": RECORD_A, "data": {"x": 1}, "type": "image"}
    ]


def test_a_record_that_is_not_public_is_left_out_of_a_published_block(published):
    published.pop(RECORD_B)

    blocks = public.hydrate_article([{"type": "uploadedRecords", "content": _records_block(RECORD_A, RECORD_B)}])

    assert [r["id"] for r in blocks[0]["content"]] == [RECORD_A]
