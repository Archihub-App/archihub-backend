"""User routes.

Port of ``app/api/users/routes.py``.

SCOPE: the read and self-service routes, plus favourites - `POST /users`,
`GET /users/me`, `GET /users/requests`, and the three favourites routes. The
account-lifecycle routes (`register`, `register-me`, `forgot-password`,
`update`, `delete`, `update-me`, the compromise pair) and the four token-issuing
routes depend on the `email` domain and on a decision about API-key issuance
(see ``services`` and BACKEND_FINDINGS F16); they land in the next increment.

Role failures keep the legacy 401 pending the coordinated frontend flip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.users import services
from archihub.api.users.schemas import (
    FavoriteListRequest,
    FavoriteRequest,
    UserListRequest,
)
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

require_admin_or_editor = require_role_any(
    "admin", "editor", status_code=LEGACY_ROLE_FAILURE_STATUS
)

_ROLE_RESPONSES = {401: {"description": "Missing/invalid token, or insufficient role"}}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


@router.post(
    "",
    responses={200: {"description": "Paginated users"}, **_ROLE_RESPONSES},
)
def get_all(
    body: UserListRequest = Body(...),
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """List users, filtered and paginated.

    A POST because the filter travels in the body - the legacy shape, which the
    frontend sends.

    Filters are reduced to an allowlist of string-equality fields before they
    reach a query: a client-supplied filter document passed through unchecked
    lets the caller express arbitrary query operators.
    """
    return _respond(services.get_all(body.model_dump(exclude_unset=True), current_user.username))


@router.get(
    "/requests",
    responses={200: {"description": "The caller's weekly quota usage"}, **_ROLE_RESPONSES},
)
def get_requests(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    """How much of the caller's weekly public-API quota is used."""
    return _respond(services.get_requests(current_user.username))


@router.get(
    "/me",
    responses={
        200: {"description": "The caller's own profile"},
        400: {"description": "User does not exist"},
        **_ROLE_RESPONSES,
    },
)
def get_me(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    """The caller's own profile, without the password hash."""
    return _respond(services.get_profile(current_user.username))


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------


@router.post(
    "/favorites",
    responses={
        200: {"description": "Favourite added"},
        400: {"description": "Invalid type, or the resource is not published"},
        404: {"description": "Resource not found"},
        **_ROLE_RESPONSES,
    },
)
def set_favorite(
    body: FavoriteRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Add a resource, record or snap to the caller's favourites.

    ``type`` is a fixed enumeration because it selects the collection to read.
    """
    return _respond(services.set_favorite(current_user.username, body.model_dump()))


@router.delete(
    "/favorites",
    responses={200: {"description": "Favourite removed"}, **_ROLE_RESPONSES},
)
def delete_favorite(
    body: FavoriteRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Remove one of the caller's favourites."""
    return _respond(services.delete_favorite(current_user.username, body.model_dump()))


@router.post(
    "/favorites_list",
    responses={200: {"description": "The caller's favourites of one type"}, **_ROLE_RESPONSES},
)
def get_favorites(
    body: FavoriteListRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """List the caller's favourites of a given type."""
    return _respond(services.get_favorites(current_user.username, body.model_dump()))
