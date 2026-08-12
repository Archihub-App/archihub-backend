"""Saved views.

Two of the six routes are unauthenticated, and that is what shapes the tests.
The thumbnail section is the regression for BACKEND_FINDINGS S25: `filesObj` was
client-settable and the thumbnail it named was base64-encoded into a public
response, with no check on the record it pointed at.
"""

from __future__ import annotations

import base64

import pytest
from bson.objectid import ObjectId

from archihub.api.views import services

VIEW_ID = "6a70b833497d4440325c94b1"
OWN_RECORD = "6a70b833497d4440325c94c1"
FOREIGN_RECORD = "6a70b833497d4440325c94c2"


class FakeInsert:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeMongo:
    def __init__(self):
        self.views: dict[str, dict] = {}
        self.by_slug: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.inserted: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []
        self.deleted: list[dict] = []
        self.counts: dict = {}

    def get_record(self, collection, filters=None, fields=None):
        filters = filters or {}
        if collection == "views":
            if "slug" in filters:
                return self.by_slug.get(filters["slug"])
            return self.views.get(str(filters.get("_id")))
        return self.records.get(str(filters.get("_id")))

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return list(self.views.values())

    def count(self, collection, filters=None):
        kind = (filters or {}).get("processing.fileProcessing.type")
        if kind is None:
            return self.counts.get("total", 0)
        return self.counts.get(kind, 0)

    def insert_record(self, collection, record):
        self.inserted.append(record)
        return FakeInsert(ObjectId(VIEW_ID))

    def update_record(self, collection, filters, update):
        self.updates.append((filters, update))

    def delete_record(self, collection, filters):
        self.deleted.append(filters)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_audit", lambda *a, **k: None)
    return fake


@pytest.fixture
def derives(monkeypatch):
    """A working `filesProcessing`: the thumbnail derives successfully.

    Stubbed at `_derive_thumbnail` because the real one goes through the plugin
    interop registry and then re-reads Mongo; the tests that use this are about
    what `create`/`update` do with the *outcome*, not how it is produced. The
    outcome itself is covered separately below.
    """
    monkeypatch.setattr(services, "_derive_thumbnail", lambda attached, user: None)


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    web = tmp_path / "web" / "2024"
    web.mkdir(parents=True)
    (web / "thumb_medium.jpg").write_bytes(b"jpeg-bytes")
    (tmp_path / "outside_medium.jpg").write_bytes(b"not yours")

    class Settings:
        web_files_path = str(tmp_path / "web")

    monkeypatch.setattr(services, "get_settings", lambda: Settings())
    monkeypatch.setattr("archihub.core.files.get_settings", lambda: Settings())
    return tmp_path


def view(**overrides):
    document = {
        "_id": ObjectId(VIEW_ID),
        "name": "Photographs",
        "slug": "photographs",
        "description": "Every photograph",
        "parent": "",
        "root": "fondo",
        "visible": ["foto"],
        "defaultView": "list",
        "filesObj": [{"id": OWN_RECORD, "tag": "thumbnail"}],
    }
    document.update(overrides)
    return document


def image_record(record_id, parent_id=VIEW_ID, access_rights=None, path="2024/thumb"):
    return {
        "_id": ObjectId(record_id),
        "accessRights": access_rights,
        "parent": [{"id": parent_id, "post_type": "view"}] if parent_id else [],
        "processing": {"fileProcessing": {"type": "image", "path": path}},
    }


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


def test_a_views_own_thumbnail_is_served_inline(mongo, media_root):
    mongo.records[OWN_RECORD] = image_record(OWN_RECORD)

    thumbnail = services._thumbnail(view())

    assert thumbnail.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(thumbnail.split(",", 1)[1]) == b"jpeg-bytes"


def test_a_record_not_attached_to_the_view_is_never_served(mongo, media_root):
    """BACKEND_FINDINGS S25.

    `filesObj` used to be client-settable and the thumbnail it named was
    base64-encoded into `GET /views`, which is unauthenticated - so pointing a
    view at any record published that image.
    """
    mongo.records[FOREIGN_RECORD] = image_record(FOREIGN_RECORD, parent_id="another-thing")

    assert services._thumbnail(view(filesObj=[{"id": FOREIGN_RECORD}])) is None


def test_a_restricted_record_is_never_a_thumbnail(mongo, media_root):
    """A thumbnail is published anonymously, whatever the view says."""
    mongo.records[OWN_RECORD] = image_record(OWN_RECORD, access_rights="reserved")

    assert services._thumbnail(view()) is None


def test_an_unprocessed_thumbnail_renders_as_no_image(mongo, media_root):
    record = image_record(OWN_RECORD)
    record["processing"] = {}
    mongo.records[OWN_RECORD] = record

    assert services._thumbnail(view()) is None


def test_a_stored_path_that_climbs_out_is_refused(mongo, media_root):
    mongo.records[OWN_RECORD] = image_record(OWN_RECORD, path="../../outside")

    assert services._thumbnail(view()) is None


def test_a_view_with_no_files_has_no_thumbnail(mongo, media_root):
    assert services._thumbnail(view(filesObj=[])) is None


def test_the_thumbnail_tag_is_preferred_over_the_first_file(mongo, media_root):
    mongo.records[FOREIGN_RECORD] = image_record(FOREIGN_RECORD, path="2024/other")
    mongo.records[OWN_RECORD] = image_record(OWN_RECORD)

    thumbnail = services._thumbnail(
        view(filesObj=[{"id": FOREIGN_RECORD}, {"id": OWN_RECORD, "tag": "thumbnail"}])
    )

    assert thumbnail is not None


def test_the_listing_never_exposes_file_entries(mongo, media_root):
    mongo.views[VIEW_ID] = view()
    mongo.records[OWN_RECORD] = image_record(OWN_RECORD)

    payload, status = services.get_all()

    assert status == 200
    assert set(payload[0]) == {"id", "name", "description", "slug", "thumbnail"}


# ---------------------------------------------------------------------------
# What a client may set
# ---------------------------------------------------------------------------


def test_a_client_cannot_set_files_obj_on_create(mongo):
    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo", "filesObj": [{"id": FOREIGN_RECORD}]},
        "alice",
    )

    assert status == 201
    assert mongo.inserted[0]["filesObj"] == []


def test_a_client_cannot_set_files_obj_on_update(mongo):
    mongo.views[VIEW_ID] = view()

    payload, status = services.update(
        VIEW_ID, {"name": "Renamed", "filesObj": [{"id": FOREIGN_RECORD}]}, "alice"
    )

    assert status == 200
    _filters, update = mongo.updates[0]
    assert "filesObj" not in update
    assert update["name"] == "Renamed"


def test_creating_without_a_required_field_is_refused(mongo):
    payload, status = services.create({"name": "V", "slug": "v"}, "alice")

    assert status == 400
    assert mongo.inserted == []


def test_a_duplicate_slug_is_refused(mongo):
    """The slug is how the public route addresses a view."""
    mongo.by_slug["taken"] = view(slug="taken")

    payload, status = services.create(
        {"name": "V", "slug": "taken", "root": "fondo"}, "alice"
    )

    assert status == 409
    assert mongo.inserted == []


def test_renaming_a_slug_onto_an_existing_one_is_refused(mongo):
    mongo.views[VIEW_ID] = view()
    mongo.by_slug["taken"] = {"_id": ObjectId(FOREIGN_RECORD)}

    payload, status = services.update(VIEW_ID, {"slug": "taken"}, "alice")

    assert status == 409
    assert mongo.updates == []


def test_keeping_your_own_slug_is_not_a_clash(mongo):
    mongo.views[VIEW_ID] = view()
    mongo.by_slug["photographs"] = view()

    payload, status = services.update(VIEW_ID, {"slug": "photographs"}, "alice")

    assert status == 200


def test_an_update_with_nothing_in_it_is_refused(mongo):
    mongo.views[VIEW_ID] = view()

    payload, status = services.update(VIEW_ID, {"unknownField": 1}, "alice")

    assert status == 400
    assert mongo.updates == []


def test_visible_must_be_a_list(mongo):
    mongo.views[VIEW_ID] = view()

    payload, status = services.update(VIEW_ID, {"visible": "foto"}, "alice")

    assert status == 400


# ---------------------------------------------------------------------------
# Thumbnails on write
# ---------------------------------------------------------------------------


class FakeUpload:
    def __init__(self, filename):
        self.filename = filename


@pytest.mark.parametrize("filename", ["evil.exe", "notes.pdf", "archive.zip", "script.svg"])
def test_a_thumbnail_that_is_not_an_image_is_refused(mongo, filename):
    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload(filename)]
    )

    assert status == 400
    assert mongo.inserted == []


def test_more_than_one_thumbnail_is_refused(mongo):
    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"},
        "alice",
        [FakeUpload("a.jpg"), FakeUpload("b.jpg")],
    )

    assert status == 400


def test_an_image_thumbnail_is_attached_and_recorded(mongo, derives, monkeypatch):
    attached = []

    def fake_attach(view_id, incoming, user, resource=None):
        attached.append((view_id, resource))
        return [{"id": OWN_RECORD, "tag": "thumbnail"}]

    monkeypatch.setattr("archihub.api.records.storage.attach_files", fake_attach)

    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload("cover.jpg")]
    )

    assert status == 201
    # Inserted first, so the file has a real parent id to attach to.
    assert attached[0][0] == VIEW_ID
    _filters, update = mongo.updates[0]
    assert update["filesObj"] == [{"id": OWN_RECORD, "tag": "thumbnail"}]


def test_a_failed_attachment_rolls_the_view_back(mongo, monkeypatch):
    """A save the user was told had failed must not leave a view behind."""
    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("archihub.api.records.storage.attach_files", explode)

    with pytest.raises(RuntimeError):
        services.create(
            {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload("cover.jpg")]
        )

    assert mongo.deleted


def test_replacing_a_thumbnail_detaches_the_old_one(mongo, derives, monkeypatch):
    detached = []
    monkeypatch.setattr(
        "archihub.api.records.storage.detach_from_parent",
        lambda record_id, parent_id, user: detached.append((record_id, parent_id)),
    )
    monkeypatch.setattr(
        "archihub.api.records.storage.attach_files",
        lambda *a, **k: [{"id": FOREIGN_RECORD, "tag": "thumbnail"}],
    )
    mongo.views[VIEW_ID] = view()

    payload, status = services.update(VIEW_ID, {}, "alice", [FakeUpload("new.png")])

    assert status == 200
    assert detached == [(OWN_RECORD, VIEW_ID)]


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


def test_deleting_a_view_retires_its_thumbnail(mongo, monkeypatch):
    detached = []
    monkeypatch.setattr(
        "archihub.api.records.storage.detach_from_parent",
        lambda record_id, parent_id, user: detached.append(record_id),
    )
    mongo.views[VIEW_ID] = view()

    payload, status = services.delete(VIEW_ID, "alice")

    assert status == 200
    assert detached == [OWN_RECORD]
    assert mongo.deleted


def test_deleting_a_view_that_does_not_exist_is_a_404(mongo):
    """The original deleted nothing and reported success."""
    payload, status = services.delete(VIEW_ID, "alice")

    assert status == 404
    assert mongo.deleted == []


def test_a_malformed_view_id_is_a_404(mongo):
    assert services.delete("not-an-id", "alice")[1] == 404
    assert services.get("not-an-id")[1] == 404
    assert services.update("not-an-id", {"name": "x"}, "alice")[1] == 404


# ---------------------------------------------------------------------------
# View info (public)
# ---------------------------------------------------------------------------


@pytest.fixture
def types(monkeypatch):
    catalogue = {
        "foto": {
            "slug": "foto",
            "name": "Photograph",
            "description": "A photograph",
            "icon": "camera",
            "form": "foto-form",
            "metadata": {"slug": "foto-form", "name": "Photograph form", "fields": [{"destiny": "t"}]},
        }
    }
    monkeypatch.setattr(
        "archihub.api.types.services.get_by_slug", lambda slug: catalogue.get(slug)
    )
    monkeypatch.setattr("archihub.api.types.services.get_parents", lambda pt: [])
    monkeypatch.setattr("archihub.api.types.services.get_icon", lambda slug: "folder")
    return catalogue


def test_an_unknown_slug_is_a_404_not_a_500(mongo, types):
    """The original read the view's fields before checking it had found one."""
    payload, status = services.get_view_info("nonexistent")

    assert status == 404


def test_view_info_describes_its_content_types(mongo, types):
    mongo.by_slug["photographs"] = view()

    payload, status = services.get_view_info("photographs")

    assert status == 200
    assert payload["types"][0]["name"] == "Photograph"
    assert payload["forms"]["forms"] == [{"slug": "foto-form", "name": "Photograph form"}]
    assert "visible" not in payload


def test_a_view_naming_a_deleted_content_type_still_renders(mongo, types):
    """The original subscripted the lookup and took the whole screen down."""
    mongo.by_slug["photographs"] = view(visible=["foto", "removed"])

    payload, status = services.get_view_info("photographs")

    assert status == 200
    assert [t["slug"] for t in payload["types"]] == ["foto"]


def test_file_counts_exclude_restricted_material(mongo, types):
    """PUBLIC route. The original counted everything with no filter at all."""
    mongo.by_slug["photographs"] = view()
    captured = []

    def counting(collection, filters=None):
        captured.append(filters)
        return 3

    mongo.count = counting

    payload, status = services.get_view_info("photographs")

    assert status == 200
    assert all("$and" in f for f in captured)
    assert all(f.get("status") == {"$ne": "deleted"} for f in captured)


def test_counts_are_ranked_with_the_commonest_first(mongo, types):
    mongo.by_slug["photographs"] = view()
    mongo.counts = {"image": 9, "document": 4, "audio": 1, "total": 14}

    payload, _status = services.get_view_info("photographs")

    assert [entry["_id"] for entry in payload["files"]["data"][:2]] == ["image", "document"]


# ---------------------------------------------------------------------------
# Deriving the thumbnail
#
# Attaching the file is NOT enough to make it visible. `_thumbnail` serves
# `<web_files>/<path>_medium.jpg`, which exists only after processing - and it
# treats a missing derivative as "not ready yet" and returns None. So a view
# whose image was merely stored renders with no image, permanently, and nothing
# anywhere reports a problem. That is what this section exists to prevent.
#
# The two automatic paths in `filesProcessing` cannot cover it: both select
# RESOURCES by content type, and a view is not a resource.
# ---------------------------------------------------------------------------


@pytest.fixture
def attaches(monkeypatch):
    def fake_attach(view_id, incoming, user, resource=None):
        return [{"id": OWN_RECORD, "tag": "thumbnail"}]

    monkeypatch.setattr("archihub.api.records.storage.attach_files", fake_attach)
    detached = []
    monkeypatch.setattr(
        "archihub.api.records.storage.detach_from_parent",
        lambda record_id, parent_id, user: detached.append(record_id) or True,
    )
    return detached


def _processing(kind):
    """A records lookup answering with a given fileProcessing type."""
    return {"processing": {"fileProcessing": {"type": kind, "path": "2024/thumb"}}}


def test_the_upload_is_processed_before_the_view_is_reported_created(
    mongo, attaches, monkeypatch
):
    called = []
    monkeypatch.setattr(
        "archihub.plugins.framework.interop.derive_web_versions",
        lambda record: called.append(record) or True,
    )
    mongo.records[OWN_RECORD] = _processing("image") | {"mime": "image/jpeg", "filepath": "p"}

    _payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload("cover.jpg")]
    )

    assert status == 201
    assert called, "the thumbnail was stored but never made renderable"


def test_a_view_whose_image_cannot_be_processed_is_not_created(mongo, attaches, monkeypatch):
    """Better no view than one with a permanently blank card the operator was
    told had saved."""
    monkeypatch.setattr(
        "archihub.plugins.framework.interop.derive_web_versions",
        lambda record: True,
    )
    # Processing ran but produced something that is not an image.
    mongo.records[OWN_RECORD] = _processing("document") | {"mime": "image/jpeg", "filepath": "p"}

    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload("cover.jpg")]
    )

    assert status == 500
    assert payload["msg"] == services._("File processing failed for image")
    assert mongo.deleted, "the half-made view was left behind"


def test_an_inactive_files_processing_plugin_is_reported_not_crashed(
    mongo, attaches, monkeypatch
):
    """The capability follows plugin activation, so this is a real deployment
    state - not an internal error."""
    from archihub.plugins.framework import interop

    def unavailable(record):
        raise interop.CapabilityUnavailable("no provider")

    monkeypatch.setattr(interop, "derive_web_versions", unavailable)
    mongo.records[OWN_RECORD] = {"mime": "image/jpeg", "filepath": "p"}

    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload("cover.jpg")]
    )

    assert status == 500
    assert payload["msg"] == services._("File processing failed for image")


def test_a_processing_crash_does_not_escape_as_a_500_traceback(mongo, attaches, monkeypatch):
    monkeypatch.setattr(
        "archihub.plugins.framework.interop.derive_web_versions",
        lambda record: (_ for _ in ()).throw(RuntimeError("imagemagick died")),
    )
    mongo.records[OWN_RECORD] = {"mime": "image/jpeg", "filepath": "p"}

    payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", [FakeUpload("cover.jpg")]
    )

    assert status == 500
    assert payload["msg"] == services._("File processing failed for image")


def test_a_view_with_no_upload_needs_no_processing(mongo, monkeypatch):
    """Only the image path touches the plugin; a plain view must not require
    filesProcessing to be active at all."""
    def explode(record):
        raise AssertionError("processing must not be attempted without an upload")

    monkeypatch.setattr("archihub.plugins.framework.interop.derive_web_versions", explode)

    _payload, status = services.create(
        {"name": "V", "slug": "v", "root": "fondo"}, "alice", None
    )

    assert status == 201


def test_replacing_a_thumbnail_processes_the_new_one(mongo, attaches, monkeypatch):
    called = []
    monkeypatch.setattr(
        "archihub.plugins.framework.interop.derive_web_versions",
        lambda record: called.append(record) or True,
    )
    mongo.views[VIEW_ID] = view()
    mongo.records[OWN_RECORD] = _processing("image") | {"mime": "image/jpeg", "filepath": "p"}

    _payload, status = services.update(
        VIEW_ID, {"name": "V"}, "alice", [FakeUpload("cover.jpg")]
    )

    assert status == 200
    assert called, "the replacement image was stored but never made renderable"
