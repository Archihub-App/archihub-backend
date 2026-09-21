"""JWT encoding and decoding.

Plain PyJWT. Tokens keep the claim set ArchiHUB has always issued, so tokens
issued before an upgrade remain valid after it:

* tokens are signed with ``JWT_SECRET_KEY`` and the claim set is stable, so a
  token stays valid across a restart or a redeploy - a signed-in user is not
  logged out by an upgrade;
* the same secret is read by every process, so any of them can verify a token
  another one minted.

The claims::

    {"fresh": false, "iat": ..., "jti": "<uuid4>", "type": "access",
     "sub": "<username>", "nbf": ..., "exp": ..., "csrf": "<uuid4>"}

``create_access_token`` emits that set apart from ``csrf``, which only matters
for cookie-based tokens. ArchiHUB sends tokens in the ``Authorization`` header,
so the claim would never be checked; emitting one would imply a protection this
code does not provide.

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

# Lifetime of a token issued at login.
DEFAULT_ACCESS_TOKEN_EXPIRES = timedelta(days=1)

# Fixed English messages, deliberately NOT translated: clients match on them.
MSG_MISSING_HEADER = "Missing Authorization Header"
MSG_MISSING_BEARER = (
    "Missing 'Bearer' type in 'Authorization' header. "
    "Expected 'Authorization: Bearer <JWT>'"
)
MSG_EXPIRED = "Token has expired"
MSG_NOT_REFRESH = "Only non-refresh tokens are allowed"
MSG_BAD_SIGNATURE = "Signature verification failed"
# A fixed string, never the decoder's exception: that would expose internals
# such as a codec error for a non-base64 token.
MSG_INVALID_TOKEN = "Invalid token"


def create_access_token(
    identity: str,
    expires_delta: timedelta | None = DEFAULT_ACCESS_TOKEN_EXPIRES,
    *,
    fresh: bool = False,
) -> str:
    """Mint an access token.

    ``expires_delta=None`` produces a token with no ``exp`` claim. Decoding
    tolerates tokens without ``exp`` so API keys issued without an expiry keep
    working until they are rotated.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload: dict[str, object] = {
        "fresh": fresh,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": ACCESS_TOKEN_TYPE,
        # `sub` is always a string.
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
    tokens. See ``InvalidTokenError``'s docstring for why the two differ.
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

    # PyJWT does not know about token types, so a refresh token would otherwise
    # pass - see the module docstring.
    token_type = claims.get("type")
    if token_type is not None and token_type != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError(MSG_NOT_REFRESH)

    return claims


def extract_bearer_token(authorization_header: str | None) -> str:
    """Pull the credential out of an ``Authorization: Bearer <jwt>`` header.

    Both failure modes are 401, with fixed messages the frontend surfaces.
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
