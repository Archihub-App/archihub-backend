"""Controlled-vocabulary routes.

Port of ``app/api/lists/routes.py``. All five routes require admin OR editor,
and all keep the legacy **401** for role failures pending the coordinated
frontend flip - see ``LEGACY_ROLE_FAILURE_STATUS``.

Success responses are byte-identical to the legacy ones. Error responses are
corrected: see the module docstring in ``services.py`` for why the previous ones
returned HTTP 200 with a JSON array body.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.lists import services
from archihub.api.lists.schemas import ListCreate, ListUpdate
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lists", tags=["Lists"])

require_admin_or_editor = require_role_any(
    "admin", "editor", status_code=LEGACY_ROLE_FAILURE_STATUS
)

_ROLE_RESPONSES = {401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


@router.get(
    "",
    responses={200: {"description": "All lists, as name + id"}, **_ROLE_RESPONSES},
)
def get_all(current_user: CurrentUser = Depends(require_admin_or_editor)) -> JSONResponse:
    """Get every controlled vocabulary, alphabetically, as ``{name, id}``."""
    return _respond(services.get_all())


@router.post(
    "",
    responses={201: {"description": "List created"}, **_ROLE_RESPONSES},
)
def create(
    body: ListCreate = Body(...),
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Create a vocabulary and its options.

    Options are stored as their own documents; the list holds an ordered array
    of their ids.
    """
    return _respond(services.create(body.model_dump(exclude_unset=True), current_user.username))


@router.get(
    "/{list_id}",
    responses={
        200: {"description": "The list, with its options resolved and ordered"},
        404: {"description": "List not found"},
        **_ROLE_RESPONSES,
    },
)
def get_by_id(
    list_id: str,
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Get one vocabulary by id, with its options resolved to ``{id, term}``.

    Lists are addressed by id. There is no slug-based lookup - `lists` documents
    carry no slug field, and the legacy function that queried by one could never
    match anything.
    """
    return _respond(services.get_by_id(list_id))


@router.put(
    "/{list_id}",
    responses={
        200: {"description": "List updated"},
        404: {"description": "List not found"},
        **_ROLE_RESPONSES,
    },
)
def update_by_id(
    list_id: str,
    body: ListUpdate = Body(...),
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Update a vocabulary, reconciling its options.

    Options carrying an id are updated, options without one are created, and
    options flagged ``deleted`` are removed from the list. A patch that omits
    ``options`` entirely leaves them untouched.
    """
    return _respond(
        services.update_by_id(list_id, body.model_dump(exclude_unset=True), current_user.username)
    )


@router.delete(
    "/{list_id}",
    responses={
        200: {"description": "List deleted"},
        404: {"description": "List not found"},
        **_ROLE_RESPONSES,
    },
)
def delete_by_id(
    list_id: str,
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Delete a vocabulary.

    The option documents it referenced are left in place, matching legacy
    behaviour - they may be shared, and orphan cleanup is not part of this path.
    """
    return _respond(services.delete_by_id(list_id, current_user.username))
