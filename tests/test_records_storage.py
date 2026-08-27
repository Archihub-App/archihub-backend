"""Attaching files to a resource.

Every upload in the system goes through this, and it is what the resources
write path is blocked on. The interesting behaviour is deduplication by content
hash - archives receive the same scan against several catalogue entries
routinely, and it must be stored once.
"""

from __future__ import annotations

import io

import pytest
from bson.objectid import ObjectId

from archihub.api.records import storage
from archihub.core import files as filestore

RESOURCE_ID = "6a70b833497d4440325c94b1"
OTHER_RESOURCE = "6a70b833497d4440325c94b2"


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
            return next((r for r in self.records if r["hash"] == filters["hash"]), None)
        return next((r for r in self.records if r["_id"] == filters.get("_id")), None)

    def insert_record(self, collection, record):
        self._next += 1
        record_id = f"rec{self._next}"
        self.records.append({**record, "_id": record_id})
        return Inserted(record_id)

    def update_record(self, collection, filters, update_model):
        self.updates.append((filters, update_model))
        for record in self.records:
            if record["_id"] == filters.get("_id"):
                record.update(update_model)


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
    monkeypatch.setattr(settings, "original_files_path", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    # The real sniffer needs libmagic; the point here is the record shape.
    monkeypatch.setattr(filestore, "sniff_media_type", lambda path: "image/jpeg")

    fake.root = tmp_path
    return fake


def upload(content: bytes, filename: str = "scan.jpg", **kwargs) -> storage.IncomingFile:
    return storage.IncomingFile(filename=filename, stream=io.BytesIO(content), **kwargs)


def resource_of(mongo):
    return mongo.resources[RESOURCE_ID]


# ---------------------------------------------------------------------------
# Storing a new file
# ---------------------------------------------------------------------------


def test_a_file_becomes_a_record_and_an_attachment_entry(mongo):
    attached = storage.attach_files(RESOURCE_ID, [upload(b"scan bytes")], "alice")

    assert attached == [{"id": "rec1", "tag": "file"}]
    assert len(mongo.records) == 1


def test_the_record_carries_what_is_known_about_the_file(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"scan bytes", tag="master")], "alice")
    record = mongo.records[0]

    assert record["name"] == "scan.jpg"
    assert record["size"] == len(b"scan bytes")
    assert record["mime"] == "image/jpeg"
    assert record["status"] == storage.STATUS_UPLOADED
    assert record["updatedBy"] == "alice"


def test_the_stored_path_is_relative_to_the_media_root(mongo):
    """Absolute paths would break the moment the media root moves, which it does
    between a local install and a compose deployment."""
    storage.attach_files(RESOURCE_ID, [upload(b"x")], "alice")
    filepath = mongo.records[0]["filepath"]

    assert not filepath.startswith("/")
    assert (mongo.root / filepath).is_file()


def test_the_record_inherits_the_resources_ancestry(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"x")], "alice")
    record = mongo.records[0]

    assert record["parent"] == [{"id": RESOURCE_ID, "post_type": "carpeta"}]
    assert record["parents"] == [{"id": "root", "post_type": "fondo"}]


def test_the_tag_and_order_travel_to_the_attachment_entry(mongo):
    attached = storage.attach_files(
        RESOURCE_ID, [upload(b"x", tag="anexo", order=3)], "alice"
    )
    assert attached == [{"id": "rec1", "tag": "anexo", "order": 3}]


def test_no_order_means_no_order_key(mongo):
    assert "order" not in storage.attach_files(RESOURCE_ID, [upload(b"x")], "alice")[0]


def test_several_files_are_attached_in_order(mongo):
    attached = storage.attach_files(
        RESOURCE_ID,
        [upload(b"one", "a.jpg"), upload(b"two", "b.pdf"), upload(b"three", "c.png")],
        "alice",
    )

    assert [a["id"] for a in attached] == ["rec1", "rec2", "rec3"]
    assert len(mongo.records) == 3


# ---------------------------------------------------------------------------
# File types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["a.exe", "a.sh", "a.php", "a"])
def test_a_disallowed_file_type_is_refused(mongo, filename):
    with pytest.raises(storage.UnsupportedFileType):
        storage.attach_files(RESOURCE_ID, [upload(b"x", filename)], "alice")

    assert mongo.records == []


def test_the_extension_check_is_case_insensitive(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"x", "SCAN.JPG")], "alice")
    assert len(mongo.records) == 1


def test_a_jpeg_named_jfif_is_accepted(mongo):
    """Windows and several cameras write a JPEG under this name, so refusing it
    rejects a file the archive can already read and derive from."""
    storage.attach_files(RESOURCE_ID, [upload(b"x", "photo.jfif")], "alice")
    assert len(mongo.records) == 1


def test_a_refused_file_is_not_written_to_disk(mongo):
    with pytest.raises(storage.UnsupportedFileType):
        storage.attach_files(RESOURCE_ID, [upload(b"x", "a.exe")], "alice")

    assert list(mongo.root.rglob("*.exe")) == []


def test_a_filename_that_sanitises_to_nothing_is_refused(mongo):
    with pytest.raises(filestore.UnsupportedFile):
        storage.attach_files(RESOURCE_ID, [upload(b"x", "...")], "alice")


def test_a_traversal_filename_cannot_escape_the_media_root(mongo):
    """`../../etc/passwd` reduces to `passwd`, which has no extension and is
    therefore refused before anything is written."""
    with pytest.raises(storage.UnsupportedFileType):
        storage.attach_files(RESOURCE_ID, [upload(b"x", "../../etc/passwd")], "alice")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_the_same_bytes_uploaded_twice_produce_one_record(mongo):
    """Archives receive the same scan against several catalogue entries
    routinely; storing it once is the point of hashing."""
    storage.attach_files(RESOURCE_ID, [upload(b"identical")], "alice")
    attached = storage.attach_files(RESOURCE_ID, [upload(b"identical", "otro.jpg")], "alice")

    assert len(mongo.records) == 1
    assert attached[0]["id"] == "rec1"


def test_the_duplicate_copy_is_removed_from_disk(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"identical")], "alice")
    storage.attach_files(RESOURCE_ID, [upload(b"identical", "otro.jpg")], "alice")

    assert len(list(mongo.root.rglob("*.jpg"))) == 1


def test_deduplication_is_by_content_not_by_name(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"same", "a.jpg")], "alice")
    storage.attach_files(RESOURCE_ID, [upload(b"same", "b.jpg")], "alice")

    assert len(mongo.records) == 1


def test_different_bytes_with_the_same_name_are_two_records(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"one", "scan.jpg")], "alice")
    storage.attach_files(RESOURCE_ID, [upload(b"two", "scan.jpg")], "alice")

    assert len(mongo.records) == 2


def test_a_duplicate_gains_the_new_resource_as_a_parent(mongo):
    mongo.resources[OTHER_RESOURCE] = {
        "_id": ObjectId(OTHER_RESOURCE),
        "post_type": "expediente",
        "parents": [{"id": "other-root", "post_type": "fondo"}],
    }

    storage.attach_files(RESOURCE_ID, [upload(b"shared")], "alice")
    storage.attach_files(OTHER_RESOURCE, [upload(b"shared")], "alice")

    record = mongo.records[0]
    assert [p["id"] for p in record["parent"]] == [RESOURCE_ID, OTHER_RESOURCE]
    assert {p["id"] for p in record["parents"]} == {"root", "other-root"}


def test_reattaching_to_the_same_resource_does_not_duplicate_the_parent(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"shared")], "alice")
    storage.attach_files(RESOURCE_ID, [upload(b"shared")], "alice")

    assert len(mongo.records[0]["parent"]) == 1


def test_parent_order_is_stable(mongo):
    """The original built a set of ids and rebuilt the list from it, so stored
    parent order was whatever the set happened to iterate - and string hashing
    is salted per process, so it differed between runs."""
    mongo.records.append({
        "_id": "rec0",
        "hash": "h",
        "parent": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        "parents": [],
        "status": storage.STATUS_UPLOADED,
    })

    storage._add_parent(mongo.records[0], "d", {"post_type": "x", "parents": []}, "alice")

    _filters, update = mongo.updates[0]
    assert [p["id"] for p in update["parent"]] == ["a", "b", "c", "d"]


def test_a_parent_entry_without_an_id_does_not_crash_the_merge(mongo):
    """The original did `set(x['id'] for x in new_parent)`."""
    mongo.records.append({
        "_id": "rec0", "hash": "h", "parent": [{"no": "id"}, {"id": "a"}],
        "parents": [], "status": storage.STATUS_UPLOADED,
    })

    storage._add_parent(mongo.records[0], "b", {"post_type": "x", "parents": []}, "alice")

    _filters, update = mongo.updates[0]
    assert [p["id"] for p in update["parent"]] == ["a", "b"]


# ---------------------------------------------------------------------------
# Reviving a deleted record
# ---------------------------------------------------------------------------


def test_reuploading_a_deleted_file_revives_it(mongo):
    """Re-uploading is exactly how someone restores a file whose only parent
    was deleted."""
    storage.attach_files(RESOURCE_ID, [upload(b"gone")], "alice")
    mongo.records[0]["status"] = storage.STATUS_DELETED

    storage.attach_files(RESOURCE_ID, [upload(b"gone")], "alice")

    assert mongo.records[0]["status"] == storage.STATUS_UPLOADED


def test_a_revived_file_with_derivatives_returns_to_processed(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"gone")], "alice")
    mongo.records[0]["status"] = storage.STATUS_DELETED
    mongo.records[0]["processing"] = {"files": ["derived.jpg"]}

    storage.attach_files(RESOURCE_ID, [upload(b"gone")], "alice")

    assert mongo.records[0]["status"] == storage.STATUS_PROCESSED


def test_an_undeleted_record_keeps_its_status(mongo):
    storage.attach_files(RESOURCE_ID, [upload(b"here")], "alice")
    mongo.records[0]["status"] = storage.STATUS_PROCESSED

    storage.attach_files(RESOURCE_ID, [upload(b"here")], "alice")

    assert mongo.records[0]["status"] == storage.STATUS_PROCESSED


# ---------------------------------------------------------------------------
# Files produced elsewhere
# ---------------------------------------------------------------------------


def test_a_file_already_on_disk_can_be_attached(mongo, tmp_path):
    source = tmp_path / "derived.pdf"
    source.write_bytes(b"produced by a plugin")

    attached = storage.attach_files(
        RESOURCE_ID, [storage.IncomingFile.from_path(source)], "alice"
    )

    assert attached[0]["id"] == "rec1"
    assert mongo.records[0]["name"] == "derived.pdf"
    assert source.exists(), "the source must not be consumed"


def test_a_file_on_disk_is_deduplicated_the_same_way(mongo, tmp_path):
    """THE bug this replaces .

    In the legacy non-upload branch, the duplicate case returned
    ``str(new_record.inserted_id)`` - a variable only bound when a record was
    *created*. On the first file that raised NameError; on a later one it still
    referred to the previous iteration's record, so the resource was given an
    attachment pointing at the wrong file.
    """
    source = tmp_path / "a.pdf"
    source.write_bytes(b"same bytes")
    other = tmp_path / "b.pdf"
    other.write_bytes(b"same bytes")

    storage.attach_files(RESOURCE_ID, [storage.IncomingFile.from_path(source)], "alice")
    attached = storage.attach_files(
        RESOURCE_ID, [storage.IncomingFile.from_path(other)], "alice"
    )

    assert len(mongo.records) == 1
    assert attached[0]["id"] == "rec1"


def test_a_duplicate_in_the_middle_of_a_batch_reports_its_own_record(mongo, tmp_path):
    """The precise shape of the defect: the wrong id was reported only for files after
    the first, which is why it would look like intermittent mis-association
    rather than an outright failure."""
    first = tmp_path / "first.pdf"
    first.write_bytes(b"first")
    storage.attach_files(RESOURCE_ID, [storage.IncomingFile.from_path(first)], "alice")

    duplicate = tmp_path / "dup.pdf"
    duplicate.write_bytes(b"first")
    fresh = tmp_path / "fresh.pdf"
    fresh.write_bytes(b"fresh")

    attached = storage.attach_files(
        RESOURCE_ID,
        [
            storage.IncomingFile.from_path(fresh),
            storage.IncomingFile.from_path(duplicate),
        ],
        "alice",
    )

    assert [a["id"] for a in attached] == ["rec2", "rec1"]


# ---------------------------------------------------------------------------
# Resource lookup
# ---------------------------------------------------------------------------


def test_attaching_to_a_missing_resource_is_refused(mongo):
    with pytest.raises(ValueError):
        storage.attach_files("6a70b833497d4440325c94ff", [upload(b"x")], "alice")


def test_attaching_to_a_malformed_resource_id_is_refused(mongo):
    with pytest.raises(ValueError):
        storage.attach_files("not-an-object-id", [upload(b"x")], "alice")


def test_a_caller_may_supply_a_resource_that_is_not_stored_yet(mongo):
    """The create path needs this: the resource is inserted after its files are
    stored, so there is nothing to look up."""
    attached = storage.attach_files(
        "brand-new",
        [upload(b"x")],
        "alice",
        resource={"post_type": "carpeta", "parents": []},
    )

    assert attached[0]["id"] == "rec1"
    assert mongo.records[0]["parent"] == [{"id": "brand-new", "post_type": "carpeta"}]


def test_a_resource_with_no_ancestry_is_fine(mongo):
    """`resource['parents']` was subscripted directly."""
    del mongo.resources[RESOURCE_ID]["parents"]

    storage.attach_files(RESOURCE_ID, [upload(b"x")], "alice")
    assert mongo.records[0]["parents"] == []


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_an_oversized_upload_is_refused_and_leaves_nothing(mongo, monkeypatch):
    settings = storage.get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 10, raising=False)

    with pytest.raises(filestore.UploadTooLarge):
        storage.attach_files(RESOURCE_ID, [upload(b"x" * 100)], "alice")

    assert mongo.records == []
    assert list(mongo.root.rglob("*.jpg")) == []
