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
