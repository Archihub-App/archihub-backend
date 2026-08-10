"""Search routes.

Three: one authenticated, two public. The public pair mount first, and both go
through the same service with ``public=True`` — which is what fixes S28. There
is no code path by which a request can widen what an anonymous caller sees.

Unlike the legacy blueprint these are **always registered**. Whether search is
available is a per-request question answered by `services.indexing_enabled`,
answering 503 when it is off. Registering conditionally at construction meant an
operator turning indexing on had to restart every worker, and that the OpenAPI
document differed between instances of the same build.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from archihub.api.search import rss, services
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import CurrentUser, get_current_user
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])
public_router = APIRouter(prefix="/search", tags=["Search (public)"])

_RESPONSES = {
    400: {"description": "The request does not describe a runnable search"},
    503: {"description": "Search is not enabled on this instance"},
}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


@public_router.post(
    "/public",
    responses={200: {"description": "Matching published resources"}, **_RESPONSES},
)
def search_public(body: dict = Body(default_factory=dict)) -> JSONResponse:
    """Search the published catalogue.

    **The publication state is fixed here and cannot be requested.** The legacy
    route read it from the body, and since every resource is indexed whatever
    its state with `accessRights` defaulting to `public`, asking for
    `status: "draft"` returned unpublished material to anyone — demonstrated
    against a real index. See BACKEND_FINDINGS S28.
    """
    return _respond(services.search(body, None, public=True))


@public_router.get(
    "/public/rss",
    responses={200: {"description": "An RSS 2.0 feed of published articles"}, **_RESPONSES},
)
def rss_feed(
    request: Request,
    post_type: str | None = Query(None, description="Comma-separated content types"),
    keyword: str | None = Query(None),
    size: int = Query(20, ge=1, le=50),
    page: int = Query(0, ge=0),
    title: str | None = Query(None, description="Feed title"),
    description: str | None = Query(None, description="Feed description"),
    link_template: str | None = Query(None, description="Path template, e.g. /resource/{id}"),
) -> Response:
    """A feed of published articles.

    Typed query parameters rather than the legacy `?body=<json>` blob: a URL
    carrying a JSON document is a search API with extra steps, and it was the
    route through which a caller set `status`.
    """
    body = {
        "post_type": [p.strip() for p in (post_type or "").split(",") if p.strip()],
        "viewType": "blog",
        "size": size,
        "page": page,
        "full_article": True,
    }
    if keyword:
        body["keyword"] = keyword

    payload, status_code = services.search(body, None, public=True)
    if status_code != 200:
        return JSONResponse(status_code=status_code, content=payload)

    base_url = str(request.base_url).rstrip("/")
    feed = rss.build(
        payload, base_url=base_url, link_template=link_template,
        title=title, description=description,
    )
    return Response(content=feed, media_type="application/rss+xml; charset=utf-8")


# ---------------------------------------------------------------------------
# Authenticated
# ---------------------------------------------------------------------------


@router.post(
    "",
    responses={
        200: {"description": "Matching resources"},
        401: {"description": "Missing token, or no rights over a requested content type"},
        **_RESPONSES,
    },
)
def search(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Search the catalogue as this caller.

    A requested publication state is honoured only as far as the caller's roles
    allow: drafts need publisher or editor, the recycle bin needs administrator.
    A content type the caller cannot view is a **401** — the legacy route raised
    a bare exception that its own caller turned into a 500, which its Swagger
    documented as the behaviour.
    """
    return _respond(services.search(body, current_user.username, public=False))
