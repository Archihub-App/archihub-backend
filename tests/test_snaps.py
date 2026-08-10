"""Snaps: user-made clips of a record.

Two properties carry the weight here. **A snap is not a capability** - owning
one is not permission to read what it points at, and creating one requires being
able to see the record in the first place (BACKEND_FINDINGS S22). And the stored
coordinates are validated at creation, because they are read back later by other
code, sometimes on another user's screen.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.snaps import render, services

SNAP_ID = "6a70b833497d4440325c94a1"
RECORD_ID = "6a70b833497d4440325c94b1"
OTHER_ID = "6a70b833497d4440325c94b2"


class FakeInsert:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeMongo:
    def __init__(self):
        self.snaps: dict[str, dict] = {}
        self.inserted: list[dict] = []
        self.deleted: list[dict] = []

    def get_record(self, collection, filters=None, fields=None):
        return self.snaps.get(str((filters or {}).get("_id")))

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        rows = [
            s for s in self.snaps.values()
            if s.get("user") == (filters or {}).get("user")
            and s.get("type") == (filters or {}).get("type")
        ]
        if skip:
            rows = rows[skip:]
        if limit:
            rows = rows[:limit]
        return rows

    def count(self, collection, filters=None):
        return len(
            [
                s for s in self.snaps.values()
                if s.get("user") == (filters or {}).get("user")
                and s.get("type") == (filters or {}).get("type")
            ]
        )

    def insert_record(self, collection, record):
        self.inserted.append(record)
        return FakeInsert(ObjectId(SNAP_ID))

    def delete_record(self, collection, filters):
        self.deleted.append(filters)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_audit", lambda *a, **k: None)
    return fake


@pytest.fixture
def visible_record(monkeypatch):
    """The caller can see the record, and it is a document."""
    record = {
        "_id": ObjectId(RECORD_ID),
        "name": "scan.pdf",
        "displayName": "A scanned folio",
        "processing": {"fileProcessing": {"type": "document", "path": "2024/03/doc"}},
    }
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible", lambda rid, user: (record, None)
    )
    return record


@pytest.fixture
def invisible_record(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible",
        lambda rid, user: (None, ({"msg": "Record does not exist"}, 404)),
    )


def box(**overrides):
    data = {"bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}, "page": 2}
    data.update(overrides)
    return data


def snap(user="alice", snap_type="document", data=None):
    return {
        "_id": ObjectId(SNAP_ID),
        "user": user,
        "record_id": RECORD_ID,
        "type": snap_type,
        "data": data if data is not None else box(),
    }


# ---------------------------------------------------------------------------
# Creating requires being able to see the record
# ---------------------------------------------------------------------------


def test_a_snap_of_a_visible_record_is_created(mongo, visible_record):
    payload, status = services.create("alice", {
        "record_id": RECORD_ID, "type": "document", "data": box()
    })

    assert status == 201
    assert mongo.inserted[0]["user"] == "alice"
    assert mongo.inserted[0]["record_id"] == RECORD_ID


def test_a_snap_of_an_invisible_record_is_refused(mongo, invisible_record):
    """BACKEND_FINDINGS S22.

    The original fetched the record by id with no access check at all, so any
    authenticated user could snap anything in the archive - and store its
    filename along with it.
    """
    payload, status = services.create("mallory", {
        "record_id": RECORD_ID, "type": "document", "data": box()
    })

    assert status == 404
    assert mongo.inserted == []


def test_the_record_name_comes_from_the_loaded_record(mongo, visible_record):
    """And so cannot be supplied by the caller."""
    payload, status = services.create("alice", {
        "record_id": RECORD_ID, "type": "document", "data": box(),
        "record_name": "something else", "user": "bob",
    })

    assert mongo.inserted[0]["record_name"] == "A scanned folio"
    assert mongo.inserted[0]["user"] == "alice"


def test_a_snap_type_the_record_is_not_is_refused(mongo, visible_record):
    """A time range out of a scanned page can never be rendered."""
    payload, status = services.create("alice", {
        "record_id": RECORD_ID, "type": "audio", "data": {"begin": 1.0, "end": 2.0}
    })

    assert status == 400
    assert mongo.inserted == []


def test_an_unknown_snap_type_is_refused(mongo, visible_record):
    payload, status = services.create("alice", {
        "record_id": RECORD_ID, "type": "hologram", "data": box()
    })

    assert status == 400


def test_creating_without_a_record_id_is_refused(mongo, visible_record):
    payload, status = services.create("alice", {"type": "document", "data": box()})

    assert status == 400


# ---------------------------------------------------------------------------
# The stored coordinates
# ---------------------------------------------------------------------------


def test_a_valid_box_is_normalised_to_floats():
    data, error = services.validate_data("image", {"bbox": {"x": 0, "y": 0, "width": 1, "height": 1}})

    assert error is None
    assert data == {"bbox": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}}


@pytest.mark.parametrize(
    "bbox",
    [
        {"x": -0.1, "y": 0, "width": 0.5, "height": 0.5},
        {"x": 0, "y": 0, "width": 1.5, "height": 0.5},
        {"x": 0.8, "y": 0, "width": 0.5, "height": 0.5},
        {"x": 0, "y": 0, "width": 0, "height": 0.5},
        {"x": 0, "y": 0, "width": 0.5},
        {"x": "0", "y": 0, "width": 0.5, "height": 0.5},
    ],
)
def test_a_box_outside_the_image_is_refused(bbox):
    """These become arithmetic on an image; PIL pads a bad crop with black."""
    data, error = services.validate_data("image", {"bbox": bbox})

    assert data is None
    assert error


def test_a_document_snap_needs_a_page(mongo):
    data, error = services.validate_data("document", {"bbox": {"x": 0, "y": 0, "width": 1, "height": 1}})

    assert data is None


@pytest.mark.parametrize("page", [0, -1, 1.5, "2", True])
def test_a_page_that_is_not_a_positive_integer_is_refused(page):
    """Pages are 1-indexed; the original passed page 0 through as index -1."""
    data, error = services.validate_data("document", box(page=page))

    assert data is None


def test_a_valid_time_range_is_normalised():
    data, error = services.validate_data("audio", {"begin": 1, "end": 3.5})

    assert error is None
    assert data == {"begin": 1.0, "end": 3.5}


@pytest.mark.parametrize(
    "payload",
    [
        {"begin": 3.0, "end": 1.0},
        {"begin": -1.0, "end": 3.0},
        {"begin": 1.0, "end": 1.0},
        {"begin": 1.0},
        {"begin": "one", "end": "two"},
    ],
)
def test_an_unusable_time_range_is_refused(payload):
    data, error = services.validate_data("video", payload)

    assert data is None


def test_data_that_is_not_an_object_is_refused():
    assert services.validate_data("image", "everything")[0] is None
    assert services.validate_data("image", None)[0] is None


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_a_snap_belongs_to_the_person_who_made_it(mongo):
    mongo.snaps[SNAP_ID] = snap(user="alice")

    found, error = services.load_own(SNAP_ID, "alice")

    assert error is None
    assert found["user"] == "alice"


def test_another_users_snap_is_refused(mongo):
    mongo.snaps[SNAP_ID] = snap(user="alice")

    found, error = services.load_own(SNAP_ID, "bob")

    assert found is None
    assert error[1] == services.LEGACY_ROLE_FAILURE_STATUS


def test_not_even_an_administrator_reads_someone_elses_snap(mongo, monkeypatch):
    """A snap is a personal working note. The original made the same choice."""
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: True)
    mongo.snaps[SNAP_ID] = snap(user="alice")

    found, _error = services.load_own(SNAP_ID, "root")

    assert found is None


def test_a_missing_snap_is_a_404(mongo):
    found, error = services.load_own(SNAP_ID, "alice")

    assert error[1] == 404


def test_a_malformed_snap_id_is_a_404(mongo):
    found, error = services.load_own("not-an-id", "alice")

    assert error[1] == 404


def test_deleting_your_own_snap_removes_it(mongo):
    mongo.snaps[SNAP_ID] = snap(user="alice")

    payload, status = services.delete(SNAP_ID, "alice")

    assert status == 204
    assert mongo.deleted


def test_deleting_another_users_snap_is_refused(mongo):
    mongo.snaps[SNAP_ID] = snap(user="alice")

    payload, status = services.delete(SNAP_ID, "bob")

    assert status == services.LEGACY_ROLE_FAILURE_STATUS
    assert mongo.deleted == []


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_listing_returns_only_the_callers_snaps_of_that_type(mongo):
    mongo.snaps["a"] = {"_id": ObjectId(SNAP_ID), "user": "alice", "type": "document"}
    mongo.snaps["b"] = {"_id": ObjectId(OTHER_ID), "user": "bob", "type": "document"}
    mongo.snaps["c"] = {"_id": ObjectId(RECORD_ID), "user": "alice", "type": "image"}

    payload, status = services.list_for_user("alice", {"type": "document", "page": 0})

    assert status == 200
    assert payload["total"] == 1
    assert len(payload["results"]) == 1


def test_listing_an_unknown_type_is_refused(mongo):
    payload, status = services.list_for_user("alice", {"type": "hologram"})

    assert status == 400


def test_a_negative_page_is_treated_as_the_first(mongo):
    payload, status = services.list_for_user("alice", {"type": "document", "page": -3})

    assert status == 200


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_rendering_an_unknown_snap_type_is_refused():
    with pytest.raises(render.RenderFailed) as exc:
        render.render({"type": "hologram", "data": {}}, {"processing": {}})

    assert exc.value.status_code == 400


def test_a_document_snap_crops_the_requested_page(monkeypatch):
    """The page comes through the viewer, so its allowlist and root check apply."""
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, "JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    seen = {}

    def fake_pages(record, pages, size):
        seen["pages"] = pages
        seen["size"] = size
        return [{"filename": "p.jpg", "data": encoded}]

    monkeypatch.setattr("archihub.api.records.viewers.page_images", fake_pages)

    response = render.render(snap(), {"processing": {}})

    assert response.media_type == "image/jpeg"
    # Page 2 on the wire is index 1 in the rendered directory.
    assert seen["pages"] == [1]
    assert seen["size"] == render.DOCUMENT_PAGE_SIZE


def test_a_crop_of_an_undecodable_page_is_a_404(monkeypatch):
    import base64

    monkeypatch.setattr(
        "archihub.api.records.viewers.page_images",
        lambda record, pages, size: [{"data": base64.b64encode(b"not an image").decode()}],
    )

    with pytest.raises(render.RenderFailed) as exc:
        render.render(snap(), {"processing": {}})

    assert exc.value.status_code == 404


def test_a_box_that_crops_to_nothing_is_refused(monkeypatch):
    """A snap stored before the validation existed can still be in the database."""
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, "JPEG")
    monkeypatch.setattr(
        "archihub.api.records.viewers.page_images",
        lambda record, pages, size: [
            {"data": base64.b64encode(buffer.getvalue()).decode()}
        ],
    )
    degenerate = snap(data={"bbox": {"x": 0.5, "y": 0.5, "width": 0.0001, "height": 0.0001}, "page": 1})

    with pytest.raises(render.RenderFailed) as exc:
        render.render(degenerate, {"processing": {}})

    assert exc.value.status_code == 400
