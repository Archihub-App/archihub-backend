"""Page images, gallery images, deep-zoom tiles and OCR blocks.

The centre of gravity here is that a client-supplied string never becomes a
path component. ``test_a_traversing_size_is_refused`` and its neighbours are the
regression tests for BACKEND_FINDINGS S18 - keep them.
"""

from __future__ import annotations

import base64
import pathlib

import pytest
from bson.objectid import ObjectId

from archihub.api.records import viewers

RECORD_ID = "6a70b833497d4440325c94b1"
IMAGE_A = "6a70b833497d4440325c9401"
IMAGE_B = "6a70b833497d4440325c9402"


class FakeMongo:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.chunks: list[dict] = []

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "records":
            wanted = {str(o) for o in (filters or {}).get("_id", {}).get("$in", [])}
            rows = [r for k, r in self.records.items() if k in wanted]
            if "processing.fileProcessing.type" in (filters or {}):
                kind = filters["processing.fileProcessing.type"]
                rows = [
                    r
                    for r in rows
                    if ((r.get("processing") or {}).get("fileProcessing") or {}).get("type") == kind
                ]
            return rows
        return list(self.chunks)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(viewers, "_mongo", lambda: fake)
    return fake


@pytest.fixture
def web_root(tmp_path, monkeypatch):
    """Point the media root at a temp directory, and put a decoy outside it."""
    root = tmp_path / "webfiles"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("private key material")

    class Settings:
        web_files_path = str(root)
        original_files_path = str(tmp_path / "original")
        transcription_page_char_limit = 6000

    monkeypatch.setattr(viewers, "get_settings", lambda: Settings())
    monkeypatch.setattr("archihub.core.files.get_settings", lambda: Settings())
    return root


@pytest.fixture(autouse=True)
def fake_dimensions(monkeypatch):
    """Report a fixed size rather than requiring real JPEG bytes on disk."""
    monkeypatch.setattr(viewers, "image_dimensions", lambda path: (200, 100))


def document(path="2024/03/doc", **overrides):
    record = {
        "_id": ObjectId(RECORD_ID),
        "processing": {"fileProcessing": {"type": "document", "path": path}},
    }
    record.update(overrides)
    return record


def write_pages(root, stored, size, count):
    directory = root / stored / "web" / size
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (directory / f"page_{index:04d}.jpg").write_bytes(f"page {index}".encode())
    return directory


# ---------------------------------------------------------------------------
# The size parameter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size",
    [
        "../../../../etc",
        "..",
        "../small",
        "small/../../..",
        "/etc",
        "small\x00",
    ],
)
def test_a_traversing_size_is_refused(mongo, web_root, size):
    """A size that is not one of the two known names never reaches the filesystem.

    The original concatenated this straight into the directory it listed, then
    base64-encoded whatever it found there. Any caller who could read one record
    could read any directory the service account could reach.
    """
    write_pages(web_root, "2024/03/doc", "small", 3)

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(document(), [0], size)

    assert exc.value.status_code == 400


def test_only_the_two_real_page_sizes_are_accepted(mongo, web_root):
    write_pages(web_root, "2024/03/doc", "small", 2)
    write_pages(web_root, "2024/03/doc", "big", 2)

    assert len(viewers.page_images(document(), [0], "small")) == 1
    assert len(viewers.page_images(document(), [0], "big")) == 1

    with pytest.raises(viewers.ViewerError):
        viewers.page_images(document(), [0], "medium")


def test_a_stored_path_that_climbs_out_is_refused(mongo, web_root):
    """The stored path is trusted less than the code that wrote it."""
    from archihub.core.files import UnsupportedFile

    with pytest.raises(UnsupportedFile):
        viewers.page_images(document(path="../../etc"), [0], "small")


# ---------------------------------------------------------------------------
# Page indices
# ---------------------------------------------------------------------------


def test_pages_are_returned_in_the_order_asked_for(mongo, web_root):
    write_pages(web_root, "2024/03/doc", "small", 5)

    result = viewers.page_images(document(), [2, 0], "small")

    assert [base64.b64decode(entry["data"]).decode() for entry in result] == ["page 2", "page 0"]


def test_a_negative_page_index_is_refused(mongo, web_root):
    """Bare list subscripting would count back from the end of the document."""
    write_pages(web_root, "2024/03/doc", "small", 5)

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(document(), [-1], "small")

    assert exc.value.status_code == 400


def test_a_page_past_the_end_is_a_404(mongo, web_root):
    write_pages(web_root, "2024/03/doc", "small", 2)

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(document(), [7], "small")

    assert exc.value.status_code == 404


def test_no_pages_asked_for_is_an_empty_list(mongo, web_root):
    assert viewers.page_images(document(), [], "small") == []


def test_a_non_numeric_page_is_refused(mongo, web_root):
    write_pages(web_root, "2024/03/doc", "small", 2)

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(document(), ["one"], "small")

    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Document detail
# ---------------------------------------------------------------------------


def test_document_detail_counts_pages_and_measures_the_first(mongo, web_root):
    write_pages(web_root, "2024/03/doc", "small", 4)

    assert viewers.document_detail(document()) == {"pages": 4, "aspect_ratio": 2.0}


def test_document_detail_of_an_image_is_a_single_page(mongo, web_root):
    (web_root / "2024" / "03").mkdir(parents=True)
    (web_root / "2024" / "03" / "img_small.jpg").write_bytes(b"x")

    record = {
        "processing": {"fileProcessing": {"type": "image", "path": "2024/03/img"}},
    }

    assert viewers.document_detail(record) == {"pages": 1, "aspect_ratio": 2.0}


# ---------------------------------------------------------------------------
# The image reader, which shares the document reader's two calls
# ---------------------------------------------------------------------------


def _image(root, stored="2024/03/img", sizes=("small", "large")):
    """An image record with its derivatives on disk."""
    directory = root / pathlib.Path(stored).parent
    directory.mkdir(parents=True, exist_ok=True)
    for size in sizes:
        (root / f"{stored}_{size}.jpg").write_bytes(f"{size} bytes".encode())
    return {
        "_id": ObjectId(RECORD_ID),
        "processing": {"fileProcessing": {"type": "image", "path": stored}},
    }


def test_page_images_serves_an_image_record(mongo, web_root):
    """`ImageViewer.tsx` reaches an image through the DOCUMENT reader's routes.

    It calls the shared viewer's ``init`` with four arguments, so ``isDocument``
    keeps its default and the request carries ``gallery: false``. Refusing here
    while ``document_detail`` answers ``{"pages": 1}`` for the same record
    leaves the reader knowing about a page it can never fetch - a 200 followed
    by a 400, and an empty frame.
    """
    record = _image(web_root)

    result = viewers.page_images(record, [0], "small")

    assert len(result) == 1
    assert result[0]["filename"] == "img_small.jpg"
    assert base64.b64decode(result[0]["data"]) == b"small bytes"


def test_an_image_page_carries_the_aspect_ratio(mongo, web_root):
    """The reader draws the frame from it, and a document page has none."""
    result = viewers.page_images(_image(web_root), [0], "small")

    assert result[0]["aspect_ratio"] == 2.0


def test_the_readers_two_calls_agree_about_an_image(mongo, web_root):
    """The defect was the disagreement, not either call on its own."""
    record = _image(web_root)

    detail = viewers.document_detail(record)
    pages = viewers.page_images(record, list(range(detail["pages"])), "small")

    assert len(pages) == detail["pages"]


def test_big_resolves_to_the_large_derivative(mongo, web_root):
    """``big`` is the frontend's name for what processing writes as ``large``."""
    result = viewers.page_images(_image(web_root), [0], "big")

    assert result[0]["filename"] == "img_large.jpg"


@pytest.mark.parametrize("size", ["../../../../etc", "..", "/etc", "small\x00", "nonsense"])
def test_an_image_page_size_outside_the_map_is_refused(mongo, web_root, size):
    """Same rule as the document pages: the string indexes a map, it is never a path."""
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(_image(web_root), [0], size)

    assert exc.value.status_code == 400


def test_a_missing_image_derivative_is_404_not_a_traceback(mongo, web_root):
    record = _image(web_root, sizes=())

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(record, [0], "small")

    assert exc.value.status_code == 404


def test_a_record_of_an_unknown_kind_is_still_refused(mongo, web_root):
    """Serving images must not turn the check into a pass-through."""
    record = {"processing": {"fileProcessing": {"type": "video", "path": "2024/03/v"}}}

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.page_images(record, [0], "small")

    assert exc.value.status_code == 400


def test_an_unprocessed_record_says_so_rather_than_raising_keyerror(mongo, web_root):
    """The original's inner test subscripted the key its outer test ruled out."""
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.document_detail({"name": "scan.tif"})

    assert exc.value.status_code == 404
    assert "processed" in str(exc.value)


def test_a_record_that_is_neither_document_nor_image_is_refused(mongo, web_root):
    record = {"processing": {"fileProcessing": {"type": "audio", "path": "2024/03/rec"}}}

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.document_detail(record)

    assert exc.value.status_code == 400


def test_a_document_whose_renderings_are_missing_is_a_404(mongo, web_root):
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.document_detail(document())

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------


def image_record(record_id, path):
    return {
        "_id": ObjectId(record_id),
        "processing": {"fileProcessing": {"type": "image", "path": path}},
    }


@pytest.fixture
def gallery(mongo, web_root):
    mongo.records[IMAGE_A] = image_record(IMAGE_A, "2024/03/a")
    mongo.records[IMAGE_B] = image_record(IMAGE_B, "2024/03/b")
    directory = web_root / "2024" / "03"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("a", "b"):
        for suffix in ("_small.jpg", "_large.jpg"):
            (directory / (name + suffix)).write_bytes(f"{name}{suffix}".encode())
    return {"filesObj": [{"id": IMAGE_B, "order": 0}, {"id": IMAGE_A, "order": 1}]}


def test_the_gallery_is_returned_in_the_curators_order(gallery):
    """The order map is keyed by string ids and looked up with ObjectIds.

    The original compared the two directly, so every lookup missed and every
    gallery came back in Mongo's natural order instead of the curator's.
    """
    result = viewers.gallery_images(gallery, [0, 1], "small")

    assert [base64.b64decode(entry["data"]).decode() for entry in result] == [
        "b_small.jpg",
        "a_small.jpg",
    ]


def test_big_is_an_alias_for_the_large_derivative(gallery):
    result = viewers.gallery_images(gallery, [0], "big")

    assert base64.b64decode(result[0]["data"]).decode() == "b_large.jpg"


def test_a_traversing_gallery_size_is_refused(gallery):
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.gallery_images(gallery, [0], "../../../etc/passwd")

    assert exc.value.status_code == 400


def test_gallery_entries_carry_an_aspect_ratio(gallery):
    assert viewers.gallery_images(gallery, [0], "small")[0]["aspect_ratio"] == 2.0


def test_one_missing_derivative_does_not_blank_the_whole_page(gallery, web_root):
    """The original raised, so an image still processing emptied the gallery."""
    (web_root / "2024" / "03" / "b_small.jpg").unlink()

    result = viewers.gallery_images(gallery, [0, 1], "small")

    assert [base64.b64decode(entry["data"]).decode() for entry in result] == ["a_small.jpg"]


def test_an_empty_gallery_window_is_an_empty_list(gallery):
    assert viewers.gallery_images(gallery, [], "small") == []


def test_a_resource_with_no_files_has_an_empty_gallery(mongo, web_root):
    assert viewers.gallery_images({"filesObj": []}, [0], "small") == []


# ---------------------------------------------------------------------------
# Deep zoom
# ---------------------------------------------------------------------------


@pytest.fixture
def pyramid(mongo, web_root):
    record = image_record(IMAGE_A, "2024/03/a")
    record["processing"]["fileProcessing"]["dzi"] = True
    mongo.records[IMAGE_A] = record

    directory = web_root / "2024" / "03"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a_tiles.dzi").write_text("<Image/>")
    tiles = directory / "a_tiles_files" / "9"
    tiles.mkdir(parents=True)
    (tiles / "3_4.jpeg").write_bytes(b"tile-bytes")

    return {"filesObj": [{"id": IMAGE_A, "order": 0}]}


def test_the_dzi_descriptor_is_returned_as_text(pyramid):
    assert viewers.dzi_data(pyramid, [0], {"type": "xml"}) == {"type": "xml", "data": "<Image/>"}


def test_a_tile_is_returned_base64_with_its_format(pyramid):
    result = viewers.dzi_data(pyramid, [0], {"type": "tile", "level": 9, "col": 3, "row": 4})

    assert result["format"] == "jpeg"
    assert base64.b64decode(result["data"]) == b"tile-bytes"


@pytest.mark.parametrize("coordinate", ["..", "../..", -1, None, "9;rm"])
def test_a_tile_coordinate_that_is_not_a_number_is_refused(pyramid, coordinate):
    """Coordinates become path segments, so they are checked before they are used."""
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.dzi_data(
            pyramid, [0], {"type": "tile", "level": coordinate, "col": 3, "row": 4}
        )

    assert exc.value.status_code == 400


def test_a_missing_tile_is_a_404(pyramid):
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.dzi_data(pyramid, [0], {"type": "tile", "level": 9, "col": 99, "row": 4})

    assert exc.value.status_code == 404


def test_an_unknown_dzi_payload_type_is_refused(pyramid):
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.dzi_data(pyramid, [0], {"type": "svg"})

    assert exc.value.status_code == 400


def test_only_images_with_a_pyramid_are_counted(mongo, web_root, pyramid):
    """A gallery image without tiles must not shift the index of one that has them."""
    mongo.records[IMAGE_B] = image_record(IMAGE_B, "2024/03/b")
    resource = {"filesObj": [{"id": IMAGE_B, "order": 0}, {"id": IMAGE_A, "order": 1}]}

    assert viewers.dzi_data(resource, [0], {"type": "xml"})["data"] == "<Image/>"


def test_a_dzi_index_past_the_end_is_a_404(pyramid):
    with pytest.raises(viewers.ViewerError) as exc:
        viewers.dzi_data(pyramid, [5], {"type": "xml"})

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


def ocr_document(web_root, pages):
    write_pages(web_root, "2024/03/doc", "big", len(pages))
    return {
        "_id": ObjectId(RECORD_ID),
        "processing": {
            "fileProcessing": {"type": "document", "path": "2024/03/doc"},
            "ocr": {"type": "ocr", "result": pages, "labels": [{"name": "title"}]},
        },
    }


def test_blocks_drop_word_level_geometry(mongo, web_root):
    """Words are the bulk of an OCR payload and the block view does not draw them."""
    record = ocr_document(
        web_root,
        [{"blocks": [{"bbox": [0, 0, 1, 1], "text": "hello", "words": [{"text": "hello"}]}]}],
    )

    result = viewers.blocks_for_page(record, 1, "ocr", "blocks")

    assert result["blocks"] == [{"bbox": [0, 0, 1, 1], "text": "hello"}]
    assert result["labels"] == [{"name": "title"}]


def test_words_are_flattened_across_the_pages_blocks(mongo, web_root):
    record = ocr_document(
        web_root,
        [
            {
                "blocks": [
                    {"words": [{"text": "a"}, {"text": "b"}]},
                    {"words": [{"text": "c"}]},
                ]
            }
        ],
    )

    result = viewers.blocks_for_page(record, 1, "ocr", "words")

    assert result == {
        "page": 1,
        "words": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        "labels": [{"name": "title"}],
    }


def test_blocks_are_addressed_one_indexed(mongo, web_root):
    record = ocr_document(
        web_root, [{"blocks": [{"text": "first"}]}, {"blocks": [{"text": "second"}]}]
    )

    assert viewers.blocks_for_page(record, 2, "ocr", "blocks")["blocks"] == [{"text": "second"}]


def test_a_block_kind_other_than_blocks_or_words_is_refused(mongo, web_root):
    record = ocr_document(web_root, [{"blocks": []}])

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.blocks_for_page(record, 1, "ocr", "everything")

    assert exc.value.status_code == 400


def test_an_unknown_slug_is_a_404(mongo, web_root):
    record = ocr_document(web_root, [{"blocks": []}])

    with pytest.raises(viewers.ViewerError) as exc:
        viewers.blocks_for_page(record, 1, "nosuch", "blocks")

    assert exc.value.status_code == 404


def test_blocks_of_an_image_come_from_the_result_directly(mongo, web_root):
    record = {
        "processing": {
            "fileProcessing": {"type": "image", "path": "2024/03/a"},
            "ocr": {"result": {"blocks": [{"text": "sign"}]}},
        }
    }

    assert viewers.blocks_for_page(record, 1, "ocr", "blocks") == [{"text": "sign"}]


def test_a_chunked_result_is_reassembled_in_chunk_order(mongo, web_root):
    mongo.chunks = [
        {"pages": [{"blocks": [{"text": "p1"}]}]},
        {"pages": [{"blocks": [{"text": "p2"}]}]},
    ]
    write_pages(web_root, "2024/03/doc", "big", 2)
    record = {
        "_id": ObjectId(RECORD_ID),
        "processing": {
            "fileProcessing": {"type": "document", "path": "2024/03/doc"},
            "ocr": {
                "type": "ocr",
                "result_storage": {"type": "chunked", "collection": "ocr_chunks"},
            },
        },
    }

    assert viewers.blocks_for_page(record, 2, "ocr", "blocks")["blocks"] == [{"text": "p2"}]
