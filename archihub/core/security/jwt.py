"""JWT authentication and role authorisation dependencies.

Replaces ``@jwt_required()`` + ``get_jwt_identity()`` (202 usages) and the
hand-written ``if not has_role(current_user, 'admin'): return 401`` blocks that
follow most of them.

A route that used to read::

    @bp.route('/thing', methods=['GET'])
    @jwt_required()
    def get_thing():
        current_user = get_jwt_identity()
        if not has_role(current_user, 'admin'):
            return {'msg': 'You do not have sufficient permissions'}, 401
        ...

becomes::

    @router.get('/thing', dependencies=[Depends(require_role_any('admin'))])
    def get_thing(current_user: CurrentUser = Depends(get_current_user)):
        ...

Two things change on purpose, and only one of them is visible on the wire.

* The identity arrives as a declared parameter rather than through a request
  global, so the dependency that enforces auth and the one that documents it in
  OpenAPI are the same object. A route can no longer enforce a role while
  forgetting to declare it (or vice versa).
* **Role failures return 403, not 401.** 401 means "I don't know who you are",
  which signing in fixes; 403 means "I know, and no", which it does not. Almost
  every check here runs on an already-authenticated caller, so 403 is the honest
  answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from archihub.core.errors import PermissionDeniedError
from archihub.core.i18n import gettext as _
from archihub.core.security import tokens

logger = logging.getLogger(__name__)

# Message reproduced from the legacy role checks, which did translate it.
MSG_INSUFFICIENT_PERMISSIONS = "You do not have sufficient permissions"


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller."""

    username: str
    claims: dict

    def __str__(self) -> str:  # so it can be passed where a username is expected
        return self.username


# `auto_error=False` is load-bearing. This scheme is declared purely so FastAPI
# advertises bearer auth in the OpenAPI document (the equivalent of Flasgger's
# `securityDefinitions`, which the legacy app set by hand) and renders an
# Authorize button in /docs. With auto_error=True, HTTPBearer would reject a
# missing header itself with `403 {"detail": "Not authenticated"}` - both a
# different status code AND a different body key from the legacy
# `401 {"msg": "Missing Authorization Header"}`. Letting it stay quiet keeps
# documentation and wire contract from fighting each other.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="JWT",
    description="Session token issued by POST /auth/login. Send as: Authorization: Bearer <token>",
)


def get_current_user(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    """Authenticate the caller from the ``Authorization: Bearer <jwt>`` header.

    The header is re-read off the raw request rather than taken from
    ``_credentials`` so that malformed values (no ``Bearer`` prefix, empty
    header) produce the legacy messages instead of HTTPBearer's own. The
    injected credentials exist only to document the scheme - see above.
    """
    token = tokens.extract_bearer_token(request.headers.get("Authorization"))
    claims = tokens.decode_access_token(token)
    return CurrentUser(username=tokens.get_identity(claims), claims=claims)


# The status a role or right failure produces.
#
# 403, never 401. The two answer different questions: 401 means "I do not know
# who you are", which signing in fixes, and 403 means "I know who you are and
# you may not do this", which it does not. Collapsing them tells a user to log
# in again to solve a problem that logging in cannot solve.
#
# Named and exported rather than written as a literal because services return
# ``(payload, status)`` tuples, where a bare 403 says nothing about which kind
# of refusal it is. Route dependencies do not need it - `PermissionDeniedError`
# already carries this status - so passing it there is redundant.
ROLE_FAILURE_STATUS = 403


def require_role_any(*roles: str, status_code: int | None = None) -> Callable[..., CurrentUser]:
    """Dependency factory: require at least one of ``roles``.

    Passes when the caller holds *any* of the listed roles.

    ``status_code`` overrides the refusal status for the rare route that needs a
    different one; it defaults to `PermissionDeniedError`'s own 403, which is
    what a role failure should be.
    """

    def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        from archihub.api.users.services import has_role

        if not any(has_role(current_user.username, role) for role in roles):
            logger.info(
                "Denied %s access requiring one of %s", current_user.username, list(roles)
            )
            raise PermissionDeniedError(
                _(MSG_INSUFFICIENT_PERMISSIONS), status_code=status_code
            )
        return current_user

    return _dependency


def require_right(right: str) -> Callable[..., CurrentUser]:
    """Dependency factory: require a specific access right."""

    def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        from archihub.api.users.services import has_right

        if not has_right(current_user.username, right):
            logger.info("Denied %s access requiring right %s", current_user.username, right)
            raise PermissionDeniedError(_(MSG_INSUFFICIENT_PERMISSIONS))
        return current_user

    return _dependency


# Convenience aliases for the two combinations that dominate the codebase.
require_admin = require_role_any("admin")
require_admin_or_processing = require_role_any("admin", "processing")
