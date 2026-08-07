"""Reading records.

The access rule is the interesting part: a file is visible through the resources
it belongs to, so a record with no restriction of its own can still be reserved
because of where it is filed.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.records import access, media, services

RECORD_ID = "6a70b833497d4440325c94b1"
RESOURCE_ID = "6a70b833497d4440325c94c1"
ANCESTOR_ID = "6a70b833497d4440325c94d1"


class FakeMongo:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.resources: dict[str, dict] = {}
        self.types: list[dict] = []
        self.user: dict | None = None
        self.updates: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        key = str((filters or {}).get("_id"))
        if collection == "resources":
            return self.resources.get(key)
        if collection == "users":
            return self.user
        return self.records.get(key)

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "post_types":
            return list(self.types)
        if collection == "resources":
            wanted = {str(o) for o in (filters or {}).get("_id", {}).get("$in", [])}
            return [r for k, r in self.resources.items() if k in wanted]
        rows = list(self.records.values())
        ids = (filters or {}).get("_id", {})
        if isinstance(ids, dict) and "$in" in ids:
            wanted = {str(o) for o in ids["$in"]}
            rows = [r for r in rows if str(r["_id"]) in wanted]
        if "processing.fileProcessing.type" in (filters or {}):
            wanted_type = filters["processing.fileProcessing.type"]
            rows = [
                r for r in rows
                if ((r.get("processing") or {}).get("fileProcessing") or {}).get("type") == wanted_type
            ]
        if skip:
            rows = rows[skip:]
        if limit:
            rows = rows[:limit]
        return rows

    def count(self, collection, filters=None):
        return len(self.records)

    def update_record(self, collection, filters, update_model):
        self.updates.append((filters, update_model))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(access, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.access._mongo", lambda: fake)
    monkeypatch.setattr(services, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(services, "_call_hook", lambda *a, **k: None)
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


@pytest.fixture
def as_admin(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r == "admin")


def record(**overrides):
    return {
        "_id": ObjectId(RECORD_ID),
        "name": "scan.jpg",
        "filepath": "2024/03/01/abc.jpg",
        "accessRights": None,
        "parent": [],
        "parents": [],
        **overrides,
    }


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def test_an_unrestricted_record_is_visible(mongo):
    mongo.records[RECORD_ID] = record()
    mongo.user = {"accessRights": []}

    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == 200


def test_a_restricted_record_is_refused(mongo):
    mongo.records[RECORD_ID] = record(accessRights="reserved")
    mongo.user = {"accessRights": ["public"]}

    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == services.LEGACY_ROLE_FAILURE_STATUS


def test_holding_the_right_grants_access(mongo):
    mongo.records[RECORD_ID] = record(accessRights="reserved")
    mongo.user = {"accessRights": ["reserved"]}

    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == 200


def test_an_administrator_may_read_a_restricted_record(mongo, as_admin):
    """THE fix (F27).

    The original wrote `has_right(current_user, 'admin')` - but `has_right`
    resolves **access rights**, and `admin` is a *role*. No instance defines an
    access right by that name, so the intended administrator bypass never
    existed and administrators were refused. It fails closed, so it is a
    usability defect rather than a hole - but it was never true that an
    administrator could always read a record.
    """
    mongo.records[RECORD_ID] = record(accessRights="reserved")
    mongo.user = {"accessRights": []}

    _payload, status = services.get_by_id(RECORD_ID, "admin")
    assert status == 200


def test_a_record_inherits_its_parents_restriction(mongo):
    """A file reachable from a reserved series is reserved, however it was
    reached."""
    mongo.records[RECORD_ID] = record(parent=[{"id": RESOURCE_ID}])
    mongo.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID), "accessRights": "reserved", "parents": [],
    }
    mongo.user = {"accessRights": ["public"]}

    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == services.LEGACY_ROLE_FAILURE_STATUS


def test_a_restriction_inherited_by_the_parent_from_its_own_ancestor_applies(mongo):
    mongo.records[RECORD_ID] = record(parent=[{"id": RESOURCE_ID}])
    mongo.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID),
        "accessRights": None,
        "parents": [{"id": ANCESTOR_ID}],
    }
    mongo.resources[ANCESTOR_ID] = {"_id": ObjectId(ANCESTOR_ID), "accessRights": "reserved"}
    mongo.user = {"accessRights": ["public"]}

    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == services.LEGACY_ROLE_FAILURE_STATUS


def test_a_dangling_parent_reference_does_not_deny_access(mongo):
    """It cannot grant access and it must not deny it; the record's other
    parents decide."""
    mongo.records[RECORD_ID] = record(parent=[{"id": "6a70b833497d4440325c94ff"}])
    mongo.user = {"accessRights": []}

    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == 200


def test_a_missing_record_is_404(mongo):
    _payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == 404


def test_a_malformed_id_is_404_not_500(mongo):
    _payload, status = services.get_by_id("not-an-object-id", "alice")
    assert status == 404


def test_a_permission_failure_is_not_reported_as_a_server_error(mongo):
    """F27's other half.

    Eight functions opened with `if status != 200: return {...}, 500`, so a 404
    and a 401 both reached the client as 500. The message survived; the status
    did not.
    """
    mongo.records[RECORD_ID] = record(accessRights="reserved")
    mongo.user = {"accessRights": []}

    _record, error = services.load_visible(RECORD_ID, "alice")
    assert error[1] == services.LEGACY_ROLE_FAILURE_STATUS


# ---------------------------------------------------------------------------
# What the detail view returns
# ---------------------------------------------------------------------------


def test_the_storage_path_never_reaches_a_browser(mongo):
    mongo.records[RECORD_ID] = record()
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice")
    assert "filepath" not in payload


def test_an_internal_caller_may_ask_for_the_storage_path(mongo):
    mongo.records[RECORD_ID] = record()
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice", full_fields=True)
    assert payload["filepath"] == "2024/03/01/abc.jpg"


def test_parents_are_annotated_with_their_title_and_icon(mongo):
    mongo.records[RECORD_ID] = record(parent=[{"id": RESOURCE_ID}])
    mongo.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID),
        "accessRights": None,
        "parents": [],
        "post_type": "carpeta",
        "metadata": {"firstLevel": {"title": "Expediente 12"}},
    }
    mongo.types = [{"slug": "carpeta", "icon": "folder"}]
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice")

    assert payload["parent"][0]["name"] == "Expediente 12"
    assert payload["parent"][0]["icon"] == "folder"


def test_a_parent_with_no_title_does_not_take_the_record_down(mongo):
    """The original subscripted `metadata.firstLevel.title` directly."""
    mongo.records[RECORD_ID] = record(parent=[{"id": RESOURCE_ID}])
    mongo.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID), "accessRights": None, "parents": [], "metadata": {},
    }
    mongo.user = {"accessRights": []}

    payload, status = services.get_by_id(RECORD_ID, "alice")
    assert status == 200
    assert payload["parent"][0]["name"]


def test_a_stale_parent_is_dropped_from_the_breadcrumb(mongo):
    mongo.records[RECORD_ID] = record(parent=[{"id": "6a70b833497d4440325c94ff"}])
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice")
    assert payload["parent"] == []


def test_the_ancestry_list_is_not_returned(mongo):
    mongo.records[RECORD_ID] = record(parents=[{"id": "x"}])
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice")
    assert "parents" not in payload


# ---------------------------------------------------------------------------
# Processing summary
# ---------------------------------------------------------------------------


def test_raw_plugin_output_is_not_shipped_to_the_client(mongo):
    """The stored block holds complete OCR page trees and transcription
    segments, which the detail view neither shows nor should send."""
    mongo.records[RECORD_ID] = record(
        processing={
            "fileProcessing": {"type": "image", "path": "a/b", "pages": ["...huge..."]},
        }
    )
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice")

    assert payload["processing"]["fileProcessing"] == {"type": "image"}


def test_the_dzi_descriptor_survives_because_the_viewer_needs_it(mongo):
    mongo.records[RECORD_ID] = record(
        processing={"fileProcessing": {"type": "image", "dzi": "a.dzi", "cloud": True}}
    )
    mongo.user = {"accessRights": []}

    payload, _status = services.get_by_id(RECORD_ID, "alice")
    assert payload["processing"]["fileProcessing"]["dzi"] == "a.dzi"
    assert payload["processing"]["fileProcessing"]["cloud"] is True


def test_exif_is_reduced_to_a_presentable_subset():
    """The full block routinely carries GPS coordinates, camera serial numbers
    and owner names - none of which an archive means to publish with a scan."""
    kept = services.important_exif({
        "Make": "Canon",
        "GPSLatitude": "4.6097",
        "GPSLongitude": "-74.0817",
        "SerialNumber": "123456",
        "OwnerName": "Someone",
    })

    assert kept == {"Make": "Canon"}


def test_exif_of_the_wrong_shape_is_dropped():
    assert services.important_exif("not a dict") == {}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filters",
    [
        {"$where": "1==1"},
        {"a": {"$function": {}}},
        {"$or": [{"b": {"$expr": 1}}]},
        {"a": [{"$accumulator": 1}]},
    ],
)
def test_server_side_javascript_is_refused_at_any_depth(mongo, filters):
    _payload, status = services.get_by_filters({"filters": filters, "page": 0}, "admin")
    assert status == 400


def test_an_ordinary_operator_is_still_usable(mongo):
    mongo.records[RECORD_ID] = record()
    payload, status = services.get_by_filters(
        {"filters": {"size": {"$gt": 10}}, "page": 0}, "admin"
    )
    assert status == 200
    assert isinstance(payload, list)


def test_a_listing_never_returns_the_storage_path(mongo):
    mongo.records[RECORD_ID] = record()
    payload, _status = services.get_by_filters({"filters": {}, "page": 0}, "admin")
    assert "filepath" not in payload[0]


def test_no_matches_is_an_empty_list_not_a_404(mongo):
    """The legacy 404 made "nothing matched" indistinguishable from a wrong
    endpoint, and broke pagination past the last page."""
    payload, status = services.get_by_filters({"filters": {}, "page": 0}, "admin")
    assert (status, payload) == (200, [])


def test_filters_are_required(mongo):
    _payload, status = services.get_by_filters({"page": 0}, "admin")
    assert status == 400


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------


@pytest.fixture
def gallery(mongo):
    mongo.resources[RESOURCE_ID] = {
        "_id": ObjectId(RESOURCE_ID),
        "accessRights": None,
        "parents": [],
        "filesObj": [
            {"id": "6a70b833497d4440325c9401", "order": 2},
            {"id": "6a70b833497d4440325c9402", "order": 0},
            {"id": "6a70b833497d4440325c9403", "order": 1},
        ],
    }
    for suffix in ("01", "02", "03"):
        rid = f"6a70b833497d4440325c94{suffix}"
        mongo.records[rid] = record(
            _id=ObjectId(rid), processing={"fileProcessing": {"type": "image", "path": "p"}}
        )
    mongo.user = {"accessRights": []}
    return mongo


def test_the_gallery_respects_the_curators_order(gallery):
    """The original keyed its order map by the resource's string ids and looked
    it up with the record's ObjectId, so nothing ever matched and galleries came
    back in Mongo's natural order."""
    payload, status = services.get_by_gallery_index({"id": RESOURCE_ID, "index": 0}, "alice")

    assert status == 200
    assert payload["_id"]["$oid"] == "6a70b833497d4440325c9402"


def test_an_index_past_the_end_is_404(gallery):
    _payload, status = services.get_by_gallery_index({"id": RESOURCE_ID, "index": 99}, "alice")
    assert status == 404


@pytest.mark.parametrize("index", [-1, "2", True, None])
def test_an_unusable_index_is_400(gallery, index):
    _payload, status = services.get_by_gallery_index({"id": RESOURCE_ID, "index": index}, "alice")
    assert status == 400


def test_a_missing_resource_is_404(gallery):
    _payload, status = services.get_by_gallery_index(
        {"id": "6a70b833497d4440325c94ff", "index": 0}, "alice"
    )
    assert status == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_only_the_display_fields_may_be_set(mongo):
    """The original passed the caller's whole body into `RecordUpdate`, which
    also declares `parent`, `parents`, `processing` and `status` - so a display
    rename could re-file the record or overwrite a plugin's results."""
    mongo.records[RECORD_ID] = record()

    services.update_record_by_id(
        RECORD_ID,
        "alice",
        {
            "displayName": "Portada",
            "status": "deleted",
            "parent": [{"id": "elsewhere"}],
            "processing": {},
        },
    )

    _filters, written = mongo.updates[0]
    assert set(written) == {"displayName", "updatedBy", "updatedAt"}


def test_public_access_is_stored_as_none(mongo):
    mongo.records[RECORD_ID] = record()
    services.update_record_by_id(RECORD_ID, "alice", {"accessRights": "public"})

    _filters, written = mongo.updates[0]
    assert written["accessRights"] is None


def test_an_update_with_nothing_usable_is_400(mongo):
    mongo.records[RECORD_ID] = record()
    _payload, status = services.update_record_by_id(RECORD_ID, "alice", {"status": "deleted"})

    assert status == 400
    assert mongo.updates == []


def test_updating_a_missing_record_is_404(mongo):
    _payload, status = services.update_record_by_id(RECORD_ID, "alice", {"displayName": "x"})
    assert status == 404


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------


def test_a_record_never_favourited_reports_zero(mongo):
    mongo.records[RECORD_ID] = record()
    assert services.get_fav_count(RECORD_ID) == ({"favCount": 0}, 200)


def test_favcount_of_a_missing_record_is_404(mongo):
    _payload, status = services.get_fav_count(RECORD_ID)
    assert status == 404


# ---------------------------------------------------------------------------
# Derivatives
# ---------------------------------------------------------------------------


def test_an_unprocessed_record_says_so(mongo):
    """The original's guard subscripted the very key it had just established
    was absent, so this raised KeyError and reached the client as a 500 with
    the raw key name."""
    with pytest.raises(media.NotStreamable):
        media.derivative_of({})


def test_a_record_of_an_unstreamable_kind_says_so():
    with pytest.raises(media.NotStreamable):
        media.derivative_of({"processing": {"fileProcessing": {"type": "document", "path": "a"}}})


def test_a_processed_record_with_no_path_says_so():
    with pytest.raises(media.NotStreamable):
        media.derivative_of({"processing": {"fileProcessing": {"type": "image"}}})


@pytest.mark.parametrize(
    "kind,size,suffix",
    [
        ("video", "large", ".mp4"),
        ("audio", "large", ".mp3"),
        ("image", "large", "_large.jpg"),
        ("image", "medium", "_medium.jpg"),
        ("image", "small", "_small.jpg"),
    ],
)
def test_each_kind_resolves_to_its_derivative(monkeypatch, tmp_path, kind, size, suffix):
    settings = media.get_settings()
    monkeypatch.setattr(settings, "web_files_path", str(tmp_path), raising=False)
    monkeypatch.setattr(media, "get_settings", lambda: settings)

    path, resolved_kind = media.derivative_of(
        {"processing": {"fileProcessing": {"type": kind, "path": "2024/a"}}}, size
    )

    assert resolved_kind == kind
    assert path.name == "a" + suffix


def test_an_unknown_size_falls_back_to_large(monkeypatch, tmp_path):
    settings = media.get_settings()
    monkeypatch.setattr(settings, "web_files_path", str(tmp_path), raising=False)
    monkeypatch.setattr(media, "get_settings", lambda: settings)

    path, _kind = media.derivative_of(
        {"processing": {"fileProcessing": {"type": "image", "path": "a"}}}, "enormous"
    )
    assert path.name.endswith("_large.jpg")


def test_a_stored_path_cannot_escape_the_media_root(monkeypatch, tmp_path):
    """Derivative paths come out of the database."""
    from archihub.core import files as filestore

    settings = media.get_settings()
    monkeypatch.setattr(settings, "web_files_path", str(tmp_path), raising=False)
    monkeypatch.setattr(media, "get_settings", lambda: settings)

    with pytest.raises(filestore.UnsupportedFile):
        media.derivative_of(
            {"processing": {"fileProcessing": {"type": "image", "path": "../../etc/passwd"}}}
        )


def test_no_fragment_bounds_means_no_range_requested():
    assert media.parse_fragment_bounds(None, None) is None


def test_valid_fragment_bounds_are_parsed():
    assert media.parse_fragment_bounds("100", "250.5") == (100.0, 250.5)


@pytest.mark.parametrize("start,end", [("a", "b"), (None, 100), (-1, 10), (100, 100), (100, 50)])
def test_an_unusable_fragment_range_is_rejected(start, end):
    with pytest.raises(ValueError):
        media.parse_fragment_bounds(start, end)
