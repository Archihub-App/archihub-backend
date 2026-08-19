"""Metadata-form routes.

Every route requires the **admin** role - not admin-or-editor, as types and
lists do.

TWO SHAPES THAT LOOK WRONG AND ARE NOT, both confirmed against
``upgrade_front/src/services/FormService.tsx``:

* **Retrieving a form is a POST**, not a GET (``POST /forms/{slug}``).
  ``FormService.getForm`` sends ``method: "POST"`` and expects 200. Changing it
  to GET would break the client.
* **Delete returns 204**, where the types domain returns 200.
  ``FormService.deleteForm`` requires exactly 204 and reads no body - which is
  the correct pairing, since HTTP forbids content on a 204. (The types domain
  returning 200 while ``TypesService.deleteType`` expects 204 is the outlier,
  and is a known pre-existing mismatch.)

Route order matters: ``/forms/fields`` is declared before ``/forms/{slug}`` so
the literal path wins. They differ by method today, but relying on that would
make a later ``GET /forms/{slug}`` silently shadow it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import JSONResponse

from archihub.api.forms import services
from archihub.api.forms.schemas import FormCreate, FormUpdate
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forms", tags=["Forms"])

require_admin = require_role_any("admin")

_ROLE_RESPONSES = {401: {"description": "Missing or invalid token"},
        403: {"description": "The admin role is required"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


@router.get(
    "/fields",
    responses={200: {"description": "Available field types, labels translated"}, **_ROLE_RESPONSES},
)
def get_all_fields(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """Get every field type a form can use, including plugin-contributed ones."""
    return _respond(services.get_all_fields_types())


@router.get(
    "",
    responses={200: {"description": "All forms, as name + description + slug"}, **_ROLE_RESPONSES},
)
def get_all(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """Get every metadata form."""
    return _respond(services.get_all())


@router.post(
    "",
    responses={
        201: {"description": "Form created"},
        500: {"description": "Validation failed, or the form conflicts with the combined schema"},
        **_ROLE_RESPONSES,
    },
)
def create(
    body: FormCreate = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Create a metadata form.

    The slug is derived from the name when not supplied, and suffixed until
    unique. The form must define a ``metadata.firstLevel.title`` text field, and
    must not declare a destiny with a type that conflicts with another form's.
    """
    return _respond(services.create(body.model_dump(exclude_unset=True), current_user.username))


@router.post(
    "/{slug}",
    responses={
        200: {"description": "The form, with the synthetic accessRights field prepended"},
        404: {"description": "Form not found"},
        **_ROLE_RESPONSES,
    },
)
def get_by_slug(
    slug: str,
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Get one form by slug.

    This is a POST because that is what the client sends - see the module
    docstring.
    """
    return _respond(services.get_by_slug(slug))


@router.put(
    "/{slug}",
    responses={
        200: {"description": "Form updated"},
        404: {"description": "Form not found"},
        **_ROLE_RESPONSES,
    },
)
def update_by_slug(
    slug: str,
    body: FormUpdate = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Update a form definition."""
    return _respond(
        services.update_by_slug(slug, body.model_dump(exclude_unset=True), current_user.username)
    )


@router.delete(
    "/{slug}",
    status_code=204,
    responses={
        204: {"description": "Form deleted"},
        400: {"description": "The form is still used by a content type"},
        404: {"description": "Form not found"},
        **_ROLE_RESPONSES,
    },
)
def delete_by_slug(
    slug: str,
    current_user: CurrentUser = Depends(require_admin),
):
    """Delete a form, unless a content type still references it.

    Returns a bodyless 204 on success. The legacy handler returned 204 *with* a
    JSON body, which HTTP forbids; the client reads no body here, so sending
    none is both correct and compatible.
    """
    payload, status_code = services.delete_by_slug(slug, current_user.username)
    if status_code == 204:
        return Response(status_code=204)
    return JSONResponse(status_code=status_code, content=payload)


@router.post(
    "/duplicate/{slug}",
    responses={
        201: {"description": "Form duplicated"},
        404: {"description": "Form not found"},
        **_ROLE_RESPONSES,
    },
)
def duplicate_by_slug(
    slug: str,
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Duplicate a form under a new, unique slug."""
    return _respond(services.duplicate_by_slug(slug, current_user.username))
