"""filesProcessing: which branch a file takes, the reindex after a run, and
its stored settings."""

from __future__ import annotations

import pytest


def build(slug: str):
    from archihub.plugins.framework.mounting import build_plugin

    return build_plugin(slug)


class FakeMongo:
    """Records the settings writes a plugin makes, and answers reads with one document."""

    def __init__(self, record=None):
        self.record = record
        self.operations: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        return self.record

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.operations.append((filters, operator))
        return None


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake



@pytest.mark.parametrize(
    "mime,path,expected",
    [
        ("audio/wav", "a/b/master.wav", "audio"),
        ("video/mp4", "a/b/master.mp4", "video"),
        ("image/tiff", "a/b/master.tif", "image"),
        ("application/pdf", "a/b/doc.pdf", "pdf"),
        ("text/plain", "a/b/notes.txt", "document"),
        ("application/msword", "a/b/report.doc", "document"),
        ("text/csv", "a/b/data.csv", "csv"),
        # THE BUG: `len(filename.split('.')) != 2` returned None for any name
        # with more than one dot, so this went to the document branch and
        # LibreOffice was asked to convert a CSV.
        ("text/plain", "a/b/interview.final.csv", "csv"),
        ("application/vnd.ms-excel", "a/b/book.xls", "spreadsheet"),
        # A JPEG under a name `mimetypes` knows only from a system mime.types
        # file. The extension decides, so no derivative depends on libmagic
        # being installed or on what the uploader called the type.
        ("image/jpeg", "a/b/photo.jfif", "image"),
        ("application/octet-stream", "a/b/photo.jfif", "image"),
        (None, "a/b/photo.jfif", "image"),
        # Camera RAW sniffs as a TIFF, as a vendor type, or as nothing at all
        # depending on the format and the libmagic version. The extension decides.
        ("image/tiff", "a/b/shot.nef", "image"),
        ("image/x-canon-cr2", "a/b/shot.cr2", "image"),
        ("application/octet-stream", "a/b/shot.cr3", "image"),
        (None, "a/b/shot.ARW", "image"),
        ("application/octet-stream", "a/b/thing.bin", None),
        (None, "a/b/thing", None),
    ],
)
def test_a_file_is_classified_by_allowlist_not_by_substring(mime, path, expected):
    from archihub.plugins.filesProcessing import classify

    assert classify(mime, path) == expected


@pytest.fixture
def processing_run(monkeypatch):
    """filesProcessing with processing and the reindex it triggers stubbed."""
    import types

    import archihub.plugins.filesProcessing as files_processing
    from archihub.worker.tasks import indexing

    state = types.SimpleNamespace(reindexed=[], outcome={})

    def process(record):
        outcome = state.outcome.get(record["_id"], True)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(files_processing, "process_record", process)
    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: True)
    monkeypatch.setattr(indexing, "index_resources_task", lambda body: state.reindexed.append(body))
    state.run = files_processing._process_all
    return state


def _file(record_id, *parents):
    return {"_id": record_id, "parent": [{"id": p, "post_type": "gallery"} for p in parents]}


def test_resources_are_reindexed_once_their_files_are_processed(processing_run):
    """A resource's search document lists only processed files, and it is
    written when the resource is saved - before processing - so an image search
    cannot find the resource until the document is rebuilt."""
    from bson.objectid import ObjectId

    first, second = str(ObjectId()), str(ObjectId())

    processed = processing_run.run([_file("r1", first), _file("r2", first, second)])

    assert processed == 2
    assert processing_run.reindexed == [{"_id": {"$in": sorted([ObjectId(first), ObjectId(second)])}}]


def test_only_resources_with_a_processed_file_are_reindexed(processing_run):
    from bson.objectid import ObjectId

    from archihub.plugins.filesProcessing import media

    done, skipped, failed = str(ObjectId()), str(ObjectId()), str(ObjectId())
    processing_run.outcome = {"r2": False, "r3": media.ProcessingFailed("no")}

    processing_run.run([_file("r1", done), _file("r2", skipped), _file("r3", failed)])

    assert processing_run.reindexed == [{"_id": {"$in": [ObjectId(done)]}}]


def test_nothing_is_reindexed_when_nothing_was_processed_or_indexing_is_off(processing_run, monkeypatch):
    from bson.objectid import ObjectId

    processing_run.outcome = {"r1": False}
    processing_run.run([_file("r1", str(ObjectId()))])
    assert processing_run.reindexed == []

    monkeypatch.setattr("archihub.api.search.services.indexing_enabled", lambda: False)
    processing_run.run([_file("r2", str(ObjectId()))])
    assert processing_run.reindexed == []


def test_a_failed_reindex_does_not_fail_the_processing_run(processing_run, monkeypatch):
    from bson.objectid import ObjectId

    from archihub.worker.tasks import indexing

    def unavailable(body):
        raise ConnectionError("search is down")

    monkeypatch.setattr(indexing, "index_resources_task", unavailable)

    assert processing_run.run([_file("r1", str(ObjectId()))]) == 1


def test_a_raw_image_is_derived_from_decoded_sensor_data(tmp_path, monkeypatch):
    """A RAW file never reaches vips' file loaders, which cannot decode it."""
    import sys
    import types

    np = pytest.importorskip("numpy")
    pyvips = pytest.importorskip("pyvips")
    from archihub.plugins.filesProcessing import media

    # Portrait pixels, as LibRaw returns them once the orientation is applied.
    pixels = np.full((60, 40, 3), 128, dtype=np.uint8)

    class FakeRaw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kwargs):
            return pixels

    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(imread=lambda path: FakeRaw()))
    monkeypatch.setattr(media, "_exif", lambda source: {"Model": "test"})

    def refuse(*args, **kwargs):
        raise AssertionError("a RAW file was handed to a vips file loader")

    monkeypatch.setattr(pyvips.Image, "thumbnail", refuse)
    monkeypatch.setattr(pyvips.Image, "new_from_file", refuse)

    source = tmp_path / "shot.CR2"
    source.write_bytes(b"not decodable by vips")

    metadata, has_tiles = media.image(source, tmp_path / "shot")

    assert metadata == {"Model": "test"}
    assert has_tiles is False
    for suffix, _edge, _quality in media.IMAGE_SIZES:
        derived = pyvips.Image.new_from_buffer((tmp_path / f"shot{suffix}.jpg").read_bytes(), "")
        assert derived.height > derived.width


def test_hook_order_is_stored_as_a_number(mongo):
    """The legacy save wrote the STRING '0'; the hook bus sorts registrations,
    and sorting a mix of strings and numbers raises TypeError when the hook
    fires - taking the upload with it."""
    plugin = build("filesProcessing")

    plugin.save_settings({"types_activation": [{"type": "carpeta", "order": "3"}]})

    _, operator = mongo.operations[0]
    stored = operator["$set"]["plugins_settings.filesProcessing"]
    assert stored["types_activation"][0]["order"] == 3
