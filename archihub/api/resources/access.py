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


def effective_access_right(resource: dict) -> str | None:
    """The access right that actually governs a resource.

    ACCESS RIGHTS ARE INHERITED. A resource that declares none is governed by
    the nearest ancestor that does - so restricting a fonds restricts everything
    filed under it, which is how archival access conditions are normally
    expressed. Missing this is what makes the difference between "this series is
    reserved" and "this series is reserved, but every item in it is public".

    Two changes from the original ``get_accessRights``:

    * it raised for a resource with no ``parents`` key and again for an ancestor
      with no ``accessRights`` key, both of which occur in real documents;
    * it resolved ancestors with a single ``$in`` query and took whichever came
      back first, so which right won was down to Mongo's storage order. Here the
      stored ``parents`` order decides, which :func:`hierarchy.ancestors` sorts
      nearest-first - the nearest ancestor's condition is the one that applies.
    """
    own = resource.get("accessRights")
    if own:
        return own

    parents = resource.get("parents") or []
    parent_ids = [p.get("id") for p in parents if isinstance(p, dict) and p.get("id")]
    if not parent_ids:
        return None

    from bson.objectid import ObjectId

    object_ids = []
    for parent_id in parent_ids:
        try:
            object_ids.append(ObjectId(parent_id))
        except Exception:
            logger.warning("Resource lists an unusable ancestor id %r", parent_id)

    if not object_ids:
        return None

    rows = _mongo().get_all_records(
        "resources", {"_id": {"$in": object_ids}}, fields={"accessRights": 1}
    )
    rights = {str(row["_id"]): row.get("accessRights") for row in rows}

    for parent_id in parent_ids:
        if rights.get(parent_id):
            return rights[parent_id]

    return None


def may_view_resource(username: str, resource: dict, is_admin: bool) -> bool:
    """Whether this caller may open this resource.

    Administrators always may. Everyone else must hold the governing access
    right, if there is one.
    """
    if is_admin:
        return True

    required = effective_access_right(resource)
    if not required:
        return True

    held = user_access_rights(username)
    # The field is declared a single id, but list-valued documents exist in real
    # data - which is why the "no rights" clauses above have to enumerate the
    # empty list too. A list means any one of them is sufficient.
    if isinstance(required, list):
        return bool(set(required) & set(held))
    return required in held


def holds_edit_role(username: str, post_type: str | None, is_admin: bool) -> bool:
    """Whether the content type's ``editRoles`` admit this caller.

    A type declaring none is unconstrained by this check - which is why it can
    never be the *only* check on a write path. See BACKEND_FINDINGS S17 for what
    happened where it was.
    """
    from archihub.api.resources.hierarchy import type_roles
    from archihub.api.users.services import has_role

    if is_admin:
        return True

    edit_roles = type_roles(post_type or "")["editRoles"]
    if not edit_roles:
        return True

    return any(has_role(username, role) for role in edit_roles)


def owns_or_supervises(username: str, resource: dict, is_admin: bool) -> bool:
    """The ownership half of the write rule: creator, ``super_editor``, or admin.

    ``createdBy`` is read with ``.get`` - documents predating the field exist,
    and the original's direct subscript turned one of those into a 500 on every
    attempt to edit it.
    """
    from archihub.api.users.services import has_role

    if is_admin:
        return True
    if resource.get("createdBy") == username:
        return True
    return has_role(username, "super_editor")


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
