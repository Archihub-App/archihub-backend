"""Unauthenticated resource routes.

MOUNTED BEFORE THE AUTHENTICATED RESOURCE ROUTER - ``/resources/public/{id}``
would otherwise be captured by ``GET /resources/{resource_id}``. ``app_factory``
asserts the ordering at startup rather than trusting it.

Every route begins at ``public.load_public``, which answers 404 both for a
resource that does not exist and for one that is not public.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response

from archihub.api.resources import files, public
from archihub.core.i18n import gettext as _
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["Resources (public)"])

_RESPONSES = {404: {"description": "No such resource, or it is not public"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


@router.post(
    "/getall/public",
    responses={200: {"description": "One page of published resources"}},
)
def get_all(body: dict = Body(default_factory=dict)) -> JSONResponse:
    """The public browse listing.

    Content types that restrict viewing are **omitted** from the request rather
    than refusing it. The original answered 401 for the whole call if any one
    requested type had ``viewRoles``, so a single restricted type in a saved
    view blanked the entire public browse page.
    """
    return _respond(public.get_all(body))


@router.post(
    "/public/tree",
    responses={
        200: {"description": "One level of the public tree, or a flat list"},
        400: {"description": "Missing or unrecognised view"},
    },
)
def get_tree(body: dict = Body(default_factory=dict)) -> JSONResponse:
    """The public navigation tree (``view: tree``) or flat list (``view: list``).

    The original had no ``else`` on that branch, so any other value returned
    ``None`` and Flask rendered an empty 500.
    """
    return _respond(public.get_tree(body))


@router.post(
    "/public/download_records",
    responses={
        200: {"description": "The files, as an archive or a single attachment"},
        400: {"description": "Downloads are disabled, or an unsupported type"},
        **_RESPONSES,
    },
)
def download(body: dict = Body(default_factory=dict)) -> Response:
    """Download everything attached to a public resource.

    Declared before ``/public/{resource_id}`` so the literal segment wins.

    Two things the original got wrong here, both fixed: the archive path was
    built from the request's ``type`` (a file write to wherever the caller
    pointed it), and files the caller could not see were included in the archive
    under a placeholder name.
    """
    resource_id = body.get("id")
    if not resource_id:
        return JSONResponse(status_code=400, content={"msg": _("id is missing")})

    try:
        result = public.download(resource_id, body.get("type") or "original")
    except files.DownloadRefused as exc:
        return JSONResponse(status_code=exc.status_code, content={"msg": str(exc)})

    return _respond(result) if isinstance(result, tuple) else result


@router.post(
    "/public/{resource_id}/records",
    responses={200: {"description": "One page of the resource's files"}, **_RESPONSES},
)
def get_records(
    resource_id: str, body: dict = Body(default_factory=dict)
) -> JSONResponse:
    """The files attached to a public resource.

    A restricted file is listed without its id or hash, so the interface can
    show that something is there without it being fetchable.
    """
    return _respond(
        public.get_files(resource_id, body.get("page") or 0, bool(body.get("groupImages")))
    )


@router.get(
    "/public/{resource_id}/imgs",
    responses={200: {"description": "How many images the resource holds"}, **_RESPONSES},
)
def get_images(resource_id: str) -> JSONResponse:
    """The image count backing the public gallery viewer's pagination."""
    return _respond(public.get_images(resource_id))


@router.get(
    "/public/{resource_id}",
    responses={200: {"description": "The resource"}, **_RESPONSES},
)
def get_by_id(resource_id: str) -> JSONResponse:
    """One public resource, with its article body hydrated.

    Blocks embedding records, snaps and favourites are resolved into content
    here, each checked for public visibility on its own - an article is
    published by one person and may cite material another restricted afterwards.
    """
    return _respond(public.get_by_id(resource_id))
