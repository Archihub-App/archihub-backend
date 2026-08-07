"""Record routes.

Port of ``app/api/records/routes.py``, read path first. Not yet ported: the
transcription and block-editing routes, the document/page viewers, and the
bulk download.

Role failures keep the legacy 401 pending the coordinated frontend flip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse, Response

from archihub.api.records import media, services
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

_RESPONSES = {
    401: {"description": "Missing/invalid token, or no access to this record"},
    404: {"description": "No such record"},
}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


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
