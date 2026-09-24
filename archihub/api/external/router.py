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
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

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


def _not_found() -> StarletteHTTPException:
    """The refusal for a switched-off API and for an endpoint that is not there.

    Raised exactly the way an unrouted path is, so the response is
    byte-identical to one: to the caller, the route does not exist. A translated
    message here ("No encontrado" where an unrouted path says "Not Found") would
    tell a prober the route is real and was deliberately refused.
    """
    return StarletteHTTPException(status_code=404)


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
        raise _not_found()


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
        raise _not_found()
    return _respond(services.system_info(identity.username))


@admin_router.post("/create", responses={201: {"description": "Resource created"}})
def create_resource(
    data: str = Form(..., description="JSON document describing the resource"),
    files: list[UploadFile] = File(default_factory=list),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Create a resource, filling in the fields an integration may omit."""
    if not _enabled(ADMIN_SETTING):
        raise _not_found()

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
        raise _not_found()

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
        raise _not_found()
    return _respond(services.find_resource(body))


@admin_router.post("/get_opts_id", responses={200: {"description": "The matching option"}})
def get_option_id(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Find a controlled-vocabulary option by its display term."""
    if not _enabled(ADMIN_SETTING):
        raise _not_found()
    return _respond(services.find_option(body))


@admin_router.post("/create_type", responses={201: {"description": "Content type created"}})
def create_type(
    body: dict = Body(default_factory=dict),
    identity: ApiIdentity = Depends(admin_identity),
) -> JSONResponse:
    """Create a content type."""
    if not _enabled(ADMIN_SETTING):
        raise _not_found()

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
        raise _not_found()

    slug = body.get("slug")
    if not isinstance(slug, str) or not slug:
        return JSONResponse(status_code=400, content={"msg": _("slug is missing")})

    from archihub.api.types import services as type_services

    return _respond(type_services.update_by_slug(slug, body, identity.username))


@admin_router.get("/get_type/{slug}", responses={200: {"description": "The content type"}})
def get_type(slug: str, identity: ApiIdentity = Depends(admin_identity)) -> JSONResponse:
    """One content type by slug."""
    if not _enabled(ADMIN_SETTING):
        raise _not_found()

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
        raise _not_found()

    from archihub.api.lists import services as list_services

    return _respond(list_services.get_by_id(list_id))


# How long the session minted for one forwarded call lives. It is checked when
# the plugin route authenticates, at the start of the call, so it only has to
# outlast the hop - an upload that streams for longer is not cut off by it.
PLUGIN_CALL_SESSION = timedelta(minutes=2)

# Headers of the outside request that must not reach the plugin route: the API
# token is replaced by the session below, and a browser cookie has no business
# on a call made with an API token.
_NOT_FORWARDED = {b"authorization", b"cookie"}

_ROUTING_KEYS = {"route", "endpoint", "path_params", "router"}


@admin_router.api_route(
    "/plugins/{plugin}/{plugin_endpoint:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    responses={
        200: {"description": "Whatever the plugin endpoint answers, passed through unchanged"},
        404: {"description": "No active plugin with that name, or no such endpoint in it"},
    },
)
def plugin_proxy(
    request: Request,
    plugin: str,
    plugin_endpoint: str,
    identity: ApiIdentity = Depends(admin_identity),
) -> Response:
    """Call an active plugin's endpoint with an admin API token.

    The request - method, query string, headers and body, streamed - is handed
    to the plugin route as if the token's owner had made it from a browser, and
    the plugin's answer comes back unchanged, streamed too.

    **The plugin route still decides.** Its own authentication and role checks
    run against a short-lived session minted here for the token's owner, never
    returned to the caller; the token only establishes who that owner is. So a
    call through here can do exactly what that administrator can do in the
    interface, and nothing a plugin route would refuse them.

    **The target is resolved, never assembled.** The plugin must be mounted
    (active), and the path must match one of that plugin's own declared routes;
    anything else is the same 404 as a path that does not exist.
    """
    if not _enabled(ADMIN_SETTING):
        raise _not_found()

    target = resolve_plugin_route(plugin, plugin_endpoint)
    if target is None:
        logger.info(
            "Plugin proxy: no route %s/%s (asked for by %s)",
            plugin, plugin_endpoint, identity.username,
        )
        raise _not_found()

    logger.info("Plugin proxy: %s %s as %s", request.method, target, identity.username)
    return _PluginCall(target, identity.username)


class _PluginCall(Response):
    """Runs the resolved plugin route in place of a response.

    The outer handler never reads the request body, so the ASGI ``receive``
    channel is still untouched and is passed straight on: uploads stream into
    the plugin route, and its response streams out through ``send``. The call
    goes through the whole application, so the error handlers and request
    logging apply to it as to any other request.
    """

    def __init__(self, path: str, username: str) -> None:
        super().__init__()
        self.target_path = path
        self.username = username

    async def __call__(self, scope, receive, send) -> None:
        from archihub.core.security import tokens

        session = tokens.create_access_token(self.username, expires_delta=PLUGIN_CALL_SESSION)
        headers = [
            (name, value) for name, value in scope["headers"] if name.lower() not in _NOT_FORWARDED
        ]
        headers.append((b"authorization", f"Bearer {session}".encode()))

        # What routing and FastAPI attached to the outer request stays behind;
        # the inner one is routed afresh and gets its own.
        inner = {
            key: value
            for key, value in scope.items()
            if key not in _ROUTING_KEYS and not key.startswith("fastapi_")
        }
        inner.update(path=self.target_path, raw_path=self.target_path.encode(), headers=headers)
        await scope["app"](inner, receive, send)


def _unsafe_segment(segment: str) -> bool:
    return segment in ("", ".", "..") or "%" in segment or "\\" in segment


def resolve_plugin_route(plugin: str, plugin_endpoint: str) -> str | None:
    """The path to call on a mounted plugin, or ``None``.

    The path must match one of the plugin's declared routes - a route with a
    parameter (``/download/{task_id}``) matches one concrete value per segment,
    never a ``/``. A segment that is empty, ``.`` or ``..``, or that still holds
    a ``%`` or ``\\`` after decoding, is refused before the match, so no request
    path can name anything but that plugin's own routes. Separated from the
    handler so it can be tested without an app or a token.
    """
    from archihub.plugins.framework.mounting import get_plugin

    mounted = get_plugin(plugin)
    if mounted is None:
        return None

    segments = plugin_endpoint.strip("/").split("/")
    if any(_unsafe_segment(segment) for segment in segments):
        return None

    wanted = "/" + "/".join([plugin, *segments])
    for route in mounted.router.routes:
        regex = getattr(route, "path_regex", None)
        if regex is not None and regex.match(wanted):
            return wanted
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
        raise _not_found()

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
        raise _not_found()

    from archihub.api.types import services as type_services

    result = type_services.get_all()
    return _respond(result if isinstance(result, tuple) else (result, 200))


@public_router.get("/resources/{resource_id}", responses={200: {"description": "The resource"}})
def get_resource(
    resource_id: str, identity: ApiIdentity = Depends(public_identity)
) -> JSONResponse:
    """One published resource, through the public visibility rule."""
    if not _enabled(PUBLIC_SETTING):
        raise _not_found()

    from archihub.api.resources import public as resources_public

    return _respond(resources_public.get_by_id(resource_id))
