"""Unauthenticated record routes.

MOUNTED BEFORE THE AUTHENTICATED RECORD ROUTER, and it has to be: these paths
begin with the literal segment ``public``, which would otherwise be captured by
``GET /records/{record_id}``. See ``core/app_factory.py``, where the ordering is
asserted rather than left to chance.

No route here takes a dependency on a caller. Every one begins at
``public.load_public``, which answers 404 both for a record that does not exist
and for one that is not public - a public endpoint must never say more than
"no".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, Response

from archihub.api.records import media, public, viewers
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["Records (public)"])

_RESPONSES = {404: {"description": "No such record, or it is not public"}}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


@router.post(
    "/public/galleryinfo",
    responses={200: {"description": "The record at that gallery position"}, **_RESPONSES},
)
def get_by_gallery_index(body: dict = Body(default_factory=dict)) -> JSONResponse:
    """The nth image of a public resource's gallery, in the curator's order.

    Declared before ``/public/{record_id}`` so the literal segment wins.
    """
    return _respond(public.get_by_gallery_index(body))


@router.post(
    "/public/download",
    responses={
        200: {"description": "The file, as an attachment"},
        400: {"description": "Downloads are disabled for this instance"},
        **_RESPONSES,
    },
)
def download(body: dict = Body(default_factory=dict)) -> Response:
    """Download a public record's archival master or its web derivative.

    Unlike the legacy public route, this honours the instance's
    ``files_download`` capability - an archive that has switched downloads off
    now has them off here too.
    """
    record_id = body.get("id")
    if not record_id:
        return JSONResponse(status_code=400, content={"msg": _("id is missing")})

    try:
        result = public.download(record_id, body.get("type") or "original")
    except media.DownloadRefused as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})

    return _respond(result) if isinstance(result, tuple) else result


@router.get(
    "/public/{record_id}/stream",
    responses={200: {"description": "The web derivative, with Range support"}, **_RESPONSES},
)
def stream(
    record_id: str,
    size: str = Query("large", description="Image derivative size: small, medium or large"),
) -> Response:
    """Serve a public record's web-optimised derivative."""
    try:
        result = public.stream(record_id, size)
    except media.NotStreamable as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"msg": _("Record does not exist")})

    return _respond(result) if isinstance(result, tuple) else result


@router.post(
    "/public/{record_id}/transcription",
    responses={200: {"description": "One page of the transcription"}, **_RESPONSES},
)
def get_transcription(record_id: str, body: dict = Body(default_factory=dict)) -> JSONResponse:
    """A page of a public record's transcription."""
    slug = body.get("slug")
    if not slug:
        return JSONResponse(status_code=400, content={"msg": _("slug is missing")})

    return _respond(public.get_transcription(record_id, slug, body.get("page", 0)))


@router.post(
    "/public/{record_id}/pages",
    responses={
        200: {"description": "The requested page images, base64 encoded"},
        400: {"description": "An unknown size, or a page index that is not a number"},
        **_RESPONSES,
    },
)
def get_pages(record_id: str, body: dict = Body(default_factory=dict)) -> JSONResponse:
    """Page images of a public document, or a window into a public gallery.

    ``gallery: true`` reinterprets ``record_id`` as a **resource** id, exactly
    as on the authenticated route - and so checks the resource's public rule
    rather than the record's.
    """
    pages = body.get("pages") or []
    size = body.get("size") or "small"

    try:
        if body.get("gallery") is True:
            resource, error = public.load_public_resource(record_id, {"filesObj": 1})
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

        record, error = public.load_public(record_id)
        if error is not None:
            return _respond(error)
        return JSONResponse(status_code=200, content=viewers.page_images(record, pages, size))
    except viewers.ViewerError as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})


@router.get(
    "/public/{record_id}",
    responses={200: {"description": "The record"}, **_RESPONSES},
)
def get_by_id(record_id: str) -> JSONResponse:
    """One public record, with its parent resources resolved for display."""
    return _respond(public.get_by_id(record_id))
