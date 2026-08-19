"""Page images, gallery images, deep-zoom tiles and OCR blocks.

This is what the document viewer and the image gallery fetch. Everything here
resolves a *stored* processing path to files on disk and returns them base64
encoded inside JSON, which is how the frontend's viewers consume them.

THE SIZE PARAMETER IS AN ALLOWLIST KEY, NOT A PATH SEGMENT. This is the whole
reason this is a module of its own. Building the directory to list as::

    path_files = os.path.join(WEB_FILES_PATH, path, 'web/' + size + '/')
    files = sorted(os.listdir(path_files))
    ... open(file, 'rb') -> base64 -> response

with ``size`` joined into the path. Any caller who can read one record can then
name any directory the service account can reach, list it, and have its contents
returned base64 encoded.

THE INVARIANT: **a client-supplied string never becomes a path component.** It
selects a key in a fixed map, and anything absent from that map is refused before
a path is built. Every path assembled from *stored* data additionally goes
through ``resolve_within``.

Page indices are validated for the same reason in miniature - subscripting
``files[x]`` with the client's integer means a negative index quietly
returned a page counted from the end of the document instead of an error.
"""

from __future__ import annotations

import base64
import logging
import os

from archihub.core import files as filestore
from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

#: Page-image directories a document's processing writes, keyed by the name the
#: frontend asks for. `DocumentViewerUtils.tsx` and `DocumentViewerScroll.tsx`
#: request exactly these two.
PAGE_DIRECTORIES = {"small": "small", "big": "big"}

#: Image derivative suffixes for gallery images. ``big`` is the frontend's name
#: for what processing writes as ``large``; the alias is kept so the viewers
#: keep working, but it resolves through this map rather than being pasted into
#: a filename.
GALLERY_SUFFIXES = {
    "small": "_small.jpg",
    "medium": "_medium.jpg",
    "large": "_large.jpg",
    "big": "_large.jpg",
}

#: Where blocks are read from. Deliberately fixed: the block editor always works
#: against the full-size renderings.
BLOCK_DIRECTORY = "big"

#: Tile formats a DZI pyramid may have been written in, in preference order.
TILE_EXTENSIONS = (".jpeg", ".png")


class ViewerError(Exception):
    """Something about the request cannot be served.

    Carries the status the caller should answer with, because "this record has
    no pages" (404) and "that size does not exist" (400) are different answers
    and the original returned 500 for both.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


# ---------------------------------------------------------------------------
# Resolving stored paths
# ---------------------------------------------------------------------------


def file_processing_of(record: dict) -> dict:
    """The ``fileProcessing`` block, or a 404-shaped refusal.

    The original tested ``if 'processing' not in record:`` and then subscripted
    ``record['processing']`` inside that branch, so an unprocessed record raised
    ``KeyError`` rather than reaching the prepared message.
    """
    processing = record.get("processing")
    entry = processing.get("fileProcessing") if isinstance(processing, dict) else None
    if not isinstance(entry, dict) or not entry.get("path"):
        raise ViewerError(_("Record has not been processed"), 404)
    return entry


def _web_root():
    return get_settings().web_files_path


def _page_directory(stored_path: str, size: str):
    """The directory holding a document's page renderings at ``size``.

    ``size`` indexes ``PAGE_DIRECTORIES``; it is never concatenated into a path.
    """
    directory = PAGE_DIRECTORIES.get(size)
    if directory is None:
        raise ViewerError(
            _('Unknown size "{size}"', size=str(size)[:40]),
            400,
        )
    return filestore.resolve_within(_web_root(), stored_path, "web", directory)


def _sorted_pages(directory) -> list:
    """The page files in a document's rendering directory, in page order.

    A missing directory means processing wrote nothing, not that the caller
    asked for something malformed.
    """
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        raise ViewerError(_("Record does not have files"), 404) from None
    except NotADirectoryError:
        raise ViewerError(_("Record does not have files"), 404) from None
    return [directory / name for name in names]


def _validate_index(value, count: int) -> int:
    """A page index the caller supplied, checked against the real page count.

    Negative indices are refused rather than wrapping to the end of the
    document, which is what bare list subscripting does.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ViewerError(_("Invalid page number"), 400) from None

    if value < 0:
        raise ViewerError(_("Invalid page number"), 400)
    if value >= count:
        raise ViewerError(_("Record does not have that many pages"), 404)
    return value


def _encoded(path) -> dict:
    with open(path, "rb") as handle:
        return {
            "filename": os.path.basename(path),
            "data": base64.b64encode(handle.read()).decode("utf-8"),
        }


def image_dimensions(path) -> tuple[int, int]:
    """``(width, height)``, read from the image header.

    Split out so it can be substituted in tests without writing real JPEGs, and
    so the Pillow import stays lazy - importing this module must not require an
    imaging library.
    """
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def _aspect_ratio(path) -> float:
    width, height = image_dimensions(path)
    if not height:
        raise ViewerError(_("Record does not have files"), 404)
    return width / height


# ---------------------------------------------------------------------------
# Document detail
# ---------------------------------------------------------------------------


def document_detail(record: dict) -> dict:
    """How many pages a document has, and the shape of its first one.

    The viewer sizes its canvas from this before requesting any page, so it is
    the first call the document reader makes.
    """
    entry = file_processing_of(record)
    kind = entry.get("type")
    stored = entry["path"]

    if kind == "document":
        pages = _sorted_pages(_page_directory(stored, "small"))
        if not pages:
            raise ViewerError(_("Record does not have files"), 404)
        return {"pages": len(pages), "aspect_ratio": _aspect_ratio(pages[0])}

    if kind == "image":
        path = filestore.resolve_within(_web_root(), stored + GALLERY_SUFFIXES["small"])
        if not path.is_file():
            raise ViewerError(_("Record does not have files"), 404)
        return {"pages": 1, "aspect_ratio": _aspect_ratio(path)}

    # The original returned None here, which Flask turned into a 500 with an
    # empty body - the viewer showed a spinner forever.
    raise ViewerError(_("Record is not a document"), 400)


# ---------------------------------------------------------------------------
# Document pages
# ---------------------------------------------------------------------------


def page_images(record: dict, pages, size: str) -> list[dict]:
    """Base64 renderings of the requested pages.

    Serves ``image`` records as well as ``document`` ones, and that is not an
    indulgence - it is what `ImageViewer.tsx` needs. The image reader is built
    on the same two calls as the document reader: ``document_detail`` to size
    the canvas, then this to fetch the page. It calls the viewer's ``init``
    with four arguments, so ``isDocument`` keeps its default of ``true`` and
    the request carries ``gallery: false``; there is no separate image route
    for it to use. Refusing a non-document here while ``document_detail``
    happily answers ``{"pages": 1}`` for one leaves the reader reporting a page
    it can never fetch, and an image record renders as an empty frame.

    An image has exactly one page, so every requested index resolves to the
    same derivative and the reply is a single entry - what the original
    returned, and what the reader draws.
    """
    if not isinstance(pages, list):
        raise ViewerError(_("You must specify a page"), 400)
    if not pages:
        return []

    entry = file_processing_of(record)
    kind = entry.get("type")

    if kind == "image":
        # One page, so index 0 is the only valid one - but it is still CHECKED.
        # Serving images from this route must not carve out a record kind where
        # a supplied index goes unvalidated; that invariant is why `_validate_index`
        # exists and it holds for every kind or it is not an invariant.
        for page in pages:
            _validate_index(page, 1)
        return [_gallery_page(entry["path"], size)]

    if kind != "document":
        raise ViewerError(_("Record is not a document"), 400)

    available = _sorted_pages(_page_directory(entry["path"], size))
    return [_encoded(available[_validate_index(page, len(available))]) for page in pages]


def _gallery_page(stored_path: str, size: str) -> dict:
    """One image record's derivative, with the aspect ratio the reader needs.

    ``size`` indexes ``GALLERY_SUFFIXES`` and is refused if it is not a key, so
    the caller's string never reaches the filesystem - the reader asks for
    ``big``, which processing wrote as ``_large.jpg``.
    """
    suffix = GALLERY_SUFFIXES.get(size)
    if suffix is None:
        raise ViewerError(_('Unknown size "{size}"', size=str(size)[:40]), 400)

    path = filestore.resolve_within(_web_root(), stored_path + suffix)
    if not path.is_file():
        raise ViewerError(_("File not found"), 404)

    width, height = image_dimensions(path)
    return {**_encoded(path), "aspect_ratio": (width / height) if height else 0}


# ---------------------------------------------------------------------------
# Gallery images
# ---------------------------------------------------------------------------


def gallery_records(resource: dict) -> list[dict]:
    """A resource's image records, in the curator's display order.

    The order map is keyed by the string ids held in ``filesObj`` and looked up
    with the record's own ``_id``, which is an ``ObjectId`` - so it is
    stringified before the lookup. The original compared the two directly,
    every lookup missed, and every gallery fell back to Mongo's natural order.
    """
    files = [f for f in (resource.get("filesObj") or []) if isinstance(f, dict) and f.get("id")]
    if not files:
        return []

    order_of = {str(f["id"]): f.get("order", 0) for f in files}
    object_ids = [oid for oid in (_object_id(f["id"]) for f in files) if oid is not None]

    records = list(
        _mongo().get_all_records(
            "records",
            {"_id": {"$in": object_ids}, "processing.fileProcessing.type": "image"},
            fields={"processing": 1},
        )
    )
    records.sort(key=lambda record: order_of.get(str(record["_id"]), float("inf")))
    return records


def _object_id(value):
    from bson.objectid import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return None


def gallery_images(resource: dict, pages, size: str) -> list[dict]:
    """Base64 renderings of a slice of a resource's image gallery.

    ``pages`` is the frontend's window into the gallery: its first entry is the
    offset and its length is how many images to return. Preserved as-is, odd as
    it is, because the viewer builds the list that way.
    """
    if not isinstance(pages, list) or not pages:
        return []

    suffix = GALLERY_SUFFIXES.get(size)
    if suffix is None:
        raise ViewerError(_('Unknown size "{size}"', size=str(size)[:40]), 400)

    records = gallery_records(resource)
    offset = _validate_offset(pages[0])
    window = records[offset : offset + len(pages)]

    result = []
    for record in window:
        entry = file_processing_of(record)
        path = filestore.resolve_within(_web_root(), entry["path"] + suffix)
        if not path.is_file():
            # One missing derivative used to abort the whole batch, blanking a
            # gallery page because a single image had not finished processing.
            logger.info("Gallery derivative missing: %s", path)
            continue
        payload = _encoded(path)
        payload["aspect_ratio"] = _aspect_ratio(path)
        result.append(payload)

    return result


def _validate_offset(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ViewerError(_("Invalid page number"), 400) from None
    if value < 0:
        raise ViewerError(_("Invalid page number"), 400)
    return value


# ---------------------------------------------------------------------------
# Deep zoom
# ---------------------------------------------------------------------------


def dzi_data(resource: dict, pages, payload: dict) -> dict:
    """The ``.dzi`` descriptor, or one tile, of a gallery image.

    OpenSeadragon asks for the descriptor once and then for tiles by level, row
    and column as the user zooms. Those three are integers and are validated as
    such - they name directories and files, and the tile path is the one place
    in this module where client numbers reach the filesystem.
    """
    index = _validate_offset(pages[0]) if isinstance(pages, list) and pages else 0
    kind = (payload or {}).get("type", "xml")

    records = [
        record
        for record in gallery_records(resource)
        if (record.get("processing") or {}).get("fileProcessing", {}).get("dzi")
    ]
    if index >= len(records):
        raise ViewerError(_("Image index out of range"), 404)

    stored = file_processing_of(records[index])["path"]

    if kind == "xml":
        descriptor = filestore.resolve_within(_web_root(), stored + "_tiles.dzi")
        if not descriptor.is_file():
            raise ViewerError(_("DZI file not found"), 404)
        return {"type": "xml", "data": descriptor.read_text()}

    if kind != "tile":
        raise ViewerError(_("Invalid dzi_payload type, expected xml or tile"), 400)

    level = _tile_number(payload.get("level"))
    col = _tile_number(payload.get("col"))
    row = _tile_number(payload.get("row"))

    for extension in TILE_EXTENSIONS:
        tile = filestore.resolve_within(
            _web_root(), stored + "_tiles_files", str(level), f"{col}_{row}{extension}"
        )
        if tile.is_file():
            return {
                "type": "tile",
                "data": base64.b64encode(tile.read_bytes()).decode("utf-8"),
                "format": extension.lstrip("."),
            }

    raise ViewerError(_("Tile not found"), 404)


def _tile_number(value) -> int:
    """A tile coordinate. Must be a non-negative integer.

    Coordinates become path segments, so a string like ``..`` would otherwise
    climb out of the pyramid directory. ``resolve_within`` would catch it, but
    refusing it here gives the caller the real reason.
    """
    if value is None:
        raise ViewerError(_("level, col, and row are required for tile requests"), 400)
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ViewerError(_("level, col, and row must be integers"), 400) from None
    if value < 0:
        raise ViewerError(_("level, col, and row must be integers"), 400)
    return value


# ---------------------------------------------------------------------------
# OCR blocks
# ---------------------------------------------------------------------------


def blocks_for_page(record: dict, page, slug: str, block: str) -> dict | list:
    """The OCR layout of one page: either its blocks, or its flattened words.

    ``page`` is 1-indexed here, unlike ``page_images``. That is the stored
    convention and both the block editor and the writes in ``blocks.py`` use
    it, so it is kept rather than quietly renumbered.
    """
    if block not in ("blocks", "words"):
        raise ViewerError(_("Record does not have blocks or words"), 400)

    entry = file_processing_of(record)
    kind = entry.get("type")
    processing = record.get("processing") or {}

    if slug not in processing:
        raise ViewerError(_("Record has not been processed with {slug}", slug=slug), 404)

    if kind == "image":
        result = processing[slug].get("result") or {}
        if "blocks" not in result:
            raise ViewerError(_("Record does not have blocks"), 400)
        return result["blocks"]

    if kind != "document":
        raise ViewerError(_("Record is not a document"), 400)

    available = _sorted_pages(_page_directory(entry["path"], BLOCK_DIRECTORY))
    index = _validate_index(_one_indexed(page) , len(available))

    result = page_result(record, slug, index)
    labels = processing[slug].get("labels") or []

    if block == "words":
        words: list = []
        for candidate in result.get("blocks") or []:
            words.extend(candidate.get("words") or [])
        return {"page": index + 1, "words": words, "labels": labels}

    # `blocks`. Word-level geometry is dropped: it is by far the bulk of the
    # payload and the block view does not draw it.
    blocks = [
        {key: value for key, value in candidate.items() if key != "words"}
        for candidate in result.get("blocks") or []
    ]
    return {**{k: v for k, v in result.items() if k != "blocks"}, "blocks": blocks, "labels": labels}


def _one_indexed(page) -> int:
    """Convert the stored 1-indexed page number to a list index."""
    if isinstance(page, bool) or not isinstance(page, int):
        try:
            page = int(page)
        except (TypeError, ValueError):
            raise ViewerError(_("Invalid page number"), 400) from None
    return page - 1


def page_result(record: dict, slug: str, index: int) -> dict:
    """One page's entry from a processing result, chunked or inline.

    Large OCR results are stored as chunk documents in a separate collection
    rather than inline in the record, so both shapes have to be read.
    """
    entry = (record.get("processing") or {}).get(slug) or {}
    storage = entry.get("result_storage") or {}

    if storage.get("type") == "chunked":
        result = load_chunked_result(record.get("_id"), storage)
    else:
        result = entry.get("result") or []

    if not isinstance(result, list) or index >= len(result):
        return {}
    return result[index] or {}


def load_chunked_result(record_id, storage: dict) -> list:
    """Reassemble a chunked processing result in chunk order."""
    collection = storage.get("collection")
    if not collection:
        return []

    filters = {"recordId": _object_id(record_id) or record_id}
    if storage.get("batchId"):
        filters["batchId"] = storage["batchId"]

    chunks = _mongo().get_all_records(
        collection, filters, sort=[("chunkIndex", 1)], fields={"_id": 0, "pages": 1}
    )

    pages: list = []
    for chunk in chunks:
        pages.extend(chunk.get("pages") or [])
    return pages
