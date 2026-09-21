"""Error handling and the HTTP status-code policy.

``BusinessError`` and its subclasses carry a translated, user-facing message and
a status code, and are rendered as-is. Everything else is an unexpected failure:
logged in full server-side, rendered as a generic message client-side, so raw
exception text - internals, sometimes connection strings - never reaches a
response.

Every error body is ``{"msg": "<text>"}``, which ``upgrade_front``'s error
handling reads.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """A failure the caller is meant to see and can act on.

    ``message`` must already be translated (i.e. wrapped in ``_()``) - it is
    rendered verbatim into the response body.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        super().__init__(message)


class ValidationError(BusinessError):
    """Malformed or missing input the client should correct."""

    status_code = status.HTTP_400_BAD_REQUEST


class AuthenticationError(BusinessError):
    """Who are you? - missing, invalid or expired credentials."""

    status_code = status.HTTP_401_UNAUTHORIZED


class PermissionDeniedError(BusinessError):
    """Authenticated, but not allowed.

    Role/right failures raise this; only identity failures raise
    AuthenticationError.
    """

    status_code = status.HTTP_403_FORBIDDEN


class InvalidTokenError(BusinessError):
    """A token that is present but unusable: malformed, badly signed, wrong type.

    422, NOT 401 - and that is deliberate. The split is part of the wire contract:

        missing header / expired token  -> 401
        malformed / bad signature /
        refresh token used as access    -> 422

    Making this 401 would make the frontend redirect to login instead of
    showing an error; that is a contract change to make together with the
    frontend.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class NotFoundError(BusinessError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(BusinessError):
    """Uniqueness violations - duplicate username, duplicate slug, ..."""

    status_code = status.HTTP_409_CONFLICT


class RateLimitError(BusinessError):
    """Quota exhausted.

    Exists as its own type so the Fernet authenticators can let this one
    specific message through while still hiding every other exception behind a
    generic 'Invalid or expired token'.
    """

    status_code = status.HTTP_429_TOO_MANY_REQUESTS


def register_exception_handlers(app: FastAPI) -> None:
    """Install the handlers that produce the ``{"msg": ...}`` envelope."""

    @app.exception_handler(BusinessError)
    def _handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
        logger.info(
            "Business error on %s %s: %s", request.method, request.url.path, exc.message
        )
        return JSONResponse(status_code=exc.status_code, content={"msg": exc.message})

    @app.exception_handler(StarletteHTTPException)
    def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Explicitly-raised HTTPExceptions are already precise (the test-control
        # gate depends on its exact 404/403/401 bodies), so they pass through
        # unmodified - only normalised into the {"msg": ...} envelope when the
        # detail is a plain string.
        detail = exc.detail
        content = detail if isinstance(detail, dict) else {"msg": detail}
        return JSONResponse(
            status_code=exc.status_code, content=content, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default is a 422 with a list of per-field error objects.
        # That shape is new to this API and no existing client parses it, so the
        # {"msg": ...} envelope is preserved and the field details are attached
        # under a separate key for debugging.
        logger.info("Request validation failed on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "msg": _("The request could not be processed. Check the submitted fields."),
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The whole point: full detail to the log, nothing to the client.
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"msg": _("An unexpected error occurred")},
        )
