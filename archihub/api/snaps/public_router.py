"""The unauthenticated snap route.

MOUNTED BEFORE THE AUTHENTICATED SNAP ROUTER - ``/snaps/public/{id}`` would
otherwise be captured by ``GET /snaps/{snap_id}``. ``app_factory`` asserts the
ordering at startup.

A public snap is one whose **record** is public. The snap's own ownership is
irrelevant here: what a snap exposes is a region of a record, so the question is
whether that record may be shown to nobody in particular. This is how article
blocks embed snaps into published pages made by one cataloguer and read by
everyone.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from archihub.api.records import public as record_public
from archihub.api.snaps import render, services
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/snaps", tags=["Snaps (public)"])

#: Identical for "no such snap" and "its record is not public". An anonymous
#: caller must not have a snap id confirmed. The original answered 500 with the
#: record's own refusal message for the second case, which both leaked the
#: distinction and reported a permission decision as a server fault.
MSG_NOT_FOUND = "Snap not found"


@router.get(
    "/public/{snap_id}",
    responses={
        200: {"description": "The snap, rendered"},
        404: {"description": "No such snap, or its record is not public"},
    },
)
def get_by_id(snap_id: str) -> Response:
    """Render a snap of a public record."""
    object_id = services._to_object_id(snap_id)
    if object_id is None:
        return JSONResponse(status_code=404, content={"msg": _(MSG_NOT_FOUND)})

    snap = services._mongo().get_record(
        services.COLLECTION,
        {"_id": object_id},
        fields={"record_id": 1, "data": 1, "type": 1},
    )
    if not snap:
        return JSONResponse(status_code=404, content={"msg": _(MSG_NOT_FOUND)})

    record, error = record_public.load_public(snap.get("record_id") or "")
    if error is not None:
        logger.info("Refused anonymous access to snap %s", snap_id)
        return JSONResponse(status_code=404, content={"msg": _(MSG_NOT_FOUND)})

    try:
        return render.render(snap, record)
    except render.RenderFailed as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})
