"""Audit log routes.

Port of ``app/api/logs/routes.py``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.logs import services
from archihub.core.security.jwt import (
    LEGACY_ROLE_FAILURE_STATUS,
    CurrentUser,
    require_role_any,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["Audit log"])

require_admin = require_role_any("admin", status_code=LEGACY_ROLE_FAILURE_STATUS)

_ROLE_RESPONSES = {401: {"description": "Missing/invalid token, or the admin role is required"}}


def _respond(result) -> JSONResponse:
    payload, status_code = result
    return JSONResponse(status_code=status_code, content=payload)


@router.get(
    "/actions",
    responses={200: {"description": "The audited action vocabulary"}, **_ROLE_RESPONSES},
)
def get_log_actions(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """Every action the audit log records, for building filter controls."""
    return _respond(services.get_log_actions())


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
