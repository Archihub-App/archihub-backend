"""API keys.

Long-lived credentials for the public, admin and node APIs, distinct from the
short-lived session JWTs in ``jwt.py``.

DESIGN, AND WHY IT IS THIS SHAPE
--------------------------------

**The server never stores anything that can be used to authenticate.** Only a
hash of each secret is kept. A read of the database - a backup, a `mongodump`, an
injection bug, an insider - yields nothing that can be presented to the API. This
is the single most important property here, and it is the one the previous scheme
did not have: there, the stored value *was* the credential, so anyone who could
read the users collection held working keys for every user.

**A key is shown exactly once, at creation.** It cannot be retrieved afterwards
because the server genuinely does not have it. Losing one means issuing a new one.

**Key format:** ``ahk_<key_id>_<secret>``

* ``key_id`` (16 hex chars) is a public lookup handle. It lets verification be a
  single indexed point-read; without it the server would have to scan and hash
  against every stored key.
* ``secret`` is 32 bytes of ``secrets.token_urlsafe`` entropy. It is what gets
  hashed, and it never appears in a query - only its hash is compared.

Splitting the two matters: the handle can be logged, displayed and used as a
database key without ever exposing the part that authenticates.

**SHA-256, not bcrypt or argon2.** That is deliberate and is the opposite of the
right answer for passwords. The secret carries 256 bits of entropy, so an
offline attack against the hash is infeasible no matter how fast the hash is -
there is no dictionary to try. A slow KDF would instead add its cost to *every
API request*, which for a machine-to-machine credential checked on every call is
a real latency budget spent for no gain. Slow hashing exists to compensate for
low-entropy human-chosen secrets; that does not apply here.

**Comparison is constant-time** (``hmac.compare_digest``), so verification does
not leak how much of a candidate hash matched.

**Keys live in their own collection**, not on the user document. That allows
several named keys per user, individual revocation, and last-used visibility -
and it keeps credentials out of a document that is projected and returned all
over the application.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

COLLECTION = "api_keys"

PREFIX = "ahk"
KEY_ID_BYTES = 8       # -> 16 hex characters
SECRET_BYTES = 32      # -> 43 url-safe base64 characters

# Scopes mirror the four credential types the legacy schema kept as separate
# fields on the user document.
SCOPE_PUBLIC = "public"
SCOPE_ADMIN = "admin"
SCOPE_NODE = "node"
SCOPE_VIZ = "viz"
SCOPES = frozenset({SCOPE_PUBLIC, SCOPE_ADMIN, SCOPE_NODE, SCOPE_VIZ})

DEFAULT_LIFETIME = timedelta(days=365)

# `last_used` is refreshed at most this often. Writing on every request would
# turn a read-only API call into a write and put one update per request on the
# users' hottest path, for information that is only ever read by a human.
LAST_USED_RESOLUTION = timedelta(hours=1)


@dataclass(frozen=True)
class ApiKeyIdentity:
    """A caller authenticated by an API key."""

    username: str
    scope: str
    key_id: str

    def __str__(self) -> str:
        return self.username


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def hash_secret(secret: str) -> str:
    """Hash a key secret. See the module docstring for why this is SHA-256."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def format_key(key_id: str, secret: str) -> str:
    return f"{PREFIX}_{key_id}_{secret}"


def parse_key(presented: str) -> tuple[str, str] | None:
    """Split a presented key into ``(key_id, secret)``, or None if not ours.

    Returning None rather than raising is what lets the caller fall back to the
    legacy credential scheme for values that predate this one.
    """
    if not presented or not presented.startswith(f"{PREFIX}_"):
        return None

    parts = presented.split("_", 2)
    if len(parts) != 3:
        return None

    _, key_id, secret = parts
    if not key_id or not secret:
        return None
    return key_id, secret


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------


def create_key(
    username: str,
    scope: str = SCOPE_PUBLIC,
    *,
    name: str | None = None,
    expires_in: timedelta | None = DEFAULT_LIFETIME,
) -> str:
    """Issue a key and return it. THIS IS THE ONLY TIME IT CAN BE READ.

    The caller is responsible for getting it to the user; nothing stored here
    can reproduce it.
    """
    if scope not in SCOPES:
        raise ValueError(f"Unknown API key scope: {scope}")

    key_id = secrets.token_hex(KEY_ID_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    now = datetime.now()

    _mongo().insert_record(
        COLLECTION,
        {
            "key_id": key_id,
            "secret_hash": hash_secret(secret),
            "user": username,
            "scope": scope,
            "name": name or scope,
            "created_at": now,
            "last_used_at": None,
            "expires_at": (now + expires_in) if expires_in else None,
            "revoked_at": None,
        },
    )
    logger.info("Issued %s API key %s for %s", scope, key_id, username)
    return format_key(key_id, secret)


def revoke_key(key_id: str, username: str | None = None) -> bool:
    """Revoke one key. Scoped to ``username`` when given, so a user can only
    revoke their own."""
    filters: dict = {"key_id": key_id, "revoked_at": None}
    if username is not None:
        filters["user"] = username

    result = _mongo().update_record(COLLECTION, filters, {"revoked_at": datetime.now()})
    revoked = bool(getattr(result, "modified_count", 0))
    if revoked:
        logger.info("Revoked API key %s", key_id)
    return revoked


def revoke_all(username: str, scope: str | None = None) -> None:
    """Revoke every live key for a user - used when disabling an account."""
    filters: dict = {"user": username, "revoked_at": None}
    if scope:
        filters["scope"] = scope
    _mongo().update_records(COLLECTION, filters, {"revoked_at": datetime.now()})


def list_keys(username: str) -> list[dict]:
    """A user's keys, described but never reproduced.

    Deliberately omits ``secret_hash``: there is nothing useful a client can do
    with it, and it should not travel.
    """
    records = _mongo().get_all_records(
        COLLECTION,
        {"user": username},
        sort=[("created_at", -1)],
        fields={"secret_hash": 0, "_id": 0},
    )
    return [
        {
            "key_id": record.get("key_id"),
            "scope": record.get("scope"),
            "name": record.get("name"),
            "created_at": _iso(record.get("created_at")),
            "last_used_at": _iso(record.get("last_used_at")),
            "expires_at": _iso(record.get("expires_at")),
            "revoked": record.get("revoked_at") is not None,
        }
        for record in records
    ]


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------


def verify_key(presented: str, *, required_scope: str | None = None) -> ApiKeyIdentity | None:
    """Authenticate a presented key, or return None.

    None means "not valid" for every reason - unknown, revoked, expired, wrong
    scope, or simply not in this format. The caller turns that into a single
    generic failure, so a probe cannot tell which.
    """
    parsed = parse_key(presented)
    if parsed is None:
        return None

    key_id, secret = parsed

    record = _mongo().get_record(COLLECTION, {"key_id": key_id})
    if not record:
        # Still hash, so a request for an unknown handle costs the same as one
        # for a known handle with a wrong secret.
        hash_secret(secret)
        return None

    stored_hash = record.get("secret_hash") or ""
    if not hmac.compare_digest(hash_secret(secret), stored_hash):
        return None

    if record.get("revoked_at") is not None:
        return None

    expires_at = record.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at < datetime.now():
        return None

    if required_scope and record.get("scope") != required_scope:
        return None

    _touch(record)
    return ApiKeyIdentity(
        username=record.get("user", ""), scope=record.get("scope", ""), key_id=key_id
    )


def _touch(record: dict) -> None:
    """Refresh ``last_used_at``, at most once per resolution window."""
    last_used = record.get("last_used_at")
    now = datetime.now()

    if isinstance(last_used, datetime) and now - last_used < LAST_USED_RESOLUTION:
        return

    try:
        _mongo().update_record(
            COLLECTION, {"key_id": record.get("key_id")}, {"last_used_at": now}
        )
    except Exception:
        # Bookkeeping must never fail a request that is otherwise authorised.
        logger.debug("Could not update last_used_at", exc_info=True)
