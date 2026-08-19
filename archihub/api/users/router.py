"""User routes.

Port of ``app/api/users/routes.py``.

A role failure answers 403; 401 is reserved for "I do not know who you are".

ROUTE ORDER MATTERS HERE. Every literal path (`/me`, `/requests`, `/register`,
`/favorites`, ...) is declared BEFORE `/{user_id}`, or the parameterised route
would capture them - a GET of `/users/me` would look up a user whose id is the
string "me".

API-KEY ROUTES: the value returned is the only copy. The server stores a hash,
so nothing can reproduce it afterwards. See core/security/api_keys.py and
API_KEYS_FRONTEND_NOTES.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.users import services
from archihub.api.users.schemas import (
    AdminApiKeyRequest,
    ApiKeyRequest,
    DeleteUserRequest,
    FavoriteListRequest,
    FavoriteRequest,
    ForgotPasswordRequest,
    RegisterMeRequest,
    RegisterRequest,
    SelfUpdateRequest,
    UpdateUserRequest,
    UserListRequest,
)
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

require_admin_or_editor = require_role_any(
    "admin", "editor"
)
require_admin = require_role_any("admin")
require_visualizer = require_role_any("visualizer")

_ROLE_RESPONSES = {401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


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


@router.post(
    "/snaps",
    responses={
        200: {"description": "One page of the caller's snaps of that type"},
        400: {"description": "Unsupported snap type"},
        **_ROLE_RESPONSES,
    },
)
def get_snaps(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """List the caller's own snaps of a given kind, newest first.

    Lives under ``/users`` rather than ``/snaps`` because it is scoped to the
    caller, which is where the legacy blueprint put it too. The implementation
    is in the ``snaps`` domain, since that is what it reads.
    """
    from archihub.api.snaps import services as snap_services

    return _respond(snap_services.list_for_user(current_user.username, body))


# ---------------------------------------------------------------------------
# Account lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    responses={201: {"description": "Account created"}, 400: {"description": "Invalid input"}, **_ROLE_RESPONSES},
)
def register(
    body: RegisterRequest = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Create an account (administrative)."""
    return _respond(services.register_user(body.model_dump(exclude_unset=True)))


@router.post(
    "/register-me",
    responses={
        201: {"description": "Account created, pending verification"},
        400: {"description": "Registration is disabled, or the account exists"},
    },
)
def register_me(body: RegisterMeRequest = Body(...)) -> JSONResponse:
    """Self-service registration, when the instance allows it.

    Unauthenticated by necessity. Roles are fixed server-side, so a caller
    cannot grant themselves anything by what they send.
    """
    return _respond(services.register_me(body.model_dump(exclude_unset=True)))


@router.post(
    "/forgot-password",
    responses={
        200: {"description": "Processed - the response is the same whether or not the account exists"},
        400: {"description": "Password recovery is disabled on this instance"},
    },
)
def forgot_password(body: ForgotPasswordRequest = Body(...)) -> JSONResponse:
    """Begin password recovery.

    Answers identically whether or not the account exists, and whether or not
    the mail is actually delivered - otherwise the endpoint reports which
    usernames are registered.
    """
    return _respond(services.forgot_password(body.model_dump(exclude_unset=True)))


@router.put(
    "/update",
    responses={200: {"description": "Account updated"}, 404: {"description": "User not found"}, **_ROLE_RESPONSES},
)
def update_user(
    body: UpdateUserRequest = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Update another account (administrative)."""
    payload = body.model_dump(exclude_unset=True, by_alias=True)
    payload["_id"] = body.id
    return _respond(services.update_user(payload, current_user.username))


@router.delete(
    "/delete",
    responses={
        200: {"description": "Account deleted"},
        400: {"description": "You cannot delete yourself"},
        404: {"description": "User does not exist"},
        **_ROLE_RESPONSES,
    },
)
def delete_user(
    body: DeleteUserRequest = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Delete an account and revoke its API keys."""
    return _respond(services.delete_user(body.model_dump(), current_user.username))


@router.put(
    "/update-me",
    responses={
        200: {"description": "Profile updated"},
        400: {"description": "Incorrect password, or nothing to change"},
        **_ROLE_RESPONSES,
    },
)
def update_me(
    body: SelfUpdateRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Update the caller's own profile.

    Requires the current password, and revokes the caller's API keys when the
    password changes.
    """
    return _respond(services.update_me(body.model_dump(exclude_unset=True), current_user.username))


@router.get("/compromise", responses={200: {"description": "The caller's profile"}, **_ROLE_RESPONSES})
def get_compromise(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    """Whether the caller has accepted the usage compromise."""
    return _respond(services.get_profile(current_user.username))


@router.get("/acceptcompromise", responses={200: {"description": "Compromise accepted"}, **_ROLE_RESPONSES})
def accept_compromise(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    """Record that the caller accepted the usage compromise."""
    return _respond(services.accept_compromise(current_user.username))


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def _issue(scope: str, username: str, body, expires_in=None) -> JSONResponse:
    return _respond(
        services.issue_api_key(
            username, body.password, scope, name=getattr(body, "name", None), expires_in=expires_in
        )
    )


@router.post(
    "/token",
    responses={
        200: {"description": "Key issued - THIS IS THE ONLY TIME IT IS RETURNED"},
        400: {"description": "Incorrect password"},
        **_ROLE_RESPONSES,
    },
)
def generate_token(
    body: ApiKeyRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Issue a public-API key for the caller.

    The response carries the only copy; the server keeps a hash.
    """
    from archihub.core.security import api_keys

    return _issue(api_keys.SCOPE_PUBLIC, current_user.username, body)


@router.post(
    "/admin-token",
    responses={200: {"description": "Key issued - only returned once"}, **_ROLE_RESPONSES},
)
def generate_admin_token(
    body: AdminApiKeyRequest = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Issue an administrative API key.

    ``duration`` is in days; ``false`` means no expiry, matching the legacy
    contract.
    """
    from datetime import timedelta

    from archihub.core.security import api_keys

    expires_in = timedelta(days=body.duration) if isinstance(body.duration, int) and body.duration else None
    return _issue(api_keys.SCOPE_ADMIN, current_user.username, body, expires_in=expires_in)


@router.post(
    "/node-token",
    responses={200: {"description": "Key issued - only returned once"}, **_ROLE_RESPONSES},
)
def generate_node_token(
    body: ApiKeyRequest = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Issue a node-to-node API key."""
    from archihub.core.security import api_keys

    return _issue(api_keys.SCOPE_NODE, current_user.username, body)


@router.post(
    "/viz-token",
    responses={200: {"description": "Key issued - only returned once"}, **_ROLE_RESPONSES},
)
def generate_viz_token(
    body: ApiKeyRequest = Body(...),
    current_user: CurrentUser = Depends(require_visualizer),
) -> JSONResponse:
    """Issue a visualisation API key."""
    from archihub.core.security import api_keys

    return _issue(api_keys.SCOPE_VIZ, current_user.username, body)


@router.get(
    "/api-keys",
    responses={200: {"description": "The caller's keys, described but not reproduced"}, **_ROLE_RESPONSES},
)
def list_api_keys(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    """List the caller's API keys.

    New route, with no legacy equivalent: the previous single-key-per-user model
    had nothing to list. Returns metadata only - the secrets do not exist here.
    """
    return _respond(services.list_api_keys(current_user.username))


@router.delete(
    "/api-keys/{key_id}",
    responses={200: {"description": "Key revoked"}, 404: {"description": "Key not found"}, **_ROLE_RESPONSES},
)
def revoke_api_key(
    key_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Revoke one of the caller's own API keys."""
    return _respond(services.revoke_api_key(current_user.username, key_id))


# ---------------------------------------------------------------------------
# By id - LAST, so the literal paths above are not captured by it
# ---------------------------------------------------------------------------


@router.get(
    "/{user_id}",
    responses={200: {"description": "The user"}, 404: {"description": "User not found"}, **_ROLE_RESPONSES},
)
def get_by_id(
    user_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Get one user by id."""
    return _respond(services.get_by_id(user_id))
