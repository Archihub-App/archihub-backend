"""Who may see which resources.

Extracted from the listing query in ``app/api/resources/services.py``, where it
sat inline among pagination and sorting. It is the access-control boundary for
the archive's main read path, so it is worth being able to read it on its own -
and worth being able to test it without constructing a full listing request.

THE MODEL:

* An administrator sees everything.
* Everyone else sees a resource only if its ``accessRights`` intersect theirs,
  OR the resource declares no access rights at all. The four "no rights" spellings
  below are all present in real data, which is why the check enumerates them
  rather than testing one.
* Deleted resources are visible only to those who may see them.
* Drafts are additionally narrowed to the caller's own, unless they hold the
  privilege to review others' - see :func:`may_see_all_drafts`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# A resource with no access rights is public to authenticated users. All four
# spellings occur in real data - absent, null, empty string, empty list - and a
# check that misses one silently hides content that should be visible.
_NO_RIGHTS_CLAUSES = [
    {"accessRights": None},
    {"accessRights": {"$exists": False}},
    {"accessRights": ""},
    {"accessRights": []},
]


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def user_access_rights(username: str | None) -> list:
    if not username:
        return []
    user = _mongo().get_record("users", {"username": username}, fields={"accessRights": 1})
    return (user or {}).get("accessRights") or []


def access_rights_clause(username: str | None) -> dict:
    """The `$or` a non-admin's queries must satisfy."""
    return {"$or": [{"accessRights": {"$in": user_access_rights(username)}}, *_NO_RIGHTS_CLAUSES]}


def may_see_deleted(username: str | None, is_admin: bool) -> bool:
    """Only administrators may browse the recycle bin."""
    return is_admin


def may_see_all_drafts(is_publisher: bool, is_admin: bool) -> bool:
    """Whether the caller may see drafts other than their own.

    PRESERVED FROM LEGACY, INCLUDING WHAT LOOKS LIKE A TYPO. The original reads:

        if not has_role(user, 'publisher') or not has_role(user, 'admin'):
            restrict to own drafts

    By De Morgan that grants the privilege only to someone who is **both**
    publisher and admin. Every comparable guard in this codebase is written
    ``if not has_role(a) and not has_role(b)`` - i.e. "neither" - so ``or`` here
    is almost certainly a slip, and the intent was that either role suffices.

    It is NOT corrected here, deliberately. The bug fails **closed**: it shows
    people less than intended, never more. Fixing it would widen who can read
    other people's unpublished work, and quietly broadening access is not
    something that should ride along inside a framework migration. It needs an
    explicit decision from someone who knows how these roles are handed out.
    See BACKEND_FINDINGS F18.
    """
    return is_publisher and is_admin


def build_listing_filters(
    base: dict,
    *,
    username: str | None,
    is_admin: bool,
    is_publisher: bool,
    status: str,
) -> tuple[dict, str | None]:
    """Assemble the listing query. Returns ``(filters, error)``.

    ``error`` is non-None when the request should be refused outright, which is
    the case only for an unprivileged caller asking for deleted resources.
    """
    filters = dict(base)

    if status == "deleted" and not may_see_deleted(username, is_admin):
        return filters, "unauthorized"

    filters["status"] = status

    if not is_admin:
        filters.setdefault("$and", []).append(access_rights_clause(username))

    if status == "draft":
        # A "draft" is any of three pre-publication states, so the status test
        # becomes a disjunction and the rest of the filter is repeated into each
        # branch.
        filters.pop("status")
        branches = [
            {"status": state, **filters} for state in ("draft", "created", "updated")
        ]
        if not may_see_all_drafts(is_publisher, is_admin):
            for branch in branches:
                branch["createdBy"] = username
        filters = {"$or": branches}

    return filters, None
