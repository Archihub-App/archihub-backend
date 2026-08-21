"""API-key authentication for the external APIs.

These are long-lived credentials for ``/adminApi``, ``/publicApi`` and the
node-to-node routes, distinct from the short-lived session JWTs in ``jwt.py``.
Keys look like ``ahk_<key_id>_<secret>`` and are verified against a stored hash;
see ``core/security/api_keys.py`` for why that shape.

**THE THREE VARIANTS ARE NOT INTERCHANGEABLE.** They authorise different things,
and collapsing them into one parameterised helper is how that distinction gets
lost:

    authenticate_admin_api    any scope; the ROLE check is at the route
    authenticate_public_api   the ``public`` scope only, and metered
    authenticate_node_api     the ``node`` scope only, administrators only,
                              and never metered

Metering is per-variant on purpose. Inter-node traffic - cache invalidation and
similar - must not consume a person's weekly public-API quota, and an
administrator is not metered at all.

**ERROR REPORTING IS ASYMMETRIC, ALSO ON PURPOSE.** Every failure collapses to a
single generic "Invalid or expired token", so a probe cannot learn whether a key
is unknown, revoked, expired or simply the wrong scope. The one exception is the
weekly rate limit, whose message is meant to reach the caller so they know they
are throttled rather than broken. :class:`RateLimitError` carries that
distinction as a *type*, so it survives refactoring; expressing it through catch
ordering and a bare ``str(e)`` does not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from archihub.core.errors import AuthenticationError, RateLimitError
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

MSG_NO_TOKEN = "Authentication token was not provided"
MSG_NO_PERMISSION = "You do not have permission to perform this action"
MSG_GENERIC = "Invalid or expired token"


@dataclass(frozen=True)
class ApiIdentity:
    """Caller identified by an API key.

    A returned object rather than arguments injected into the view: the legacy
    decorators prepended ``username`` and ``isAdmin`` as positional parameters
    (``def view(username, isAdmin, *args, **kwargs)``), which coupled every
    route signature to the decorator wrapping it.
    """

    username: str
    is_admin: bool

    def __str__(self) -> str:
        return self.username


def _count_request(username: str) -> None:
    """Charge the caller's weekly quota, letting the throttle message through."""
    from archihub.api.users.services import add_request

    try:
        add_request(username)
    except RateLimitError:
        # Deliberately propagated: the caller is meant to see this one.
        raise
    except Exception:
        logger.warning("Could not record API request for %s", username, exc_info=True)


def _is_admin(username: str) -> bool:
    from archihub.api.users.services import has_role

    return bool(has_role(username, "admin"))


def _presented(authorization: str | None) -> str:
    """The credential out of an ``Authorization`` header.

    Accepts it with or without the ``Bearer`` prefix, as the external API's
    callers have always sent both.
    """
    if not authorization:
        raise AuthenticationError(_(MSG_NO_TOKEN))
    parts = authorization.split()
    return parts[1] if len(parts) == 2 else parts[0]


def _authenticate(
    authorization: str | None,
    *,
    scope: str | None,
    admin_only: bool = False,
    count_requests: bool = True,
) -> ApiIdentity:
    from archihub.core.security import api_keys

    result = api_keys.verify_key(_presented(authorization), required_scope=scope)
    if result is None:
        # One message for every reason - unknown, revoked, expired, wrong
        # scope, wrong format. See the module docstring.
        raise AuthenticationError(_(MSG_GENERIC))

    is_admin = _is_admin(result.username)
    if admin_only and not is_admin:
        raise AuthenticationError(_(MSG_NO_PERMISSION))

    if count_requests and not is_admin:
        _count_request(result.username)

    return ApiIdentity(username=result.username, is_admin=is_admin)


def authenticate_admin_api(authorization: str | None = None) -> ApiIdentity:
    """Authenticate a caller of ``/adminApi``.

    Scope is not constrained here; the administrator requirement is applied by
    the route's own dependency, which is where the refusal is meaningful.
    """
    return _authenticate(authorization, scope=None)


def authenticate_public_api(authorization: str | None = None) -> ApiIdentity:
    """Authenticate a caller of ``/publicApi``. Metered against their quota."""
    from archihub.core.security import api_keys

    return _authenticate(authorization, scope=api_keys.SCOPE_PUBLIC)


def authenticate_node_api(authorization: str | None = None) -> ApiIdentity:
    """Authenticate a node-to-node call: ``node`` scope, administrators only.

    Never metered - a peer invalidating a cache must not spend a person's
    public-API quota.
    """
    from archihub.core.security import api_keys

    return _authenticate(
        authorization,
        scope=api_keys.SCOPE_NODE,
        admin_only=True,
        count_requests=False,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
# Thin wrappers that pull the header out of the request, so a route can write
# `identity: ApiIdentity = Depends(admin_api_dependency)`.


def _header(request) -> str | None:  # noqa: ANN001 - starlette Request
    return request.headers.get("Authorization")


def admin_api_dependency(request) -> ApiIdentity:  # noqa: ANN001
    return authenticate_admin_api(_header(request))


def public_api_dependency(request) -> ApiIdentity:  # noqa: ANN001
    return authenticate_public_api(_header(request))


def node_api_dependency(request) -> ApiIdentity:  # noqa: ANN001
    return authenticate_node_api(_header(request))
