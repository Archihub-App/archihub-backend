"""Record routes.

Port of ``app/api/records/routes.py``. Records are the files themselves: the
scans, recordings and documents that resources describe.

Every route that touches a record starts from ``services.load_visible``, which
returns the record or the *real* refusal - a 404 for one that does not exist,
a role failure for one the caller may not open. The original opened eight
functions with a check that collapsed both into 500 (BACKEND_FINDINGS F27);
routing everything through one guard is what stops that reappearing.

Role failures keep the legacy 401 pending the coordinated frontend flip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse, Response

from archihub.api.records import blocks, media, services, transcription, viewers
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["Records"])

require_admin = require_role_any("admin", status_code=LEGACY_ROLE_FAILURE_STATUS)
require_block_editor = require_role_any(
    "admin", "editor", status_code=LEGACY_ROLE_FAILURE_STATUS
)
require_transcriber = require_role_any(
    "admin", "editor", "transcriber", status_code=LEGACY_ROLE_FAILURE_STATUS
)

_RESPONSES = {
    401: {"description": "Missing/invalid token, or no access to this record"},
    404: {"description": "No such record"},
}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


def _viewer_error(exc: viewers.ViewerError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})


@router.post(
    "",
    responses={200: {"description": "Matching records"}, **_RESPONSES},
)
def get_all(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Search records with a Mongo filter.

    Administrator-only, and deliberately flexible - which is why the filter is
    screened for server-side JavaScript and expression operators rather than
    reduced to a field allowlist. **The safety of that choice rests on this
    role check**; see `services.reject_dangerous_operators`.

    An empty result is 200 with an empty list. The legacy 404 made "nothing
    matched" indistinguishable from a wrong endpoint, and broke pagination past
    the last page.
    """
    return _respond(services.get_by_filters(body, current_user.username))


@router.post(
    "/galleryinfo",
    responses={200: {"description": "The record at that gallery position"}, **_RESPONSES},
)
def get_by_gallery_index(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The nth image of a resource's gallery, in the curator's display order."""
    return _respond(services.get_by_gallery_index(body, current_user.username))


@router.post(
    "/download",
    responses={
        200: {"description": "The file, as an attachment"},
        400: {"description": "Downloads are disabled, or the type is not one this record has"},
        **_RESPONSES,
    },
)
def download(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Download a record's archival master or its web derivative.

    Declared before ``/{record_id}`` so the literal segment wins the match.

    An instance can switch distribution off entirely through the
    ``files_download`` capability; that check comes first, before the record is
    even looked up, so a disabled instance does not confirm what exists.
    """
    record_id = body.get("id")
    if not record_id:
        return JSONResponse(status_code=400, content={"msg": _("id is missing")})

    if not media.downloads_enabled():
        return JSONResponse(
            status_code=400, content={"msg": _("Files download isn't active")}
        )

    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return _respond(error)

    try:
        return media.download(record, body.get("type") or "original")
    except media.DownloadRefused as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})


@router.post(
    "/setBlock",
    responses={200: {"description": "Block added"}, **_RESPONSES},
)
def add_block(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_block_editor),
) -> JSONResponse:
    """Add an OCR block the recogniser missed.

    The role gate above is necessary but not sufficient: ``blocks.may_edit``
    additionally requires that the caller can *see* the record, so an editor
    cannot rewrite the transcription of a series whose access rights they do
    not hold.
    """
    return _respond(blocks.add(current_user.username, body))


@router.put(
    "/setBlock",
    responses={200: {"description": "Block updated"}, **_RESPONSES},
)
def update_block(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_block_editor),
) -> JSONResponse:
    """Correct an OCR block's bounding box or content."""
    return _respond(blocks.update(current_user.username, body))


@router.delete(
    "/setBlock",
    responses={200: {"description": "Block deleted"}, **_RESPONSES},
)
def delete_block(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_block_editor),
) -> JSONResponse:
    """Delete an OCR block the recogniser invented."""
    return _respond(blocks.delete(current_user.username, body))


@router.get(
    "/favcount/{record_id}",
    dependencies=[Depends(get_current_user)],
    responses={200: {"description": "How many users have favourited this file"}, **_RESPONSES},
)
def favcount(record_id: str) -> JSONResponse:
    """Declared before ``/{record_id}`` so the literal segment wins the match."""
    return _respond(services.get_fav_count(record_id))


@router.get(
    "/{record_id}/stream",
    responses={
        200: {"description": "The web derivative, with Range support"},
        400: {"description": "An unusable time range"},
        **_RESPONSES,
    },
)
def get_stream_by_id(
    record_id: str,
    size: str = Query("large", description="Image derivative size: small, medium or large"),
    startMs: float | None = Query(None, description="Fragment start, milliseconds"),
    endMs: float | None = Query(None, description="Fragment end, milliseconds"),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Serve a record's web-optimised derivative.

    Inline and Range-capable, because this is what the audio and video elements
    fetch and seek within. The archival master is never served here.

    Fragment extraction (``startMs``/``endMs``) is not yet ported - it shells
    out to ffmpeg and lands with the rest of the media pipeline. An explicit
    501 is returned rather than silently serving the whole file, which would
    look to the caller like seeking that does not work.
    """
    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return _respond(error)

    try:
        bounds = media.parse_fragment_bounds(startMs, endMs)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    if bounds is not None:
        return JSONResponse(
            status_code=501,
            content={"msg": _("Fragment extraction is not available yet")},
        )

    try:
        return media.stream(record, size)
    except media.NotStreamable as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"msg": _("Record does not exist")})


@router.get(
    "/{record_id}/document",
    responses={
        200: {"description": "Page count and page aspect ratio"},
        400: {"description": "The record is not a document or an image"},
        **_RESPONSES,
    },
)
def get_document(
    record_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """How many pages a document has, and the shape of its first page.

    The viewer sizes its canvas from this before requesting any page, so it is
    the first call the document reader makes.
    """
    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return _respond(error)

    try:
        return JSONResponse(status_code=200, content=viewers.document_detail(record))
    except viewers.ViewerError as exc:
        return _viewer_error(exc)


@router.post(
    "/{record_id}/pages",
    responses={
        200: {"description": "The requested page images, base64 encoded"},
        400: {"description": "An unknown size, or a page index that is not a number"},
        **_RESPONSES,
    },
)
def get_pages(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Page images of a document, or a window into a resource's image gallery.

    ``gallery: true`` changes what ``record_id`` means - it is a **resource**
    id, and the images come from that resource's gallery rather than from one
    record's pages. The two therefore check different permissions, and the
    branch happens before either is loaded.

    ``size`` selects a key in a fixed map; it is never joined into a path. See
    the module docstring of ``viewers.py`` for why that matters here.
    """
    pages = body.get("pages") or []
    size = body.get("size") or "small"

    try:
        if body.get("gallery") is True:
            from archihub.api.resources.services import load_visible as load_resource

            resource, error = load_resource(
                record_id, current_user.username, fields={"filesObj": 1}
            )
            if error is not None:
                return _respond(error)

            if body.get("dzi") and body.get("dzi_payload"):
                return JSONResponse(
                    status_code=200,
                    content=viewers.dzi_data(resource, pages, body["dzi_payload"]),
                )
            return JSONResponse(
                status_code=200, content=viewers.gallery_images(resource, pages, size)
            )

        record, error = services.load_visible(record_id, current_user.username)
        if error is not None:
            return _respond(error)
        return JSONResponse(status_code=200, content=viewers.page_images(record, pages, size))
    except viewers.ViewerError as exc:
        return _viewer_error(exc)


@router.post(
    "/{record_id}/blocks",
    responses={
        200: {"description": "The page's blocks, or its flattened words"},
        400: {"description": "Missing page/block/slug, or a record with no block layout"},
        **_RESPONSES,
    },
)
def get_blocks(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The OCR layout of one page.

    ``record_id`` may also be a **resource** id, in which case ``page`` is read
    as a gallery index and the record at that position is used - this is how the
    gallery viewer asks for the blocks of the image it is showing. The record
    lookup is tried first and the gallery interpretation only on a miss, which
    is the original's behaviour.

    The original answered 500 for a missing ``page``/``block``/``slug``; these
    are malformed requests and answer 400.
    """
    # Spelled out rather than built from a loop variable: these three are
    # existing msgids in the translation catalogue, and a parameterised
    # "You must specify a {field}" would match none of them and ship untranslated.
    if "page" not in body:
        return JSONResponse(status_code=400, content={"msg": _("You must specify a page")})
    if "block" not in body:
        return JSONResponse(status_code=400, content={"msg": _("You must specify a block")})
    if "slug" not in body:
        return JSONResponse(status_code=400, content={"msg": _("You must specify a slug")})

    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        gallery, gallery_status = services.get_by_gallery_index(
            {"id": record_id, "index": body["page"]}, current_user.username
        )
        if gallery_status != 200:
            return _respond(error)
        record, error = services.load_visible(
            gallery["_id"]["$oid"], current_user.username
        )
        if error is not None:
            return _respond(error)

    try:
        result = viewers.blocks_for_page(record, body["page"], body["slug"], body["block"])
    except viewers.ViewerError as exc:
        return _viewer_error(exc)

    return JSONResponse(status_code=200, content=services.parse_result(result))


@router.post(
    "/{record_id}/metadata",
    responses={
        200: {"description": "The processing's extracted metadata"},
        404: {"description": "No such record, or no such processing on it"},
        401: _RESPONSES[401],
    },
)
def get_processing_metadata(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The metadata a named processing extracted from this record.

    Filtered to the same presentable EXIF subset the detail route uses - this
    reads the same stored block, and serving it whole would hand back the GPS
    coordinates and camera serial numbers that route is careful to drop.
    """
    slug = body.get("slug")
    if not slug:
        return JSONResponse(status_code=400, content={"msg": _("slug is missing")})

    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return _respond(error)

    return _respond(services.get_processing_metadata(record, slug))


@router.post(
    "/{record_id}/result",
    responses={
        200: {"description": "The processing's result"},
        404: {"description": "No such record, or no such processing on it"},
        401: _RESPONSES[401],
    },
)
def get_processing_result(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The full output of a named processing, reassembled if it was chunked."""
    slug = body.get("slug")
    if not slug:
        return JSONResponse(status_code=400, content={"msg": _("slug is missing")})

    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return _respond(error)

    return _respond(services.get_processing_result(record, slug))


@router.post(
    "/{record_id}/transcription",
    responses={
        200: {"description": "One page of the transcription"},
        404: {"description": "No such record, or it has no transcription under that slug"},
        401: _RESPONSES[401],
    },
)
def get_transcription(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """A page of a record's transcription, with its label and speaker summaries.

    Transcripts of long recordings run to tens of thousands of characters, so
    they are paginated by character count with segments kept whole.
    """
    slug = body.get("slug")
    if not slug:
        return JSONResponse(status_code=400, content={"msg": _("slug is missing")})

    record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return _respond(error)

    try:
        result = transcription.build(record, slug, body.get("page", 0))
    except transcription.TranscriptionError as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})

    return JSONResponse(status_code=200, content=services.parse_result(result))


@router.put(
    "/{record_id}/edit-transcription",
    responses={
        200: {"description": "Segment edited"},
        400: {"description": "A missing field, or an index outside the transcript"},
        **_RESPONSES,
    },
)
def edit_transcription(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_transcriber),
) -> JSONResponse:
    """Correct one transcription segment's text, timing and speaker.

    Two separate checks, and both are needed. The role gate above admits
    administrators, editors and transcribers; ``transcription.may_edit`` then
    requires that a *transcriber* hold an open task on this specific record,
    because for them the assignment is the grant and the role alone is not.
    """
    return _respond(_edit_transcription(transcription.edit_segment, record_id, body, current_user))


@router.put(
    "/{record_id}/edit-transcription-speaker",
    responses={
        200: {"description": "Speaker renamed"},
        400: {"description": "Missing slug, speaker or oldSpeaker"},
        **_RESPONSES,
    },
)
def edit_transcription_speaker(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_transcriber),
) -> JSONResponse:
    """Rename one speaker across every segment attributed to them."""
    return _respond(
        _edit_transcription(transcription.rename_speaker, record_id, body, current_user)
    )


@router.delete(
    "/{record_id}/edit-transcription",
    responses={
        200: {"description": "Segment deleted"},
        400: {"description": "Missing slug/index, or an index outside the transcript"},
        **_RESPONSES,
    },
)
def delete_transcription_segment(
    record_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_transcriber),
) -> JSONResponse:
    """Remove one segment from a transcript."""
    return _respond(
        _edit_transcription(transcription.delete_segment, record_id, body, current_user)
    )


def _edit_transcription(operation, record_id: str, body: dict, current_user: CurrentUser):
    """Shared preamble for the three transcription writes.

    Visibility first, then the per-record editing right. Stated once so the
    three routes cannot drift apart - which is how the originals ended up each
    checking a slightly different subset.
    """
    _record, error = services.load_visible(record_id, current_user.username)
    if error is not None:
        return error

    if not transcription.may_edit(record_id, current_user.username):
        logger.info("Denied %s transcription editing on %s", current_user.username, record_id)
        return (
            {"msg": _("You do not have permission to edit this transcription")},
            LEGACY_ROLE_FAILURE_STATUS,
        )

    return operation(record_id, body, current_user.username)


@router.get(
    "/{record_id}",
    responses={200: {"description": "The record"}, **_RESPONSES},
)
def get_by_id(
    record_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """One record, with its parent resources resolved for display.

    ``filepath`` is deliberately absent: it is where the file lives on the
    server's disk and has no business reaching a browser.
    """
    return _respond(services.get_by_id(record_id, current_user.username))
