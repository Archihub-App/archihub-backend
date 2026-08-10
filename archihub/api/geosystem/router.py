"""Geosystem routes.

Port of ``app/api/geosystem/routes.py`` and ``public_routes.py``. Both routes
are unauthenticated, in both the legacy code and here.

That is not an oversight in either. ``/geosystem/level`` is called by
``GeoService.getAdminLevel`` with no ``Authorization`` header at all, and the
public explore map draws boundaries before anyone signs in. What these serve is
reference geography — the administrative divisions of a country — not anything
the archive holds.

The legacy ``routes.py`` imports ``jwt_required`` and never applies it, which
reads like an intention that was dropped. Kept unauthenticated because that is
the live contract; the defences are on the *inputs* instead (see
``services``): nothing from the request becomes a query operator, results are
capped, and the simplification retention is quantised.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from archihub.api.geosystem import services
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geosystem", tags=["Geosystem"])

_RESPONSES = {400: {"description": "The request does not describe a shape query"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


@router.post(
    "/level",
    responses={200: {"description": "Shapes at that administrative level"}, **_RESPONSES},
)
def get_level(body: dict = Body(default_factory=dict)) -> JSONResponse:
    """The boundary shapes at one administrative level.

    ``bounds`` narrows to the viewport and, when it is small enough, raises the
    detail: a tight rectangle drops to level 2 with a finer area threshold, so
    zooming in shows municipalities rather than the country outline.

    Slivers below the area threshold are dropped — at national zoom an offshore
    islet is a rendering cost with no visible result.
    """
    return _respond(services.get_level(body))


@router.post(
    "/polygon",
    responses={
        200: {"description": "One shape's outline, or every shape matching the filters"},
        404: {"description": "No shape with that identifier"},
        **_RESPONSES,
    },
)
def get_polygon(body: dict = Body(default_factory=dict)) -> JSONResponse:
    """One shape's outline by identifier, or the shapes under a parent.

    With ``ident`` this answers a single GeoJSON feature; without it, the list
    matching the remaining filters — capped, because a third administrative
    level runs to thousands of polygons and the original returned all of them,
    simplified, to an anonymous caller.

    ``retention`` is the fraction of vertices to keep. It is clamped and rounded
    before use: it keys a disk cache, and taking the caller's float verbatim
    meant one file per distinct value.
    """
    return _respond(services.get_shape(body))
