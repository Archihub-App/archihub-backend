"""Fernet-wrapped-JWT authentication for the public, admin and node APIs.

Port of ``app/utils/FernetAuth.py``.

These are long-lived API keys, distinct from the short-lived session JWTs in
``jwt.py``. A key is a JWT that has then been encrypted with a Fernet symmetric
key, and the *encrypted string itself* is stored on the user document. So
validation is: decrypt, decode, then check the presented string equals the one
on record - which means a key can be revoked by overwriting that field, and a
correctly-signed JWT that is not the current key is refused.

THE THREE VARIANTS ARE NOT INTERCHANGEABLE. They look like copy-paste in the
original, but they check different fields, and getting that wrong would let an
ordinary API key act as an administrative one:

    fernet_authenticate         admin -> user['adminToken'],  else user['token']
    public_fernet_authenticate  admin -> user['token'],       else user['token']
    node_fernet_authenticate    admin only -> user['nodeToken']

Note the second row. ``publicFernetAuthenticate`` checks ``token`` in *both*
branches - an admin calling the public API authenticates with their ordinary
key, not their admin key. That asymmetry is preserved exactly; merging these
into one parameterised helper is how it would get lost.

ERROR REPORTING IS ALSO ASYMMETRIC ON PURPOSE. Every failure collapses to a
generic "Invalid or expired token" so nothing about why is leaked - except the
weekly rate limit, whose message is meant to reach the caller so they know they
are throttled rather than broken. The legacy code achieved that with catch
ordering and a bare ``str(e)``; here :class:`RateLimitError` carries it, so the
distinction survives refactoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from archihub.core.errors import AuthenticationError, BusinessError, RateLimitError
from archihub.core.i18n import gettext as _
from archihub.core.security import tokens
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

# Reproduced from FernetAuth.py; these were translated there and stay so.
MSG_NO_TOKEN = "Authentication token was not provided"
MSG_EXPIRED = "The token has expired"
MSG_NO_USER = "The user does not exist"
MSG_INVALID = "The token is not valid"
MSG_NO_PERMISSION = "You do not have permission to perform this action"
MSG_GENERIC = "Invalid or expired token"


@dataclass(frozen=True)
class FernetIdentity:
    """Caller identified by an API key.

    Replaces the legacy decorators' habit of injecting ``username`` and
    ``isAdmin`` as leading positional arguments into the view function
    (``def view(username, isAdmin, *args, **kwargs)``), which coupled every
    route signature to the decorator that wrapped it.
    """

    username: str
    is_admin: bool

    def __str__(self) -> str:
        return self.username


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def _decrypt_and_decode(authorization_header: str | None) -> tuple[str, str]:
    """Return ``(raw_key, username)`` for a presented API key.

    The raw encrypted string is returned alongside the identity because it is
    what gets compared against the stored key.
    """
    if not authorization_header:
        raise AuthenticationError(_(MSG_NO_TOKEN))

    parts = authorization_header.split()
    raw_key = parts[1] if len(parts) == 2 else parts[0]

    try:
        inner_jwt = _fernet().decrypt(raw_key.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        raise AuthenticationError(_(MSG_GENERIC)) from None

    try:
        claims = tokens.decode_access_token(inner_jwt)
    except BusinessError as exc:
        # Expiry is reported distinctly, as the legacy code did; anything else
        # collapses into the generic message.
        if exc.message == tokens.MSG_EXPIRED:
            raise AuthenticationError(_(MSG_EXPIRED)) from None
        raise AuthenticationError(_(MSG_GENERIC)) from None

    return raw_key, tokens.get_identity(claims)


def _load_user(username: str) -> dict:
    from archihub.api.users.services import get_by_username

    try:
        return get_by_username(username)
    except BusinessError:
        raise AuthenticationError(_(MSG_NO_USER)) from None


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

    return has_role(username, "admin")


def _authenticate(
    authorization: str | None,
    *,
    admin_field: str,
    user_field: str,
    admin_only: bool = False,
    count_requests: bool = True,
) -> FernetIdentity:
    raw_key, username = _decrypt_and_decode(authorization)
    user = _load_user(username)
    is_admin = _is_admin(username)

    if admin_only and not is_admin:
        raise AuthenticationError(_(MSG_NO_PERMISSION))

    expected = user.get(admin_field if is_admin else user_field)
    if not expected or raw_key != expected:
        raise AuthenticationError(_(MSG_INVALID))

    # Only non-admin callers are metered, matching the legacy behaviour.
    if count_requests and not is_admin:
        _count_request(username)

    return FernetIdentity(username=username, is_admin=is_admin)


def fernet_authenticate(authorization: str | None = None) -> FernetIdentity:
    """Admin API keys: admins present ``adminToken``, everyone else ``token``."""
    return _authenticate(authorization, admin_field="adminToken", user_field="token")


def public_fernet_authenticate(authorization: str | None = None) -> FernetIdentity:
    """Public API keys: ``token`` for everyone, admins included.

    The admin branch checking ``token`` rather than ``adminToken`` is NOT a
    typo carried over - see the module docstring.
    """
    return _authenticate(authorization, admin_field="token", user_field="token")


def node_fernet_authenticate(authorization: str | None = None) -> FernetIdentity:
    """Node-to-node calls: admins only, presenting ``nodeToken``.

    Not metered - inter-node traffic (cache invalidation and similar) must not
    consume a user's public-API quota.
    """
    return _authenticate(
        authorization,
        admin_field="nodeToken",
        user_field="nodeToken",
        admin_only=True,
        count_requests=False,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
# Thin wrappers that pull the header out of the request, so routes can write
# `identity: FernetIdentity = Depends(fernet_auth_dependency)`.

def _header(request) -> str | None:  # noqa: ANN001 - starlette Request
    return request.headers.get("Authorization")


def fernet_auth_dependency(request) -> FernetIdentity:  # noqa: ANN001
    return fernet_authenticate(_header(request))


def public_fernet_auth_dependency(request) -> FernetIdentity:  # noqa: ANN001
    return public_fernet_authenticate(_header(request))


def node_fernet_auth_dependency(request) -> FernetIdentity:  # noqa: ANN001
    return node_fernet_authenticate(_header(request))
