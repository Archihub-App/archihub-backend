"""JWT encoding and decoding.

Replaces ``flask_jwt_extended``'s token handling with plain PyJWT - which the
codebase already depends on, since ``app/utils/FernetAuth.py`` used it directly
to decode the Fernet-wrapped tokens.

TOKEN COMPATIBILITY IS THE POINT OF THIS MODULE. Both stacks sign with the same
``JWT_SECRET_KEY``, and during a phased cutover both may be serving at once, so
tokens must be interchangeable in *both* directions:

* tokens are signed with ``JWT_SECRET_KEY`` and the claim set is stable, so a
  token stays valid across a restart or a redeploy - a signed-in user is not
  logged out by an upgrade;
* the same secret is read by every process, so any of them can verify a token
  another one minted.

``flask_jwt_extended`` 4.x emits exactly these claims, verified against a live
instance of the library::

    {"fresh": false, "iat": ..., "jti": "<uuid4>", "type": "access",
     "sub": "<username>", "nbf": ..., "exp": ..., "csrf": "<uuid4>"}

``create_access_token`` below reproduces that set apart from ``csrf``, which
flask_jwt_extended only validates for cookie-based tokens
(``JWT_COOKIE_CSRF_PROTECT``). ArchiHUB sends tokens in the ``Authorization``
header, so the claim is never checked; emitting one would imply a protection
this code does not actually provide. ``tests/test_jwt_compat.py`` proves the
round trip works both ways.

THE TRAP THIS MODULE EXISTS TO AVOID: PyJWT verifies signature, ``exp`` and
``nbf``, but it does **not** care about ``type``. An access token and a refresh
token differ by that one claim and nothing else. So a naive
``jwt.decode(token, key, algorithms=["HS256"])`` happily accepts a refresh token
as proof of identity - a real privilege bug, since refresh tokens are
long-lived by design. :func:`decode_access_token` checks it explicitly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from archihub.core.errors import AuthenticationError, InvalidTokenError
from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

# The /auth/login route issues 1-day tokens, overriding the framework-level
# JWT_ACCESS_TOKEN_EXPIRES (18000s / 5h) that config.py declares. Preserved.
DEFAULT_ACCESS_TOKEN_EXPIRES = timedelta(days=1)

# Messages are reproduced verbatim from flask_jwt_extended so response bodies do
# not change. They are deliberately NOT translated: the legacy ones came from
# the library's own error handlers and never passed through Babel either.
MSG_MISSING_HEADER = "Missing Authorization Header"
MSG_MISSING_BEARER = (
    "Missing 'Bearer' type in 'Authorization' header. "
    "Expected 'Authorization: Bearer <JWT>'"
)
MSG_EXPIRED = "Token has expired"
MSG_NOT_REFRESH = "Only non-refresh tokens are allowed"
MSG_BAD_SIGNATURE = "Signature verification failed"
# flask_jwt_extended echoed the raw decoder exception here, which for a
# non-base64 token leaked a Python codec error ("Invalid header string: 'utf-8'
# codec can't decode byte 0x9e..."). Replaced with a fixed string: the status
# code and shape are preserved, the internals are not.
MSG_INVALID_TOKEN = "Invalid token"


def create_access_token(
    identity: str,
    expires_delta: timedelta | None = DEFAULT_ACCESS_TOKEN_EXPIRES,
    *,
    fresh: bool = False,
) -> str:
    """Mint an access token that the legacy Flask app also accepts.

    ``expires_delta=None`` produces a token with no ``exp`` claim. The legacy
    code used that (``expires_delta=False``) for the never-expiring public API
    keys; those are now issued with a 365-day lifetime instead, but decoding
    still tolerates tokens without ``exp`` so keys minted before that change
    keep working until they are rotated.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload: dict[str, object] = {
        "fresh": fresh,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": ACCESS_TOKEN_TYPE,
        # flask_jwt_extended 4.x requires `sub` to be a string.
        "sub": str(identity),
        "nbf": now,
    }
    if expires_delta is not None:
        payload["exp"] = now + expires_delta

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and fully validate an access token.

    Raises :class:`AuthenticationError` (401) for expired tokens and
    :class:`InvalidTokenError` (422) for malformed, mis-signed or wrong-type
    tokens, mirroring the legacy split. See ``InvalidTokenError``'s docstring
    for why the two differ.
    """
    settings = get_settings()

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[ALGORITHM],
            # `exp` is verified when present but not required: tokens minted
            # before the API-key expiry change have no exp claim.
            options={"require": ["sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError(MSG_EXPIRED) from None
    except jwt.ImmatureSignatureError:
        # nbf in the future. PyJWT checks this; worth keeping distinct because a
        # token that is merely not-yet-valid is not the same as a forged one.
        raise InvalidTokenError(MSG_INVALID_TOKEN) from None
    except jwt.InvalidSignatureError:
        raise InvalidTokenError(MSG_BAD_SIGNATURE) from None
    except jwt.InvalidTokenError as exc:
        # Includes malformed base64, missing required claims, bad algorithm.
        logger.info("Rejected malformed token: %s", exc)
        raise InvalidTokenError(MSG_INVALID_TOKEN) from None

    # PyJWT does not know about flask_jwt_extended's token types, so a refresh
    # token would otherwise sail through - see the module docstring.
    token_type = claims.get("type")
    if token_type is not None and token_type != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError(MSG_NOT_REFRESH)

    return claims


def extract_bearer_token(authorization_header: str | None) -> str:
    """Pull the credential out of an ``Authorization: Bearer <jwt>`` header.

    Both failure modes are 401 and both messages match the legacy ones, because
    the frontend surfaces them.
    """
    if not authorization_header:
        raise AuthenticationError(MSG_MISSING_HEADER)

    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError(MSG_MISSING_BEARER)

    return parts[1]


def get_identity(claims: dict) -> str:
    """Return the username a set of claims identifies."""
    subject = claims.get("sub")
    if not subject:
        raise InvalidTokenError(_("Invalid or expired token"))
    return str(subject)
