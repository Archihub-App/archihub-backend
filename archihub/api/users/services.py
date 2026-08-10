"""User lookups and authorisation checks.

PARTIAL PORT. Only the helpers the authentication layer needs are here -
``get_by_username``, ``has_role``, ``has_right``, ``add_request``. The rest of
the users domain (CRUD, tokens, profile, favourites) lands in Phase 3 step 2.
Auth has to come first because every other domain's routes depend on it, so
these few functions are pulled forward.

INVARIANTS THIS MODULE ENFORCES. Both are easy to get wrong and both are
security-relevant, so they are stated as rules rather than left implicit.

1. AN AUTHORISATION HELPER ALWAYS RETURNS A REAL ``bool`` - never a response
   object, tuple, dict or ``None``. Callers write ``if not has_role(...): deny``,
   and a non-empty tuple is truthy, so any helper that returns a
   response-shaped value on the "unknown user" path inverts the guard that uses
   it. There must remain exactly ONE implementation of each of these helpers;
   do not add a variant elsewhere in the tree.

2. Authorisation is decided in ONE place, including the
   ``_is_valid_system_user`` allowance for scheduled-task pseudo-users. Two
   functions of the same name with different rules is how the two drift apart.

CACHING IS DELIBERATELY NOT WIRED UP YET. Correctness depends on 119
``.invalidate()``/``.invalidate_all()`` call sites across the codebase, which are
not ported. Caching permission checks while those are missing would serve stale
authorisation decisions - strictly worse than an uncached lookup. Restored in
Phase 3 step 2, together with its invalidation sites.
"""

from __future__ import annotations

import bcrypt
import datetime
import logging

from archihub.core.errors import NotFoundError, RateLimitError
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)


def serialise(result):
    """Mongo types (``ObjectId``, ``datetime``) into their JSON forms.

    The same ``json_util`` pass every other domain applies, so an ``_id`` is
    ``{"$oid": ...}`` here as it is everywhere else, and a stray ``datetime``
    cannot reach the encoder and 500 the route.
    """
    import json

    from bson import json_util

    return json.loads(json_util.dumps(result))

# Weekly quota for Fernet-authenticated (public API) callers.
MAX_REQUESTS_PER_WEEK = 2000

SYSTEM_SCHEDULER_PREFIX = "system_scheduler_"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _is_valid_system_user(username: str) -> bool:
    """True for the pseudo-users that scheduled tasks run as.

    ``scheduleSystemTasks`` runs jobs as ``system_scheduler_<taskname>``. Such a
    user has no document in ``users``, so it is authorised by checking that a
    matching scheduled task is actually configured.
    """
    if not username or not username.startswith(SYSTEM_SCHEDULER_PREFIX):
        return False

    task_name = username[len(SYSTEM_SCHEDULER_PREFIX) :]
    settings = _mongo().get_record("system", {"name": "active_plugins"})
    plugin_settings = (settings or {}).get("plugins_settings") or {}
    scheduled = (plugin_settings.get("scheduleSystemTasks") or {}).get("schedule_tasks") or []
    return any(task.get("task") == task_name for task in scheduled)


def get_by_username(username: str) -> dict:
    """Return the token-bearing fields of a user.

    Raises ``NotFoundError`` when the user does not exist. The legacy version
    returned a ``({'msg': ...}, 404)`` tuple and callers then probed it with
    ``if 'msg' in current_user`` - which is why an exception is clearer: there
    is no way to accidentally treat the error as a user object.
    """
    user = _mongo().get_record(
        "users",
        {"username": username},
        fields={"token": 1, "adminToken": 1, "requests": 1, "lastRequest": 1, "nodeToken": 1},
    )
    if not user:
        raise NotFoundError(_("User not found"), status_code=404)

    user["_id"] = str(user["_id"])
    user.setdefault("favorites", [])
    return user


def get_user(username: str) -> dict | None:
    """Full user record for authentication, or None.

    Returns None for an unverified account, so an account awaiting verification
    cannot be logged into. The projection omits fields the login path has no use
    for; ``password`` IS included, because verifying it is the point.
    """
    user = _mongo().get_record(
        "users",
        {"username": username},
        fields={"status": 0, "photo": 0, "requests": 0, "lastRequest": 0},
    )
    if not user:
        return None

    # Absent means "verified" - the flag was introduced after accounts already
    # existed, so its absence must not lock those accounts out.
    if user.get("verified") is False:
        return None

    user.setdefault("favorites", [])
    user["_id"] = str(user["_id"])
    return user


def register_user(body: dict) -> tuple[dict, int]:
    """Create a user account.

    Roles and access rights are validated against the configured vocabularies:
    an unrecognised value is rejected rather than stored, because a role that
    does not exist would silently grant nothing and look like a configuration
    that had been applied.
    """
    from archihub.core.roles import verify_access_rights_exist, verify_roles_exist

    mongo = _mongo()

    if mongo.get_record("users", {"username": body.get("username")}, {"username": 1}):
        return {"msg": _("User already exists")}, 400

    try:
        roles = verify_roles_exist(body.get("roles") or [])
        rights = verify_access_rights_exist(body.get("accessRights") or [])
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    password = body.get("password") or ""
    hashed = (
        bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        if password
        else ""
    )

    record = {
        "username": body.get("username"),
        "name": body.get("name"),
        "password": hashed,
        "roles": roles,
        "accessRights": rights,
        "loginType": body.get("loginType", "local"),
        "verified": body.get("verified", True),
        "createdAt": datetime.datetime.now(),
    }
    mongo.insert_record("users", record)
    logger.info("Created account %s", record["username"])
    return {"msg": _("User created successfully")}, 201


def has_role(username: str, role: str) -> bool:
    """Whether ``username`` holds ``role``. Always a real bool - see module docstring."""
    if _is_valid_system_user(username):
        return True

    user = _mongo().get_record("users", {"username": username}, fields={"roles": 1})
    if not user:
        return False
    return role in (user.get("roles") or [])


def has_right(username: str, right: str) -> bool:
    """Whether ``username`` holds ``right``. Always a real bool."""
    if _is_valid_system_user(username):
        return True

    user = _mongo().get_record("users", {"username": username}, fields={"accessRights": 1})
    if not user:
        return False
    return right in (user.get("accessRights") or [])


def _is_date_in_current_week(value: datetime.datetime) -> bool:
    now = datetime.datetime.now()
    return value.isocalendar()[:2] == now.isocalendar()[:2]


def add_request(username: str) -> None:
    """Count one public-API request against the caller's weekly quota.

    Raises :class:`RateLimitError` once the quota is exhausted. That exception
    type matters: the Fernet authenticators hide every other failure behind a
    generic "Invalid or expired token", but this particular message is meant to
    reach the caller so they understand *why* they are being refused. In the
    legacy code that distinction relied on catch ordering plus a bare
    ``str(e)``; here it is carried by the type.
    """
    mongo = _mongo()
    user = mongo.get_record(
        "users", {"username": username}, fields={"requests": 1, "lastRequest": 1}
    )
    if not user:
        raise NotFoundError(_("User not found"), status_code=404)

    last_request = user.get("lastRequest")
    requests = user.get("requests", 0)

    if not isinstance(last_request, datetime.datetime) or not _is_date_in_current_week(last_request):
        # First request, or the first of a new week: reset the counter.
        requests = 1
    elif requests < MAX_REQUESTS_PER_WEEK:
        requests += 1
    else:
        raise RateLimitError(_("You have reached the limit of requests for this week"))

    mongo.update_record(
        "users",
        {"username": username},
        {"requests": requests, "lastRequest": datetime.datetime.now()},
    )


# ---------------------------------------------------------------------------
# Listing and profile
# ---------------------------------------------------------------------------

# Fields a client may filter the user list by. Anything else is dropped before
# the value reaches a query: a client-supplied filter document that is passed
# through unchecked lets the caller express arbitrary query operators.
ALLOWED_USER_FILTER_FIELDS = frozenset({"username", "name"})

# Fields never returned by the listing. Credentials and API keys are the point,
# but `compromise` and `photo` are simply not needed and are large.
_LIST_PROJECTION = {
    "password": 0, "status": 0, "photo": 0, "compromise": 0,
    "token": 0, "adminToken": 0, "nodeToken": 0, "vizToken": 0,
    "requests": 0, "lastRequest": 0, "favorites": 0,
}

PAGE_SIZE = 20


def sanitize_user_filters(filters) -> dict:
    """Reduce a client filter document to allowed string equality only."""
    if not isinstance(filters, dict):
        return {}
    return {
        key: value
        for key, value in filters.items()
        if key in ALLOWED_USER_FILTER_FIELDS and isinstance(value, str)
    }


def get_all(body: dict, current_user: str) -> tuple[list | dict, int]:
    """Paginated user listing, with role/right ids resolved to display terms."""
    try:
        from archihub.core.roles import get_access_rights, get_roles

        mongo = _mongo()
        page = int(body.get("page") or 0)
        filters = sanitize_user_filters(body.get("filters"))

        users = list(
            mongo.get_all_records(
                "users", filters, limit=PAGE_SIZE, skip=page * PAGE_SIZE,
                fields=_LIST_PROJECTION, sort=[("name", 1)],
            )
        )
        total = mongo.count("users", filters)

        rights = {r.get("id"): r.get("term") for r in (get_access_rights().get("options") or [])}
        roles = {r.get("id"): r.get("term") for r in (get_roles().get("options") or [])}

        for user in users:
            user["id"] = str(user.pop("_id"))
            user["total"] = total
            # Unrecognised ids are dropped rather than shown raw - a stale id is
            # not a term and would render as noise.
            user["accessRights"] = [rights[r] for r in (user.get("accessRights") or []) if r in rights]
            user["roles"] = [roles[r] for r in (user.get("roles") or []) if r in roles]

        return users, 200
    except Exception as exc:
        logger.exception("Could not list users")
        return {"msg": str(exc)}, 500


def get_profile(username: str) -> tuple[dict, int]:
    """The caller's own profile, without the password hash.

    The existence check comes before any use of the record. The legacy handler
    called `user.pop('password')` on the line above its own `if not user` check,
    so an absent account raised AttributeError and surfaced as a 500 where 400
    was documented.
    """
    # `requests`/`lastRequest` are projected out, matching the projection the
    # legacy route reaches through `get_user`. The quota the profile screen
    # shows does NOT come from here: `KeysMain.tsx`'s counter calls
    # `UsersService.getRequests()` -> `/users/requests`, and its only read of
    # `requests` off this response is guarded by `if (response.requests)`, so
    # the field has always been absent and nothing renders from it.
    #
    # An earlier revision widened this projection to include them, on the
    # strength of a harness diff that had been misread. It also made the route
    # 500: `lastRequest` is a raw `datetime`, which does not survive JSON
    # encoding. Keep the projection narrow.
    user = _mongo().get_record(
        "users",
        {"username": username},
        fields={"password": 0, "status": 0, "photo": 0, "requests": 0, "lastRequest": 0},
    )
    if not user:
        return {"msg": _("User does not exist")}, 400

    # Projected out above *and* removed here. Relying on the projection alone
    # means one edit to that dict leaks the password hash, and this is the
    # response a user sees; the redundancy costs nothing.
    user.pop("password", None)

    user.setdefault("favorites", [])
    # `verified` is absent on accounts created before the flag existed, and
    # absent means verified - the same reading `get_user` applies.
    user.setdefault("verified", True)
    # `{"$oid": ...}`, not a bare string: the legacy route serialises through
    # json_util and every other endpoint returns the wrapped form.
    return serialise(user), 200


def get_requests(username: str) -> tuple[dict, int]:
    """The caller's weekly public-API quota usage."""
    user = _mongo().get_record("users", {"username": username}, fields={"requests": 1})
    if not user:
        return {"msg": _("User does not exist")}, 400
    return {"requests": user.get("requests", 0), "limit": MAX_REQUESTS_PER_WEEK}, 200


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------
#
# `type` selects the MongoDB collection to read, so it is constrained to a fixed
# allowlist. The check lives here as well as in the request schema because these
# functions are importable and a future caller may not come through the router.

FAVORITE_COLLECTIONS = frozenset({"resources", "records", "snaps"})


def _favorite_collection(type_name: str) -> str:
    if type_name not in FAVORITE_COLLECTIONS:
        raise ValueError(_("Invalid favorite type"))
    return type_name


def set_favorite(username: str, body: dict) -> tuple[dict, int]:
    from bson.objectid import ObjectId

    try:
        collection = _favorite_collection(body.get("type"))
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    try:
        object_id = ObjectId(body.get("id"))
    except Exception:
        return {"msg": _("Resource not found")}, 404

    mongo = _mongo()
    target = mongo.get_record(collection, {"_id": object_id}, {"_id": 1, "status": 1})
    if not target:
        return {"msg": _("Resource not found")}, 404

    # Only published resources may be favourited. `.get` rather than `[...]`:
    # collections other than `resources` have no status field, and the legacy
    # subscript raised KeyError for them.
    if collection == "resources" and target.get("status") != "published":
        return {"msg": _("Resource not published")}, 400

    mongo.update_record_operator(
        "users",
        {"username": username},
        {"$addToSet": {"favorites": {"type": body["type"], "id": body["id"], "view": body.get("view")}}},
    )
    return {"msg": _("Favorite added successfully")}, 200


def delete_favorite(username: str, body: dict) -> tuple[dict, int]:
    try:
        _favorite_collection(body.get("type"))
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    _mongo().update_record_operator(
        "users",
        {"username": username},
        {"$pull": {"favorites": {"type": body["type"], "id": body.get("id")}}},
    )
    return {"msg": _("Favorite removed successfully")}, 200


def get_favorites(username: str, body: dict) -> tuple[dict | list, int]:
    """The caller's favourites of one type, resolved to display fields."""
    from bson.objectid import ObjectId

    try:
        collection = _favorite_collection(body.get("type"))
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    mongo = _mongo()
    page = int(body.get("page") or 0)

    user = mongo.get_record("users", {"username": username}, fields={"favorites": 1})
    favorites = [f for f in ((user or {}).get("favorites") or []) if f.get("type") == body["type"]]
    total = len(favorites)

    object_ids = []
    for favorite in favorites:
        try:
            object_ids.append(ObjectId(favorite.get("id")))
        except Exception:
            continue

    if collection == "resources":
        fields = {"metadata.firstLevel.title": 1}
        sort = [("metadata.firstLevel.title", 1)]
        filters = {"_id": {"$in": object_ids}, "status": "published"}
    else:
        fields = {"name": 1, "displayName": 1}
        sort = [("name", 1)]
        filters = {"_id": {"$in": object_ids}}

    records = list(
        mongo.get_all_records(
            collection, filters, limit=PAGE_SIZE, skip=page * PAGE_SIZE, fields=fields, sort=sort
        )
    )

    by_id = {str(f.get("id")): f for f in favorites}
    resolved = []
    for record in records:
        record_id = str(record.pop("_id"))
        record["id"] = record_id
        record["view"] = (by_id.get(record_id) or {}).get("view")
        resolved.append(record)

    return {"total": total, "resources": resolved}, 200


# ---------------------------------------------------------------------------
# Account lifecycle
# ---------------------------------------------------------------------------

# Roles that constitute "a system role". A user must retain at least one, or
# they would exist with no way to do anything.
SYSTEM_ROLES = frozenset({"admin", "editor", "user"})

# Never returned by a user lookup, in any projection.
_DETAIL_PROJECTION = {
    "password": 0, "status": 0, "photo": 0, "compromise": 0,
    "token": 0, "adminToken": 0, "nodeToken": 0, "vizToken": 0,
}


def get_by_id(user_id: str) -> tuple[dict, int]:
    from bson.objectid import ObjectId

    try:
        object_id = ObjectId(user_id)
    except Exception:
        # A malformed id is a client error, not a server fault. Legacy let
        # InvalidId escape into the 500 handler along with its bson message.
        return {"msg": _("User not found")}, 404

    user = _mongo().get_record("users", {"_id": object_id}, fields=_DETAIL_PROJECTION)
    if not user:
        return {"msg": _("User not found")}, 404

    user["_id"] = str(user["_id"])
    user.setdefault("favorites", [])
    return user, 200


def _user_management_flag(entry_id: str, fallback_index: int) -> bool:
    """Read a self-service toggle from the `user_management` settings document.

    Looked up by id with a positional fallback, so a reordered settings document
    does not silently flip a feature on or off - the legacy code indexed
    `data[0]` and `data[1]` directly.
    """
    record = _mongo().get_record("system", {"name": "user_management"})
    data = (record or {}).get("data") or []

    entry = next((item for item in data if item.get("id") == entry_id), None)
    if entry is None and len(data) > fallback_index:
        entry = data[fallback_index]

    return bool((entry or {}).get("value"))


def self_registration_enabled() -> bool:
    return _user_management_flag("user_registration", 0)


def password_recovery_enabled() -> bool:
    return _user_management_flag("user_password_recovery", 1)


def register_me(body: dict) -> tuple[dict, int]:
    """Self-service registration, when the instance allows it.

    The account is created unverified: roles and access rights are fixed here
    rather than taken from the body, so a self-registering user cannot grant
    themselves anything.
    """
    if not self_registration_enabled():
        return {"msg": _("User registration disabled")}, 400

    payload = {
        "username": body.get("username"),
        "name": body.get("name"),
        "password": body.get("password") or "",
        "roles": [{"id": "user"}],
        "accessRights": [],
        "verified": False,
    }
    return register_user(payload)


def forgot_password(body: dict) -> tuple[dict, int]:
    """Begin password recovery.

    INVARIANT: the response is identical whether or not the account exists, and
    whether or not the mail actually goes out. A distinct error for an unknown
    account - or a 500 when SMTP is down for a real one but a 200 for an invented
    one - turns this endpoint into a way to test which usernames are registered.
    That is why the send is wrapped in its own handler and its failure only
    reaches the log.
    """
    if not password_recovery_enabled():
        return {"msg": _("Password recovery disabled")}, 400

    username = body.get("username")
    user = _mongo().get_record("users", {"username": username}, {"username": 1})

    if user:
        try:
            _send_recovery_email(username)
        except Exception:
            logger.error("Could not send a password recovery message", exc_info=True)

    return {
        "msg": _(
            "If an account exists for this username, a password recovery email has been sent"
        )
    }, 200


def _send_recovery_email(username: str) -> None:
    import os
    from datetime import timedelta

    from cryptography.fernet import Fernet

    from archihub.api.email.services import send_email
    from archihub.api.email.templates import forgot_password_template
    from archihub.core.security import tokens
    from archihub.core.settings import get_settings

    token = tokens.create_access_token(username, expires_delta=timedelta(days=1))
    cipher = Fernet(get_settings().fernet_key).encrypt(token.encode()).decode()

    link = f"{os.environ.get('REDIRECT_URL', '')}/reset-password?token={cipher}"
    send_email(username, _("Password recovery"), forgot_password_template(link))


def update_user(body: dict, current_user: str) -> tuple[dict, int]:
    """Administrative update of another account."""
    from bson.objectid import ObjectId

    from archihub.core.roles import verify_access_rights_exist, verify_roles_exist

    try:
        object_id = ObjectId(body.get("_id"))
    except Exception:
        return {"msg": _("User not found")}, 404

    mongo = _mongo()
    user = mongo.get_record("users", {"_id": object_id}, fields={"username": 1})
    if not user:
        return {"msg": _("User not found")}, 404

    if body.get("username") and user.get("username") != body["username"]:
        return {"msg": _("You cannot change the username")}, 400

    try:
        roles = verify_roles_exist(body.get("roles") or [])
        rights = verify_access_rights_exist(body.get("accessRights") or [])
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    if not SYSTEM_ROLES.intersection(roles):
        return {"msg": _("You must have at least one system role")}, 400

    update: dict = {"roles": roles, "accessRights": rights}
    if body.get("name") is not None:
        update["name"] = body["name"]

    mongo.update_record("users", {"_id": object_id}, update)
    _register_log(current_user, "user_update", {"user": user.get("username")})
    return {"msg": _("User updated successfully")}, 200


def delete_user(body: dict, current_user: str) -> tuple[dict, int]:
    """Delete an account, and revoke everything it could still authenticate with.

    Deleting the user document alone is not enough: API keys live in their own
    collection and a session JWT stays valid until it expires. The keys are
    revoked here; session tokens are covered by BACKEND_FINDINGS S7, which is
    still open.
    """
    username = body.get("username")

    if username == current_user:
        return {"msg": _("You cannot delete yourself")}, 400

    mongo = _mongo()
    if not mongo.get_record("users", {"username": username}, {"username": 1}):
        return {"msg": _("User does not exist")}, 404

    mongo.delete_record("users", {"username": username})

    try:
        from archihub.core.security import api_keys

        api_keys.revoke_all(username)
    except Exception:
        logger.error("Could not revoke API keys for a deleted account", exc_info=True)

    _register_log(current_user, "user_delete", {"user": username})
    return {"msg": _("User deleted successfully")}, 200


def update_me(body: dict, current_user: str) -> tuple[dict, int]:
    """Self-service profile update.

    The current password is required even to change the display name: this
    endpoint can change the password, so a hijacked session must not be able to
    take over the account outright.
    """
    import bcrypt as _bcrypt

    mongo = _mongo()
    user = mongo.get_record("users", {"username": current_user}, fields={"password": 1, "name": 1})
    if not user:
        return {"msg": _("User not found")}, 404

    current_password = body.get("password") or ""
    stored = (user.get("password") or "").encode("utf-8")
    if not stored or not _bcrypt.checkpw(current_password.encode("utf-8"), stored):
        return {"msg": _("Incorrect password")}, 400

    update: dict = {}

    if body.get("name") and body["name"] != user.get("name"):
        update["name"] = body["name"]

    new_password = body.get("new_password") or ""
    if new_password:
        confirmation = body.get("new_password_confirmation")
        # Only enforced when supplied: the confirmation is a UI affordance, and
        # a client that omits it should not silently skip the password change.
        if confirmation is not None and new_password != confirmation:
            return {"msg": _("Passwords do not match")}, 400
        update["password"] = _bcrypt.hashpw(
            new_password.encode("utf-8"), _bcrypt.gensalt()
        ).decode("utf-8")

    if not update:
        return {"msg": _("No changes were made")}, 400

    mongo.update_record("users", {"username": current_user}, update)

    if "password" in update:
        # A password change must not leave previously issued keys usable - that
        # is usually the point of changing it.
        try:
            from archihub.core.security import api_keys

            api_keys.revoke_all(current_user)
        except Exception:
            logger.error("Could not revoke API keys after a password change", exc_info=True)

    return {"msg": _("User updated successfully")}, 200


def accept_compromise(username: str) -> tuple[dict, int]:
    _mongo().update_record("users", {"username": username}, {"compromise": True})
    return {"msg": _("Compromise accepted successfully")}, 200


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
#
# These wrap core/security/api_keys.py. The current password is required to
# issue one: an API key is a long-lived credential, so minting one from a
# hijacked session would be a durable takeover that outlives the session itself.
#
# The returned value is the ONLY copy - the server stores a hash. Callers must
# surface it to the user immediately.


def _verify_current_password(username: str, password: str) -> bool:
    import bcrypt as _bcrypt

    user = _mongo().get_record("users", {"username": username}, fields={"password": 1})
    stored = ((user or {}).get("password") or "").encode("utf-8")
    if not stored:
        return False
    try:
        return _bcrypt.checkpw((password or "").encode("utf-8"), stored)
    except (ValueError, TypeError):
        return False


def issue_api_key(
    username: str,
    password: str,
    scope: str,
    *,
    name: str | None = None,
    expires_in=None,
) -> tuple[dict, int]:
    from archihub.core.security import api_keys

    if not _verify_current_password(username, password):
        return {"msg": _("Incorrect password")}, 400

    try:
        key = api_keys.create_key(
            username,
            scope,
            name=name,
            expires_in=expires_in if expires_in is not None else api_keys.DEFAULT_LIFETIME,
        )
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    _register_log(username, "user_update", {"api_key": {"scope": scope}})
    return {
        "access_token": key,
        # Stated in the payload as well as the docs: this response is the only
        # time the value exists.
        "msg": _("Store this key now - it will not be shown again"),
    }, 200


def list_api_keys(username: str) -> tuple[list, int]:
    from archihub.core.security import api_keys

    return api_keys.list_keys(username), 200


def revoke_api_key(username: str, key_id: str) -> tuple[dict, int]:
    from archihub.core.security import api_keys

    if not api_keys.revoke_key(key_id, username):
        return {"msg": _("API key not found")}, 404
    return {"msg": _("API key revoked")}, 200
