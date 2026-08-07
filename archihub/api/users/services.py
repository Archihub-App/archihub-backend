"""User lookups and authorisation checks.

PARTIAL PORT. Only the helpers the authentication layer needs are here -
``get_by_username``, ``has_role``, ``has_right``, ``add_request``. The rest of
the users domain (CRUD, tokens, profile, favourites) lands in Phase 3 step 2.
Auth has to come first because every other domain's routes depend on it, so
these few functions are pulled forward.

TWO BUGS ARE FIXED HERE RATHER THAN CARRIED FORWARD; both are pre-existing and
both are security-relevant.

1. THERE ARE TWO ``has_role``/``has_right`` IMPLEMENTATIONS IN THE LEGACY TREE.
   ``app/api/users/services.py`` has the correct one, returning a real ``bool``.
   ``app/utils/functions.py`` has a second copy that returns
   ``jsonify({'msg': ...}), 400`` - a **non-empty tuple, which is truthy** - when
   the user does not exist. Every caller writes ``if not has_role(...): deny``,
   so against that copy a nonexistent user silently PASSES the check.

   This is not hypothetical: ``app/api/records/services.py`` imports ``has_role``
   from ``app.utils.functions``, so the entire records domain runs on the broken
   one. (``app/plugins/flowsManager`` imports it too, but that plugin is out of
   scope.) The correct behaviour is implemented once, here.

2. The ``app/utils/functions.py`` copy also omits the ``_is_valid_system_user``
   check, so scheduled-task pseudo-users are denied by whichever routes happen
   to import that copy - an inconsistency between two functions of the same name.

CACHING IS DELIBERATELY NOT WIRED UP YET. The legacy versions carry
``@cacheHandler.cache.cache()``, and correctness depends on 119
``.invalidate()``/``.invalidate_all()`` call sites scattered across the codebase
(``app/utils/functions.py`` alone invalidates 18 in a single function). Caching
permission checks while those invalidation sites are still unported would mean
serving stale authorisation decisions - strictly worse than an uncached lookup.
Caching is restored in Phase 3 step 2, together with its invalidation sites.
"""

from __future__ import annotations

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
