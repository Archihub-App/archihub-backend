"""Fernet-wrapped-JWT authentication for the public, admin and node APIs.

Port of ``app/utils/FernetAuth.py``.

These are long-lived API keys, distinct from the short-lived session JWTs in
``jwt.py``.

TWO SCHEMES LIVE HERE, AND THE ORDER MATTERS.

**Current** - ``core/security/api_keys.py``. Keys look like
``ahk_<key_id>_<secret>``; the server stores only a hash, so nothing readable
from the database can be presented to the API. This is what new keys use, and it
is tried first.

**Deprecated scheme** - a JWT encrypted with a Fernet key, the ciphertext stored
verbatim on the user document and compared against the presented string. The
stored value *is* the credential, so a database read yields working keys. It is
retained ONLY so that credentials already in circulation keep working; nothing
issues new ones. Once no deployment has a populated ``token`` / ``adminToken`` /
``nodeToken`` / ``vizToken`` field left, this path and those fields should be
deleted.

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
are throttled rather than broken. :class:`RateLimitError` carries that
distinction as a type, so it survives refactoring; expressing it through catch
ordering and a bare ``str(e)`` does not.
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


def _extract_presented(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError(_(MSG_NO_TOKEN))
    parts = authorization.split()
    return parts[1] if len(parts) == 2 else parts[0]


def _authenticate(
    authorization: str | None,
    *,
    admin_field: str,
    user_field: str,
    admin_only: bool = False,
    count_requests: bool = True,
    scope: str | None = None,
) -> FernetIdentity:
    # Current scheme first. Keys are `ahk_<key_id>_<secret>`, verified against a
    # stored hash - see core/security/api_keys.py.
    presented = _extract_presented(authorization)
    identity = _verify_api_key(presented, scope=scope, admin_only=admin_only)
    if identity is not None:
        if count_requests and not identity.is_admin:
            _count_request(identity.username)
        return identity

    # Fall back to the previous scheme for credentials issued before it, so a
    # key that works today keeps working. New keys are never issued this way.
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


def _verify_api_key(presented: str, *, scope: str | None, admin_only: bool) -> FernetIdentity | None:
    """Try the current API-key scheme. None means "not one of ours"."""
    from archihub.core.security import api_keys

    result = api_keys.verify_key(presented, required_scope=scope)
    if result is None:
        return None

    is_admin = _is_admin(result.username)
    if admin_only and not is_admin:
        raise AuthenticationError(_(MSG_NO_PERMISSION))

    return FernetIdentity(username=result.username, is_admin=is_admin)


def fernet_authenticate(authorization: str | None = None) -> FernetIdentity:
    """Admin API keys: admins present ``adminToken``, everyone else ``token``."""
    return _authenticate(
        authorization, admin_field="adminToken", user_field="token", scope=None
    )


def public_fernet_authenticate(authorization: str | None = None) -> FernetIdentity:
    """Public API keys: ``token`` for everyone, admins included.

    The admin branch checking ``token`` rather than ``adminToken`` is NOT a
    typo carried over - see the module docstring.
    """
    return _authenticate(
        authorization, admin_field="token", user_field="token", scope=api_key_scope_public()
    )


def api_key_scope_public() -> str:
    from archihub.core.security import api_keys

    return api_keys.SCOPE_PUBLIC


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
        scope="node",
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
