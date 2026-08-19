"""View routes.

Port of ``app/api/views/routes.py``. Four authenticated routes; the two public
ones are in ``public_router.py`` and mount before this one.

Create and update are multipart, because a view carries a thumbnail image: the
view itself travels as a JSON string in ``data``, matching the shape the
frontend sends and the same convention ``resources`` uses.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from archihub.api.records.storage import IncomingFile, UnsupportedFileType
from archihub.api.views import services
from archihub.core.files import UnsupportedFile, UploadTooLarge
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/views", tags=["Views"])

require_editor = require_role_any("admin", "editor")

_RESPONSES = {
    401: {"description": "Missing/invalid token, or not an editor"},
    404: {"description": "No such view"},
}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


def _parse_data(data: str) -> dict:
    try:
        parsed = json.loads(data)
    except (TypeError, ValueError):
        raise ValueError(_("The data field is not valid JSON")) from None
    if not isinstance(parsed, dict):
        raise ValueError(_("The data field must be an object"))
    return parsed


def _incoming(uploads: list[UploadFile] | None) -> list[IncomingFile]:
    return [
        IncomingFile.from_upload(upload, tag=services.THUMBNAIL_TAG)
        for upload in (uploads or [])
    ]


def _write(call) -> JSONResponse:
    try:
        return _respond(call())
    except UploadTooLarge as exc:
        return JSONResponse(status_code=413, content={"msg": str(exc)})
    except (UnsupportedFileType, UnsupportedFile, ValueError) as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})


@router.post(
    "",
    status_code=201,
    responses={
        201: {"description": "View created"},
        400: {"description": "Missing fields, or an unusable thumbnail"},
        409: {"description": "That slug is already taken"},
        **_RESPONSES,
    },
)
def create(
    data: str = Form(..., description="JSON document describing the view"),
    files: list[UploadFile] = File(default_factory=list),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Create a view, optionally with its thumbnail.

    ``filesObj`` is not a field a client may set - the thumbnail comes from the
    upload and nothing else. The original passed the body straight into its
    model, which declares ``filesObj``, so a view's thumbnail could be pointed
    at any record in the archive and then published through the unauthenticated
    listing.
    """
    try:
        body = _parse_data(data)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    return _write(lambda: services.create(body, current_user.username, _incoming(files)))


@router.put(
    "/{view_id}",
    responses={
        200: {"description": "View updated"},
        400: {"description": "Nothing to update, or an unusable thumbnail"},
        409: {"description": "That slug is already taken"},
        **_RESPONSES,
    },
)
def update(
    view_id: str,
    data: str = Form(..., description="JSON document with the fields to change"),
    files: list[UploadFile] = File(default_factory=list),
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Edit a view, optionally replacing its thumbnail.

    Replacing it detaches the old record first, so the previous thumbnail is
    retired rather than left behind pointing at the view.
    """
    try:
        body = _parse_data(data)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    return _write(
        lambda: services.update(view_id, body, current_user.username, _incoming(files))
    )


@router.delete(
    "/{view_id}",
    responses={200: {"description": "View deleted"}, **_RESPONSES},
)
def delete(
    view_id: str,
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """Delete a view and retire the thumbnail it owned.

    A view that does not exist is a 404. The original deleted nothing and
    reported success, so a stale id in the interface looked like it had worked.
    """
    return _respond(services.delete(view_id, current_user.username))


@router.get(
    "/{view_id}",
    responses={200: {"description": "The view"}, **_RESPONSES},
)
def get_by_id(
    view_id: str,
    current_user: CurrentUser = Depends(require_editor),
) -> JSONResponse:
    """One view, for the editor that maintains it."""
    return _respond(services.get(view_id))
