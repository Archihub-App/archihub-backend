"""Audit log routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from archihub.api.logs import services
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["Audit log"])

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
    "/actions",
    responses={200: {"description": "The audited action vocabulary"}, **_ROLE_RESPONSES},
)
def get_log_actions(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """Every action the audit log records, for building filter controls."""
    return _respond(services.get_log_actions())


@router.get(
    "/recent",
    responses={
        200: {"description": "Recent activity, filtered to what the caller may see"},
        400: {"description": "Unknown category, or an unparseable date"},
        401: {"description": "Missing or invalid token"},
    },
)
def recent_activity(
    # No `ge`/`le` here: the clamp lives in the service, and stating the bound
    # in two places means a client asking for more than the ceiling is refused
    # by one layer and quietly served by the other. Clamped rather than refused
    # because a feed asking for more than it may have wants as much as it can
    # get, not an error.
    limit: int = Query(default=20, description="Entries to return; at most 100"),
    offset: int = Query(default=0, description="Where to start"),
    category: str = Query(
        default="all",
        description="all, cataloging, system, processing or security",
    ),
    since: str | None = Query(default=None, description="ISO 8601 instant"),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Recent activity, newest first, filtered by role.

    ANY AUTHENTICATED CALLER may ask - what changes is the answer. An
    administrator sees everything; an editor sees cataloguing and processing but
    neither security nor infrastructure; anyone else sees only what they did
    themselves. The restriction is a clause in the query, joined with `$and`
    against the caller's own filters, so `category` can only ever narrow it.

    A role gate would be the wrong shape here: this is a personal feed that
    every signed-in user has, not an administrative report.
    """
    from archihub.api.logs import recent as recent_activity_service

    return _respond(recent_activity_service.recent(
        {"limit": limit, "offset": offset, "category": category, "since": since},
        current_user.username,
    ))


@router.post(
    "",
    responses={200: {"description": "Paginated audit entries, newest first"}, **_ROLE_RESPONSES},
)
def filter_logs(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Read the audit log.

    A POST because the filter travels in the body - the legacy shape.

    Returns an empty list when nothing matches, not a 404: no entries matching a
    filter is a successful query with no results. (The legacy 404 could never
    fire anyway - it tested a pymongo cursor for emptiness, and a cursor is
    always truthy.)
    """
    return _respond(services.filter_logs(body))


@router.post(
    "/resource/{resource_id}",
    responses={200: {"description": "Field-level change history"}, **_ROLE_RESPONSES},
)
def get_logs_for_resource(
    resource_id: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Change history for one resource, as field-level diffs."""
    return _respond(services.get_logs_for_resource(body, resource_id))
