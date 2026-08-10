"""Content-type routes.

Port of ``app/api/types/routes.py``. The Flasgger YAML docstrings become FastAPI
metadata: prose moves to the handler docstring, ``tags`` to the router, and the
``responses`` map to the decorator. Security is derived from the dependency
rather than hand-declared, so a route can no longer enforce auth while forgetting
to document it.

WIRE CONTRACT IS PRESERVED EXACTLY, including two things that are arguably wrong:

* Role failures return **401**, not 403. 403 is correct and is what new routes
  use, but `upgrade_front` compares status codes exactly in ~187 places, so the
  flip is a coordinated frontend change (PLAN_FASTAPI.md decision 2). Every such
  route passes ``LEGACY_ROLE_FAILURE_STATUS`` explicitly - grep for it to find
  what is awaiting the flip.
* ``PUT`` and ``DELETE`` are guarded by ``admin`` OR ``editor``, while ``POST``
  requires ``admin``. So an editor can modify or delete a content type but not
  create one, which is a strange privilege shape. Reproduced as-is; changing it
  is a permissions decision, not a migration one.

Responses are returned as explicit ``JSONResponse`` objects with no
``response_model``: a response model would *filter* undeclared fields, silently
dropping data while still returning 200. Models are introduced per route once the
diff harness confirms parity (PLAN_FASTAPI.md section 7).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.types import services
from archihub.api.types.schemas import PostTypeCreate, PostTypeUpdate
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/types", tags=["Content types"])

MSG_UNAUTHORIZED = "You don't have the required authorization"

# Ported routes keep the legacy 401-for-permission-denied contract.
require_admin = require_role_any("admin", status_code=LEGACY_ROLE_FAILURE_STATUS)
require_admin_or_editor = require_role_any(
    "admin", "editor", status_code=LEGACY_ROLE_FAILURE_STATUS
)


def _respond(result) -> JSONResponse:
    """Render a service result.

    Services return either ``(payload, status)`` or, for ``get_by_slug``, the
    document itself on success. Both shapes are handled here so the services
    keep their legacy signatures during the port.
    """
    if isinstance(result, tuple) and len(result) == 2:
        payload, status_code = result
        return json_response(payload, status_code)
    return json_response(result, 200)


@router.get(
    "",
    responses={
        200: {"description": "List of content types (name, description, slug only)"},
        401: {"description": "JWT token missing or invalid"},
        500: {"description": "Error retrieving the content types"},
    },
)
def get_all(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    """Get all cataloging content types.

    No role restriction beyond a valid session. Returns only name, description
    and slug for each type.
    """
    return _respond(services.get_all())


@router.post(
    "",
    responses={
        201: {"description": "Content type created"},
        400: {"description": "Name and slug are required, or the slug already exists"},
        401: {"description": "Missing/invalid token, or the admin role is required"},
        500: {"description": "Error creating the content type"},
    },
)
def create(
    body: PostTypeCreate = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Create a content type.

    ``slug`` may be sent empty, in which case it is derived from ``name`` and
    suffixed until unique. A non-empty slug that already exists is rejected with
    400 rather than silently suffixed - the caller asked for a specific slug.
    """
    payload = body.model_dump(exclude_unset=True)

    if not payload.get("slug"):
        # Derived slugs are made unique automatically.
        payload["slug"] = services.make_unique_slug(payload.get("name", ""))
    elif services.slug_exists(payload["slug"]):
        return JSONResponse(status_code=400, content={"msg": _("Slug already exists")})

    return _respond(services.create(payload, current_user.username))


@router.get(
    "/{slug}",
    responses={
        200: {"description": "The content type, with its parent chain and metadata form"},
        401: {"description": "Missing/invalid token, or insufficient role"},
        404: {"description": "Content type not found"},
        500: {"description": "Error retrieving the content type"},
    },
)
def get_by_slug(
    slug: str,
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Get one content type by slug.

    Beyond the admin/editor requirement, a type carrying ``viewRoles`` is only
    visible to holders of one of those roles (admins always pass).
    """
    from archihub.api.users.services import has_role

    post_type = services._mongo().get_record("post_types", {"slug": slug}, {"viewRoles": 1})
    view_roles = (post_type or {}).get("viewRoles") or []
    if view_roles:
        allowed = has_role(current_user.username, "admin") or any(
            has_role(current_user.username, role) for role in view_roles
        )
        if not allowed:
            return JSONResponse(
                status_code=LEGACY_ROLE_FAILURE_STATUS, content={"msg": _(MSG_UNAUTHORIZED)}
            )

    result = services.get_by_slug(slug)

    # The legacy handler only returned the 404 when the message matched
    # 'Type not found' exactly - and the service actually produces
    # 'Post type not found'. Every other error fell through the if/else and
    # returned None, which Flask rejects with a 500 carrying no message.
    # Here every error shape is returned as the service reported it.
    return _respond(result)


@router.put(
    "/{slug}",
    responses={
        200: {"description": "Content type updated"},
        401: {"description": "Missing/invalid token, or insufficient role"},
        404: {"description": "Content type not found"},
        500: {"description": "Error updating the content type"},
    },
)
def update_by_slug(
    slug: str,
    body: PostTypeUpdate = Body(...),
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Update a content type."""
    return _respond(
        services.update_by_slug(slug, body.model_dump(exclude_unset=True), current_user.username)
    )


@router.delete(
    "/{slug}",
    responses={
        200: {"description": "Content type deleted"},
        401: {"description": "Missing/invalid token, or insufficient role"},
        404: {"description": "Content type not found"},
    },
)
def delete_by_slug(
    slug: str,
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Delete a content type and soft-delete every resource that used it.

    Returns **200**, not 204. `TypesService.deleteType` in the frontend expects
    204 and therefore takes its error path on success today - a known, already
    documented mismatch. Preserved here so the behaviour does not change under
    the frontend's feet; fixing it is a paired backend+frontend change.
    """
    return _respond(services.delete_by_slug(slug, current_user.username))
