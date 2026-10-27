"""Authentication routes.

One route, unauthenticated by necessity.
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
    """The client address used for per-address throttling.

    The address of the connection, never a request header: a header is
    whatever the client wrote. Behind a reverse proxy, uvicorn rewrites the
    connection address from ``X-Forwarded-For`` only when the proxy is one of
    ``FORWARDED_ALLOW_IPS`` (127.0.0.1 unless configured).
    """
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
