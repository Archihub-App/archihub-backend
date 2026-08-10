"""System settings routes.

Port of ``app/api/system/routes.py``.

TWO ROUTES ARE UNAUTHENTICATED, and both are deliberate:

* ``GET /system/get-settings`` - the frontend reads it before anyone can log in,
  to decide whether to show a login form or the onboarding wizard. It returns
  only what that decision needs.
* ``POST /system/set-first-time`` - creates the initial administrator, so there
  is nobody to authenticate as. Its guard is that the instance has no users, and
  that is re-checked inside the service rather than trusted from a prior call.

NOT PORTED HERE: the maintenance routes that rebuild search indexes or load
geometry (`regenerate-index`, `index-resources`, `index-geometries`,
`regenerate-index-geometries`, `geo-load`, `zip-files-delete`,
`inventory_files_delete`). They drive handlers owned by the `search` and
`geosystem` domains and land with those.

Role failures keep the legacy 401 pending the coordinated frontend flip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.system import services
from archihub.core.security.fernet import FernetIdentity, node_fernet_auth_dependency
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["System settings"])

require_admin = require_role_any("admin", status_code=LEGACY_ROLE_FAILURE_STATUS)
require_admin_or_editor = require_role_any(
    "admin", "editor", status_code=LEGACY_ROLE_FAILURE_STATUS
)
#: The plugins listing is what the processing screens read to build themselves,
#: so it admits `processing` as well as `admin` - narrowing it to admin alone
#: leaves an operator holding only `processing` with an empty page.
require_admin_or_processing = require_role_any(
    "admin", "processing", status_code=LEGACY_ROLE_FAILURE_STATUS
)

_ROLE_RESPONSES = {401: {"description": "Missing/invalid token, or the admin role is required"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


# ---------------------------------------------------------------------------
# Public bootstrap
# ---------------------------------------------------------------------------


@router.get(
    "/get-settings",
    responses={200: {"description": "Bootstrap settings for the login screen"}},
)
def get_system_settings() -> JSONResponse:
    """Public settings needed before authentication.

    Carries only what a login screen requires: whether onboarding is pending,
    the interface language, the version and which optional features are on.
    """
    return _respond(services.get_system_settings())


@router.post(
    "/set-first-time",
    responses={
        201: {"description": "Instance configured; administrator created"},
        400: {"description": "Already configured, or the submitted fields are invalid"},
    },
)
def set_first_time(body: dict = Body(...)) -> JSONResponse:
    """Create the initial administrator.

    Unauthenticated by necessity; refuses once any user exists.
    """
    return _respond(services.set_first_time(body))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("", responses={200: {"description": "All settings groups"}, **_ROLE_RESPONSES})
def get_all(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """Read every settings group."""
    return _respond(services.get_all_settings())


@router.put("", responses={200: {"description": "Settings updated"}, **_ROLE_RESPONSES})
def update(
    body: dict = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Update settings.

    Only entries that already exist in a group are written - a client cannot
    introduce new keys into a document the application reads elsewhere.
    """
    return _respond(services.update_settings(body, current_user.username))


@router.get(
    "/default-cataloging-type",
    responses={200: {"description": "The default content type for cataloguing"}, **_ROLE_RESPONSES},
)
def get_default_cataloging_type(
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The content type new resources default to.

    The key is ``value``, which reads oddly next to the setting's own name -
    but it is the wire contract: `SystemService.getDefaultCatType` navigates to
    ``"/cataloging/" + response.value``, so a rename sends the cataloguing
    screen to ``/cataloging/undefined`` with a 200 and no error anywhere.
    """
    value = services.get_setting_value("post_types_settings", "default_type", 0)
    return json_response({"value": value}, 200)


@router.get(
    "/access-rights",
    responses={200: {"description": "Configured access rights"}, **_ROLE_RESPONSES},
)
def get_access_rights(
    current_user: CurrentUser = Depends(require_admin_or_editor),
) -> JSONResponse:
    """The access-rights vocabulary, as the stored list document.

    Returns the whole list - ``name`` and ``description`` alongside
    ``options`` - because that is what the legacy route returned. The frontend
    reads only ``options``, but this is also the shape an operator's own
    tooling sees.
    """
    from archihub.core.roles import access_rights_document

    return json_response(access_rights_document(), 200)


@router.get("/roles", responses={200: {"description": "Configured roles"}, **_ROLE_RESPONSES})
def get_roles(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """The roles vocabulary."""
    from archihub.core.roles import get_roles as _get

    return json_response(_get(), 200)


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


@router.get("/plugins", responses={200: {"description": "Installed plugins"}, **_ROLE_RESPONSES})
def get_plugins(current_user: CurrentUser = Depends(require_admin_or_processing)) -> JSONResponse:
    """List plugins, with whether each is active and whether it is supported.

    Wrapped in ``{"plugins": [...]}``, not returned as a bare array: both
    callers - the processing screen and the admin plugins table - read
    ``response.plugins``, so a bare array is a ``TypeError`` on a 200.
    """
    return _respond(services.get_plugins())


@router.post(
    "/plugins",
    responses={
        200: {"description": "Plugin activation changed"},
        400: {"description": "Unknown plugin, or not supported by this backend"},
        **_ROLE_RESPONSES,
    },
)
def activate_plugin(
    body: dict = Body(...),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Activate or deactivate a plugin.

    Activating one this backend cannot mount is refused rather than stored -
    the change would only take effect at the next restart, and the instance
    would then fail to start.
    """
    slug = body.get("slug") or body.get("plugin")
    if not slug:
        return JSONResponse(status_code=400, content={"msg": "Missing plugin slug"})

    return _respond(services.set_plugin_active(slug, bool(body.get("active")), current_user.username))


# ---------------------------------------------------------------------------
# Cache and runtime
# ---------------------------------------------------------------------------


@router.get("/clear-cache", responses={200: {"description": "Cache cleared"}, **_ROLE_RESPONSES})
def clear_cache(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """Flush the shared cache."""
    return _respond(services.clear_cache())


@router.get(
    "/node-clear-cache",
    responses={200: {"description": "Cache cleared"}, 401: {"description": "Invalid node key"}},
)
def node_clear_cache(identity: FernetIdentity = Depends(node_fernet_auth_dependency)) -> JSONResponse:
    """Flush the cache on this node.

    Authenticated with a node API key rather than a session, because the caller
    is another ArchiHUB node propagating an invalidation - not a person.
    """
    return _respond(services.clear_cache())
