"""Login.

Port of ``app/api/auth/services.py``.

TWO INVARIANTS GOVERN THIS MODULE. Both are easy to break with a change that
looks like an improvement, so they are stated rather than left to be inferred:

1. **Every rejection produces an identical response.** Unknown account, wrong
   password, LDAP refusal, disabled account - all return the same status and the
   same message. Any observable difference between them (including a difference
   in how long the request takes) lets a caller enumerate which usernames exist.
   That is why there is a single ``_reject()`` and why nothing between it and the
   route adds detail. ``ArchiHUBTestRunner`` asserts the unknown-username and
   wrong-password responses are byte-identical.

2. **Password verification always runs.** Even when the account does not exist,
   a bcrypt comparison is performed against a dummy hash. bcrypt is deliberately
   slow, so skipping it for unknown accounts makes those requests measurably
   faster and turns response time into the same enumeration oracle that
   invariant 1 closes off. See ``_verify_password``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import bcrypt

from archihub.api.auth import ldap_auth, rate_limit
from archihub.core.i18n import gettext as _
from archihub.core.security import tokens

logger = logging.getLogger(__name__)

# Session lifetime. Deliberately longer than the framework-level default in
# settings, matching the legacy login route.
ACCESS_TOKEN_LIFETIME = timedelta(days=1)

# A real bcrypt hash of a value nobody can supply, compared against when the
# account does not exist so the work performed is the same either way.
_DUMMY_HASH = bcrypt.hashpw(b"archihub-nonexistent-account-placeholder", bcrypt.gensalt())

MSG_INVALID = "Invalid username or password"
MSG_THROTTLED = "Too many login attempts. Please try again in 10 minutes."


def _reject() -> tuple[dict, int]:
    """The single rejection response. See invariant 1."""
    return {"msg": _(MSG_INVALID)}, 401


def _verify_password(password: str, stored_hash) -> bool:
    """Constant-work password check. See invariant 2."""
    if isinstance(stored_hash, str):
        stored_hash = stored_hash.encode("utf-8")
    if not stored_hash:
        # No usable hash (e.g. an LDAP-backed account). Still spend the time.
        bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
        return False

    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash)
    except (ValueError, TypeError):
        # A malformed stored hash must read as "wrong password", not as an error
        # that distinguishes this account from any other.
        logger.error("Stored password hash is unusable for an account")
        return False


def archihub_login(username: str, password: str, client_ip: str | None = None) -> tuple[dict, int]:
    """Authenticate and issue an access token."""
    username = (username or "").strip()

    try:
        if rate_limit.is_rate_limited(username, client_ip):
            return {"msg": _(MSG_THROTTLED)}, 429
    except rate_limit.RateLimitUnavailable:
        # Fail closed - see the rate_limit module docstring.
        logger.error("Login refused: the attempt store is unavailable")
        return {"msg": _(MSG_THROTTLED)}, 429

    if not username or not password:
        rate_limit.record_attempt(username, client_ip)
        return _reject()

    # -- directory first, when configured -------------------------------
    if ldap_auth.is_enabled():
        attributes = ldap_auth.authenticate(username, password)
        if attributes:
            user = _sync_ldap_user(attributes)
            if user:
                rate_limit.clear_attempts(username, client_ip)
                return _issue_token(user["username"])
            return _reject()
        # Fall through to the local check: a directory rejection does not mean
        # the account has no local credentials.

    # -- local credentials ----------------------------------------------
    from archihub.api.users.services import get_user

    user = get_user(username)
    password_ok = _verify_password(password, (user or {}).get("password"))

    if not user or not password_ok:
        rate_limit.record_attempt(username, client_ip)
        return _reject()

    rate_limit.clear_attempts(username, client_ip)
    return _issue_token(user["username"])


def _issue_token(username: str) -> tuple[dict, int]:
    token = tokens.create_access_token(username, expires_delta=ACCESS_TOKEN_LIFETIME)
    _register_log(username, "user_login", {})
    logger.info("Login succeeded for %s", username)
    return {"access_token": token}, 200


def _sync_ldap_user(attributes: dict) -> dict | None:
    """Ensure a directory-authenticated user has a local record.

    The local record is what carries roles and access rights; the directory only
    proves identity.
    """
    from archihub.api.users.services import get_user, register_user

    username = attributes.get("mail") or ""
    if not username:
        logger.warning("Directory returned no mail attribute; cannot map to a local account")
        return None

    existing = get_user(username)
    if existing:
        return existing

    created, status = register_user(
        {
            "username": username,
            "name": attributes.get("cn") or username,
            "password": "",
            "loginType": "ldap",
            "roles": ["user"],
            "accessRights": [],
        }
    )
    if status != 201:
        logger.error("Could not create a local record for a directory user")
        return None

    return get_user(username)


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)
