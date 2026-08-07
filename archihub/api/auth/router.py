"""Authentication routes.

Port of ``app/api/auth/routes.py`` - one route, unauthenticated by necessity.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from archihub.api.auth import services
from archihub.api.auth.schemas import LoginRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _client_ip(request: Request) -> str | None:
    """Best-effort client address for per-address throttling.

    ``X-Forwarded-For`` is only consulted because this runs behind the bundled
    nginx reverse proxy. The header is client-controlled and trivially spoofed,
    so it is used ONLY to widen throttling, never to grant anything - and the
    per-username budget applies regardless of what it says.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/login",
    responses={
        200: {"description": "Authenticated; returns an access token"},
        401: {"description": "Invalid username or password"},
        429: {"description": "Too many login attempts"},
    },
)
def login(request: Request, body: LoginRequest = Body(...)) -> JSONResponse:
    """Authenticate a user and issue an access token.

    Every rejection returns the same status and message, whether or not the
    account exists - see the invariants in ``services``.
    """
    payload, status_code = services.archihub_login(
        body.username, body.password, client_ip=_client_ip(request)
    )
    return JSONResponse(status_code=status_code, content=payload)
