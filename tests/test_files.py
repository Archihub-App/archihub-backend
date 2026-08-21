"""File storage and delivery.

The foundation the `resources` and `records` routes are built on, and where
three decisions are made concrete: uploads are bounded, durability applies to the
file being written, and Range handling is Starlette's.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archihub.core import files, responses


@pytest.fixture
def storage(tmp_path):
    return tmp_path / "originals"


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("photo.JPG", "photo.JPG"),
        ("mi archivo.pdf", "mi_archivo.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32", "system32"),
        ("expediente-1998_final.tiff", "expediente-1998_final.tiff"),
    ],
)
def test_a_filename_is_reduced_to_something_safe(given, expected):
    assert files.secure_name(given) == expected


def test_an_accented_filename_survives_as_ascii():
    assert files.secure_name("informe_año.pdf") == "informe_ano.pdf"


@pytest.mark.parametrize("given", ["", None, "...", "/", "///", "..", "___"])
def test_a_name_that_sanitises_to_nothing_is_refused(given):
    """Werkzeug's `secure_filename` returns '' for these.

    The original then did `os.path.join(directory, '')` - which is the directory
    - and tried to write a file over it.
    """
    with pytest.raises(files.UnsupportedFile):
        files.secure_name(given)


@pytest.mark.parametrize(
    "given,expected", [("a.PDF", "pdf"), ("a.tar.gz", "gz"), ("noextension", ""), ("a.", "")]
)
def test_the_extension_is_lowercased(given, expected):
    assert files.extension_of(given) == expected


def test_an_allowed_extension_is_recognised_case_insensitively():
    assert files.is_allowed("scan.TIF", {"tif", "tiff"}) is True


def test_a_file_with_no_extension_is_never_allowed():
    assert files.is_allowed("scan", {"tif"}) is False


def test_the_storage_name_is_unique_and_keeps_the_extension():
    first = files.unique_name("photo.jpg")
    second = files.unique_name("photo.jpg")

    assert first != second
    assert first.endswith(".jpg")


# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------


def test_the_dated_directory_is_created(tmp_path):
    import datetime

    path = files.dated_directory(tmp_path, datetime.datetime(2024, 3, 9))

    assert path == tmp_path / "2024" / "03" / "09"
    assert path.is_dir()


def test_a_stored_path_cannot_escape_the_media_root(tmp_path):
    """Paths come out of the database, and a document holding `../..` would
    otherwise reach outside the media root."""
    with pytest.raises(files.UnsupportedFile):
        files.resolve_within(tmp_path, "..", "..", "etc", "passwd")


def test_a_path_inside_the_root_resolves(tmp_path):
    assert files.resolve_within(tmp_path, "2024", "a.jpg") == tmp_path.resolve() / "2024" / "a.jpg"


def test_the_root_itself_resolves(tmp_path):
    assert files.resolve_within(tmp_path) == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


def test_an_upload_is_written_under_a_fresh_name(storage):
    stored = files.store_upload(io.BytesIO(b"contents"), storage, "photo.jpg")

    assert stored.path.read_bytes() == b"contents"
    assert stored.path.name != "photo.jpg"
    assert stored.path.suffix == ".jpg"
    assert stored.original_filename == "photo.jpg"


def test_the_client_filename_never_appears_on_disk(storage):
    """The original wrote under the client's name and *then* renamed to a UUID,
    so two concurrent uploads of the same filename raced - the second overwrote
    the first, both renamed, and one upload was silently lost.
    """
    files.store_upload(io.BytesIO(b"a"), storage, "photo.jpg")
    files.store_upload(io.BytesIO(b"b"), storage, "photo.jpg")

    names = sorted(p.name for p in storage.iterdir())
    assert "photo.jpg" not in names
    assert len(names) == 2


def test_the_hash_is_computed_during_the_copy(storage):
    """The original wrote the file and then read all of it back to hash it,
    which for archival masters means re-reading gigabytes for no reason."""
    payload = b"archival master" * 1000
    stored = files.store_upload(io.BytesIO(payload), storage, "master.tif")

    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.size == len(payload)


def test_a_multi_chunk_upload_is_written_whole(storage):
    payload = os.urandom(files.CHUNK_SIZE * 2 + 17)
    stored = files.store_upload(io.BytesIO(payload), storage, "big.bin")

    assert stored.path.read_bytes() == payload
    assert stored.size == len(payload)


def test_an_oversized_upload_is_refused(storage):
    with pytest.raises(files.UploadTooLarge):
        files.store_upload(io.BytesIO(b"x" * 100), storage, "big.bin", max_bytes=10)


def test_a_refused_upload_leaves_nothing_behind(storage):
    with pytest.raises(files.UploadTooLarge):
        files.store_upload(io.BytesIO(b"x" * 100), storage, "big.bin", max_bytes=10)

    assert list(storage.iterdir()) == []


def test_exactly_the_limit_is_accepted(storage):
    stored = files.store_upload(io.BytesIO(b"x" * 10), storage, "ok.bin", max_bytes=10)
    assert stored.size == 10


def test_a_zero_limit_means_unbounded(storage):
    stored = files.store_upload(io.BytesIO(b"x" * 5000), storage, "ok.bin", max_bytes=0)
    assert stored.size == 5000


def test_a_spooled_temporary_file_can_be_stored(storage):
    """Starlette hands us `UploadFile.file`, a SpooledTemporaryFile."""
    spooled = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    spooled.write(b"still in memory")
    spooled.seek(0)

    stored = files.store_upload(spooled, storage, "note.txt")
    assert stored.path.read_bytes() == b"still in memory"


def test_storing_does_not_force_a_spooled_upload_onto_disk(storage):
    """CORRECTS AN ASSUMPTION IN THE PLAN (section 6).

    The plan expected `SpooledTemporaryFile.fileno()` to *raise* while the
    contents are still in memory, making the legacy `os.fsync(file.fileno())`
    an outright error under Starlette. It does not, on either Python this runs
    on (3.11 in the image, 3.12 locally): `fileno()` calls `rollover()` first,
    so the real effect would have been to spill every in-memory upload to a
    temporary file and then fsync *that* - pointless I/O against the file being
    read, not a crash.

    The fix is the same either way, and this pins the property that matters:
    storing an upload never touches the source's descriptor.
    """
    spooled = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    spooled.write(b"small enough to stay in memory")
    spooled.seek(0)

    files.store_upload(spooled, storage, "note.txt")

    assert spooled._rolled is False


def test_a_filename_that_cannot_be_sanitised_is_refused_before_any_write(storage):
    with pytest.raises(files.UnsupportedFile):
        files.store_upload(io.BytesIO(b"x"), storage, "...")

    assert not storage.exists() or list(storage.iterdir()) == []


def test_a_file_already_on_disk_can_be_stored(tmp_path, storage):
    source = tmp_path / "derived.pdf"
    source.write_bytes(b"generated by a plugin")

    stored = files.store_existing_file(source, storage)

    assert stored.path.read_bytes() == b"generated by a plugin"
    assert stored.sha256 == hashlib.sha256(b"generated by a plugin").hexdigest()
    assert source.exists(), "the source must not be consumed"


def test_hashing_an_existing_file_agrees_with_storing_it(tmp_path, storage):
    source = tmp_path / "a.bin"
    source.write_bytes(b"same bytes")

    stored = files.store_existing_file(source, storage)
    assert files.hash_file(stored.path) == stored.sha256


def test_removing_a_missing_file_is_not_an_error(tmp_path):
    files.remove_quietly(tmp_path / "never-existed")


# ---------------------------------------------------------------------------
# Content sniffing
# ---------------------------------------------------------------------------


def test_sniffing_falls_back_to_none_when_unavailable(tmp_path, monkeypatch):
    target = tmp_path / "a.bin"
    target.write_bytes(b"\x00\x01")

    monkeypatch.setitem(__import__("sys").modules, "magic", None)
    assert files.sniff_media_type(target) is None


def test_an_unsniffable_file_is_not_rejected(tmp_path, monkeypatch):
    """Defence in depth layered on the extension allowlist - an environment
    without libmagic must not end up accepting nothing."""
    target = tmp_path / "a.bin"
    target.write_bytes(b"\x00")

    monkeypatch.setattr(files, "sniff_media_type", lambda path: None)
    assert files.content_matches_extension(target, ["image/"]) is True


def test_content_that_does_not_match_the_claim_is_detected(tmp_path, monkeypatch):
    target = tmp_path / "photo.jpg"
    target.write_bytes(b"#!/bin/sh\n")

    monkeypatch.setattr(files, "sniff_media_type", lambda path: "text/x-shellscript")
    assert files.content_matches_extension(target, ["image/"]) is False


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path):
    """An app serving one 1000-byte file, so Range behaviour can be observed."""
    target = tmp_path / "clip.mp4"
    target.write_bytes((bytes(range(256)) * 4)[:1000])

    app = FastAPI()

    @app.get("/media")
    def media():
        return responses.file_response(target)

    @app.get("/download")
    def download():
        return responses.file_response(target, as_attachment=True, download_name="informe.mp4")

    return TestClient(app), target


def test_a_whole_file_is_served(served):
    client, target = served
    response = client.get("/media")

    assert response.status_code == 200
    assert response.content == target.read_bytes()


def test_a_range_request_is_answered_with_206(served):
    """The multimedia players seek with these; Flask's send_file supported it
    by default, and this is the check that Starlette still does."""
    client, _target = served
    response = client.get("/media", headers={"Range": "bytes=0-99"})

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-99/1000"
    assert len(response.content) == 100


def test_a_range_from_the_middle_returns_the_right_bytes(served):
    client, target = served
    response = client.get("/media", headers={"Range": "bytes=500-599"})

    assert response.content == target.read_bytes()[500:600]


def test_an_open_ended_range_runs_to_the_end(served):
    client, _target = served
    response = client.get("/media", headers={"Range": "bytes=900-"})

    assert response.status_code == 206
    assert len(response.content) == 100


def test_an_unsatisfiable_range_is_416(served):
    client, _target = served
    assert client.get("/media", headers={"Range": "bytes=5000-6000"}).status_code == 416


def test_the_server_advertises_range_support(served):
    client, _target = served
    assert client.get("/media").headers.get("accept-ranges") == "bytes"


def test_an_attachment_carries_its_download_name(served):
    client, _target = served
    disposition = client.get("/download").headers["content-disposition"]

    assert "attachment" in disposition
    assert "informe.mp4" in disposition


def test_an_inline_response_is_not_an_attachment(served):
    client, _target = served
    assert "attachment" not in client.get("/media").headers.get("content-disposition", "")


def test_a_temporary_file_is_removed_after_it_has_been_sent(tmp_path):
    """The replacement for Flask's `response.call_on_close`, used by the
    fragment extractors. It must run *after* the last byte, not before."""
    target = tmp_path / "fragment.mp4"
    target.write_bytes(b"transcoded")

    app = FastAPI()

    @app.get("/fragment")
    def fragment():
        return responses.file_response(target, delete_after=True)

    client = TestClient(app)
    response = client.get("/fragment")

    assert response.content == b"transcoded"
    assert not target.exists()


def test_serving_a_missing_file_raises_rather_than_500ing_silently(tmp_path):
    with pytest.raises(FileNotFoundError):
        responses.file_response(tmp_path / "gone.jpg")


def test_serving_a_directory_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        responses.file_response(tmp_path)


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("a.jpg", "image/jpeg"),
        ("a.mp4", "video/mp4"),
        ("a.pdf", "application/pdf"),
        ("a.flac", "audio/flac"),
        ("a.jp2", "image/jp2"),
        ("a.unknown", "application/octet-stream"),
    ],
)
def test_media_types_cover_the_formats_an_archive_holds(filename, expected):
    assert responses.guess_media_type(filename) == expected


def test_an_in_memory_payload_is_served_without_touching_disk():
    """The two snaps routes render a single JPEG frame; there is nothing to
    range over and nothing to write."""
    app = FastAPI()

    @app.get("/frame")
    def frame():
        return responses.bytes_response(b"\xff\xd8jpeg", media_type="image/jpeg")

    response = TestClient(app).get("/frame")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8jpeg"
