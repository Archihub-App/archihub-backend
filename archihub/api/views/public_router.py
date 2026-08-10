"""Unauthenticated view routes.

The public site is built from these: one call lists the views that exist, the
other describes the one being browsed. Neither takes a caller.

MOUNTED BEFORE THE AUTHENTICATED VIEW ROUTER. ``GET /views`` and
``GET /views/{view_id}`` do not collide on their own - different segment counts
- but ``app_factory`` asserts the ordering for the whole family rather than
reasoning about each pair.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from archihub.api.views import services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/views", tags=["Views (public)"])


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


@router.get(
    "",
    responses={200: {"description": "Every view, as cards"}},
)
def get_all() -> JSONResponse:
    """List the views this archive publishes.

    Each card carries its thumbnail inline as a data URI, because a URL per card
    would be a request per card - each needing its own visibility decision.
    A thumbnail is served only when it is a record actually attached to that
    view and carries no access rights of its own.
    """
    return _respond(services.get_all())


@router.get(
    "/info/{view_slug}",
    responses={
        200: {"description": "Everything the explore screen needs"},
        404: {"description": "No view with that slug"},
    },
)
def get_view_info(view_slug: str) -> JSONResponse:
    """Describe one view: its content types, its tree, and what it holds.

    The file counts cover **public material only**. The original counted every
    matching record with no access-rights or publication filter, on this
    unauthenticated route, so the totals disclosed how much reserved and
    unpublished material an archive holds.

    An unknown slug is a 404. The original read the view's fields before
    checking whether it had found one, so it raised ``TypeError`` on ``None``
    and answered 500.
    """
    return _respond(services.get_view_info(view_slug))
