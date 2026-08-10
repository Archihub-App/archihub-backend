"""Snap routes.

Port of ``app/api/snaps/routes.py``. Three authenticated routes; the public
mirror is in ``public_router.py`` and mounts before this one.

The two checks a snap route makes are separate and both required: the snap
belongs to the caller (``services.load_own``), **and** the record it points at
is one the caller may see (``records.services.load_visible``). Ownership of a
snap is not permission to read what it points at - the record's access rights
may have been tightened since the snap was made.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, Response

from archihub.api.records import services as record_services
from archihub.api.snaps import render, services
from archihub.core.security.jwt import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/snaps", tags=["Snaps"])

_RESPONSES = {
    401: {"description": "Missing/invalid token, or the snap is not yours"},
    404: {"description": "No such snap"},
}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


@router.post(
    "",
    status_code=201,
    responses={
        201: {"description": "Snap created"},
        400: {"description": "An unusable box, page or time range"},
        **_RESPONSES,
    },
)
def create(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Save a snap of a record.

    The record must be one the caller can see. The original checked nothing at
    all here and relied on the read path to catch it, which meant any user could
    store a snap - and the record's filename - for anything in the archive.

    The stored coordinates are validated now rather than at render time: a snap
    is read back later, by other code, sometimes on another user's screen.
    """
    return _respond(services.create(current_user.username, body))


@router.delete(
    "/{snap_id}",
    status_code=204,
    responses={204: {"description": "Snap deleted"}, **_RESPONSES},
)
def delete(
    snap_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Delete one of your own snaps."""
    payload, status_code = services.delete(snap_id, current_user.username)
    if status_code == 204:
        # 204 means no body, and Starlette raises if one is attached.
        return Response(status_code=204)
    return JSONResponse(status_code=status_code, content=payload)


@router.get(
    "/{snap_id}",
    responses={
        200: {"description": "The snap, rendered"},
        400: {"description": "The snap cannot be rendered"},
        **_RESPONSES,
    },
)
def get_by_id(
    snap_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Render one of your own snaps.

    Two gates, deliberately. Owning the snap is not permission to read what it
    points at: a snap holds coordinates, not pixels, so the record is loaded
    through the ordinary visibility rule and a series reserved *after* the snap
    was taken stops rendering.
    """
    snap, error = services.load_own(snap_id, current_user.username)
    if error is not None:
        return _respond(error)

    record, error = record_services.load_visible(snap["record_id"], current_user.username)
    if error is not None:
        return _respond(error)

    try:
        return render.render(snap, record)
    except render.RenderFailed as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})
