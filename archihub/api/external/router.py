"""`/adminApi` and `/publicApi` — the surfaces other organisations script against.

Authenticated with **Fernet API tokens**, not the browser's JWT. Their consumers
live outside this repository, so a change here is invisible to any audit of
`upgrade_front` and silently breaks somebody else's integration. Paths, methods
and response shapes are therefore kept stable.

**Availability follows the `api_activation` setting, per request.** The routes
always exist and answer a plain 404 when the instance has that API switched off,
which is indistinguishable from a missing route. Deciding at construction
instead would mean restarting every worker to switch an API on.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Depends, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from archihub.api.external import services
from archihub.api.records.storage import IncomingFile, UnsupportedFileType
from archihub.core.files import UnsupportedFile, UploadTooLarge
from archihub.core.i18n import gettext as _
from archihub.core.security.api_auth import (
    ApiIdentity,
    authenticate_admin_api,
    authenticate_public_api,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/adminApi", tags=["Admin API"])
public_router = APIRouter(prefix="/publicApi", tags=["Public API"])

ADMIN_SETTING = "api_activation_admin"
PUBLIC_SETTING = "api_activation_public"


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


def _enabled(entry_id: str) -> bool:
    from archihub.api.system.services import get_setting_value

    return bool(get_setting_value("api_activation", entry_id))


def _unavailable() -> JSONResponse:
    """What an external caller saw when the API was switched off: a plain 404.

    Deliberately indistinguishable from a route that does not exist, because to
    the caller it did not.
    """
    return JSONResponse(status_code=404, content={"msg": _("Not Found")})


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _refuse_if_switched_off(entry_id: str) -> None:
    """The activation gate, run BEFORE the token is looked at.

    A dependency *of* the identity dependencies, so a switched-off API answers
    404 before any token is checked and never confirms that the route exists. In
    a handler body it would run after FastAPI had already resolved the token.
    """
    if not _enabled(entry_id):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        # Raised the way an unrouted path raises it, so the response is
        # byte-identical to one. NOT a translated `NotFoundError`: that renders
        # "No encontrado" where a genuinely missing route renders "Not Found",
        # and that single difference tells a prober the route is really there
        # and was deliberately refused - which is the whole thing this is
        # supposed to hide.
        raise StarletteHTTPException(status_code=404)


def _require_admin_api() -> None:
    _refuse_if_switched_off(ADMIN_SETTING)


def _require_public_api() -> None:
    _refuse_if_switched_off(PUBLIC_SETTING)


def admin_identity(
    _gate: None = Depends(_require_admin_api),
    authorization: str | None = Header(default=None),
) -> ApiIdentity:
    """An admin API token, and it must belong to an administrator."""
    identity = authenticate_admin_api(authorization)
    if not identity.is_admin:
        from archihub.core.errors import PermissionDeniedError

        raise PermissionDeniedError(_("You don't have the required authorization"))
    return identity


def public_identity(
    _gate: None = Depends(_require_public_api),
    authorization: str | None = Header(default=None),
) -> ApiIdentity:
    return authenticate_public_api(authorization)


def _incoming(uploads: list[UploadFile] | None, body: dict) -> list[IncomingFile]:
    tags = body.get("filesIds") or []
    incoming = []
    for index, upload in enumerate(uploads or []):
        tag = tags[index] if index < len(tags) and isinstance(tags[index], dict) else {}
        incoming.append(
            IncomingFile.from_upload(
                upload, tag=tag.get("filetag") or "file", order=tag.get("order")
            )
        )
    return incoming


def _write(call) -> JSONResponse:
    try:
        return _respond(call())
    except UploadTooLarge as exc:
        return JSONResponse(status_code=413, content={"msg": str(exc)})
    except (UnsupportedFileType, UnsupportedFile, services.InvalidRequest, ValueError) as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})


def _parse_data(data: str) -> dict:
    try:
        parsed = json.loads(data)
    except (TypeError, ValueError):
        raise ValueError(_("The data field is not valid JSON")) from None
    if not isinstance(parsed, dict):
        raise ValueError(_("The data field must be an object"))
    return parsed


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


@admin_router.get("/get_system_info", responses={200: {"description": "Instance information"}})
def get_system_info(identity: ApiIdentity = Depends(admin_identity)) -> JSONResponse:
    """Content types, active capabilities and a couple of counts."""
    if not _enabled(ADMIN_SETTING):
        return _unavailable()
    return _respond(services.system_info(identity.username))


@admin_router.post("/create", responses={201: {"description": "Resource created"}})
def create_resource(
    data: str = Form(..., description="JSON document describing the resource"),
    files: list[UploadFile] = File(default_factory=list),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Create a resource, filling in the fields an integration may omit."""
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    from archihub.api.resources import write

    try:
        body = services.with_defaults(_parse_data(data))
    except (ValueError, services.InvalidRequest) as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    return _write(lambda: write.create(body, identity.username, _incoming(files, body)))


@admin_router.post("/update", responses={200: {"description": "Resource updated"}})
def update_resource(
    data: str = Form(..., description="JSON document with the fields to change"),
    id: str = Form(..., description="Id of the resource to update"),
    files: list[UploadFile] = File(default_factory=list),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Update a resource.

    The id is its own form field.
    """
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    from archihub.api.resources import write

    try:
        body = services.with_defaults(_parse_data(data), update=True)
    except (ValueError, services.InvalidRequest) as exc:
        return JSONResponse(status_code=400, content={"msg": str(exc)})

    return _write(lambda: write.update(id, body, identity.username, _incoming(files, body)))


@admin_router.post("/get_id", responses={200: {"description": "The matching resource"}})
def get_resource_id(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Find a published resource by an identifier you already hold.

    Lookups are by `ident`, `post_type` or a `metadata.firstLevel.*` field; the
    body is never used as a Mongo filter.
    """
    if not _enabled(ADMIN_SETTING):
        return _unavailable()
    return _respond(services.find_resource(body))


@admin_router.post("/get_opts_id", responses={200: {"description": "The matching option"}})
def get_option_id(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Find a controlled-vocabulary option by its display term."""
    if not _enabled(ADMIN_SETTING):
        return _unavailable()
    return _respond(services.find_option(body))


@admin_router.post("/create_type", responses={201: {"description": "Content type created"}})
def create_type(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Create a content type."""
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    from archihub.api.types import services as type_services

    return _respond(type_services.create(body, identity.username))


@admin_router.post("/update_type", responses={200: {"description": "Content type updated"}})
def update_type(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Update a content type.

    A missing `slug` is a 400.
    """
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    slug = body.get("slug")
    if not isinstance(slug, str) or not slug:
        return JSONResponse(status_code=400, content={"msg": _("slug is missing")})

    from archihub.api.types import services as type_services

    return _respond(type_services.update_by_slug(slug, body, identity.username))


@admin_router.get("/get_type/{slug}", responses={200: {"description": "The content type"}})
def get_type(slug: str, identity: ApiIdentity = Depends(admin_identity)) -> JSONResponse:
    """One content type by slug."""
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    from archihub.api.types import services as type_services

    result = type_services.get_by_slug(slug)
    return _respond(result if isinstance(result, tuple) else (result, 200))


@admin_router.get(
    "/lists/{list_id}",
    responses={200: {"description": "The list"}, 404: {"description": "No such list"}},
)
def get_list(list_id: str, identity: ApiIdentity = Depends(admin_identity)) -> JSONResponse:
    """One controlled vocabulary by id.

    Part of the external contract other organisations' scripts read, so the
    path, the method and the response shape are fixed.
    """
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    from archihub.api.lists import services as list_services

    return _respond(list_services.get_by_id(list_id))


@admin_router.api_route(
    "/plugins/{plugin}/{plugin_endpoint:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    responses={501: {"description": "Forwarding to a plugin with an API token is not implemented"}},
)
def plugin_proxy(
    plugin: str,
    plugin_endpoint: str,
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Reach a plugin's endpoint with an admin API token instead of a JWT.

    THE TARGET IS RESOLVED, NEVER ASSEMBLED. The named plugin is looked up in the
    **mounted registry** — so it must be active — and the endpoint must match one
    of that plugin's own declared route paths *exactly*. A traversal string does
    not match any of them: there is nothing to filter when the only reachable
    values come from a list the application built.

    It answers 501, deliberately: resolution is implemented and
    testable, but re-dispatching a request into the ASGI stack with a *different*
    identity is a second, separate decision (the inner route's own
    ``Depends(get_current_user)`` would reject a Fernet identity, so honouring
    this means teaching those routes about a second principal). Nothing in
    ``upgrade_front`` calls it; an outside integration that does gets an explicit
    "not implemented" rather than a proxy with an authorisation model nobody has
    reviewed.
    """
    if not _enabled(ADMIN_SETTING):
        return _unavailable()

    target = resolve_plugin_route(plugin, plugin_endpoint)
    if target is None:
        logger.info(
            "Plugin proxy: no route %s/%s (asked for by %s)",
            plugin, plugin_endpoint, identity.username,
        )
        # The same body as any path that does not exist - which, to an outside
        # integration asking for an endpoint of a plugin this instance does not
        # run, it does not.
        return _unavailable()

    logger.info("Plugin proxy resolved %s but dispatch is not implemented", target)
    return JSONResponse(
        status_code=501,
        content={"msg": _("Plugin endpoints are not available in this build yet")},
    )


def resolve_plugin_route(plugin: str, plugin_endpoint: str) -> str | None:
    """The full path of a mounted plugin's endpoint, or ``None``.

    Returns a value drawn from the application's own route table, never one
    built from the request. Separated from the handler so it can be tested
    against traversal attempts without an app or a token.
    """
    from archihub.plugins.framework.mounting import get_plugin

    mounted = get_plugin(plugin)
    if mounted is None:
        return None

    wanted = f"/{plugin}/{plugin_endpoint.strip('/')}"
    for route in mounted.router.routes:
        if getattr(route, "path", None) == wanted:
            return route.path
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@public_router.post("", responses={200: {"description": "Matching published resources"}})
def list_resources(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(public_identity),
) -> JSONResponse:
    """Search or list published resources.

    A keyword routes to the search index; without one it is an ordinary
    listing. Both are the **public** paths, so neither can be asked for anything
    but published material —
    """
    if not _enabled(PUBLIC_SETTING):
        return _unavailable()

    keyword = body.get("keyword")
    if isinstance(keyword, str) and keyword.strip():
        from archihub.api.search import services as search_services

        return _respond(search_services.search(body, None, public=True))

    from archihub.api.resources import public as resources_public

    return _respond(resources_public.get_all(body))


@public_router.get("/types", responses={200: {"description": "Every content type"}})
def list_types(identity: ApiIdentity = Depends(public_identity)) -> JSONResponse:
    """The instance's content types."""
    if not _enabled(PUBLIC_SETTING):
        return _unavailable()

    from archihub.api.types import services as type_services

    result = type_services.get_all()
    return _respond(result if isinstance(result, tuple) else (result, 200))


@public_router.get("/resources/{resource_id}", responses={200: {"description": "The resource"}})
def get_resource(
    resource_id: str, identity: ApiIdentity = Depends(public_identity)
) -> JSONResponse:
    """One published resource, through the public visibility rule."""
    if not _enabled(PUBLIC_SETTING):
        return _unavailable()

    from archihub.api.resources import public as resources_public

    return _respond(resources_public.get_by_id(resource_id))
