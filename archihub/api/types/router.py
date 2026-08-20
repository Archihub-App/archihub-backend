"""Content-type routes.

Port of ``app/api/types/routes.py``. The Flasgger YAML docstrings become FastAPI
metadata: prose moves to the handler docstring, ``tags`` to the router, and the
``responses`` map to the decorator. Security is derived from the dependency
rather than hand-declared, so a route can no longer enforce auth while forgetting
to document it.

WIRE CONTRACT IS PRESERVED EXACTLY, including one thing that is arguably wrong:

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
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/types", tags=["Content types"])

MSG_UNAUTHORIZED = "You don't have the required authorization"

# `ROLE_FAILURE_STATUS` is 403 since the coordinated frontend flip; it is
# still passed explicitly because it marks the routes whose status was chosen for
# legacy-compatibility reasons. See its comment in core/security/jwt.py.
require_admin = require_role_any("admin")
require_admin_or_editor = require_role_any(
    "admin", "editor"
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
    status_code=201,
    responses={
        201: {"description": "Content type created"},
        400: {"description": "Name and slug are required, or the slug already exists"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "The admin role is required"},
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


@router.post(
    "/moreinfo",
    responses={
        200: {"description": "Aggregated counts, or {'msg': 'ok'} for an unknown chart"},
        400: {"description": "slug or type missing"},
        403: {"description": "Insufficient role"},
    },
)
def get_type_viz(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Counts for the charts on a content type's info panel.

    Declared before `/{slug}` because Starlette matches in registration order and
    a literal path must win over a parameterised one - though only `GET /{slug}`
    exists today, so nothing currently shadows this.

    `type` names one of a fixed table of aggregations rather than describing one;
    an unrecognised name is answered `{"msg": "ok"}` with 200, as the legacy route
    did, because the panel requests several charts by name and one it does not
    know must not fail the screen.
    """
    slug = body.get("slug")
    viz_type = body.get("type")
    if not isinstance(slug, str) or not slug or not isinstance(viz_type, str) or not viz_type:
        return JSONResponse(
            status_code=400, content={"msg": _("You must specify the slug and the type")}
        )

    return _respond(services.get_type_viz(slug, viz_type))


@router.get(
    "/{slug}",
    responses={
        200: {"description": "The content type, with its parent chain and metadata form"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
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
                status_code=ROLE_FAILURE_STATUS, content={"msg": _(MSG_UNAUTHORIZED)}
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
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
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
        401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"},
        404: {"description": "Content type not found"},
    },
)
def delete_by_slug(
    slug: str,
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """Delete a content type and soft-delete every resource that used it.

    Returns **200**, not 204, matching the legacy route. `TypesService.deleteType`
    used to demand exactly 204 and so took its error path on success; it checks
    `response.ok` now, so both are accepted and this status is free to stay as
    the legacy contract had it.
    """
    return _respond(services.delete_by_slug(slug, current_user.username))
