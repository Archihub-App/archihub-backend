"""Error handling and the HTTP status-code policy.

Two problems this module exists to prevent:

* ~233 sites do ``except Exception as e: return {'msg': str(e)}, 500``, leaking
  raw exception text - stack-adjacent internals, sometimes connection strings -
  straight into API responses.
* Exceptions are *also* the intended way to deliver user-facing business
  messages: code raises ``Exception(_('You have reached the limit of requests
  for this week'))`` specifically so the text reaches the client. A blanket
  "never show exception text" rule would therefore break real UX.

The fix is a distinguished exception hierarchy. ``BusinessError`` and its
subclasses carry a translated, user-facing message and a status code, and are
rendered as-is. Everything else is an unexpected failure: logged in full
server-side, rendered as a generic message client-side.

This makes conversion incremental. A domain can be ported with all of its error
paths still falling through to the generic 500 handler, then have specific
exception types introduced afterwards, without either step blocking the other.

RESPONSE SHAPE IS UNCHANGED: every error is ``{"msg": "<text>"}``, exactly as the
Flask app emitted, so ``upgrade_front``'s error handling keeps working. No
machine-readable ``error_code`` field is added - that would be an additive
change to make deliberately later, not a side effect of the framework swap.
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

    This is the systematic correction of the legacy 401-for-everything habit:
    the old code used 401 at ~223 sites and 403 exactly once, even though most
    of those are role checks on an already-authenticated user. Role/right
    failures raise this; only identity failures raise AuthenticationError.
    """

    status_code = status.HTTP_403_FORBIDDEN


class InvalidTokenError(BusinessError):
    """A token that is present but unusable: malformed, badly signed, wrong type.

    422, NOT 401 - and that is deliberate. flask_jwt_extended splits these two
    cases and the split is part of the wire contract this migration preserves:

        missing header / expired token  -> 401
        malformed / bad signature /
        refresh token used as access    -> 422

    ``upgrade_front`` performs exact status-code equality checks at roughly 187
    call sites, so silently collapsing 422 into 401 here would be a behavioural
    change smuggled in under a framework swap. If this should become 401 (which
    is arguably more correct, and would make the frontend redirect to login
    instead of showing an error), that is a deliberate contract change to make
    alongside a frontend audit.
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
    generic 'Invalid or expired token'. In the legacy code that asymmetry was
    achieved by catch-ordering plus a bare ``str(e)``; here it is structural.
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
