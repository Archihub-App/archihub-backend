"""Who may see which resources.

The access-control boundary for the archive's main read path, kept apart from
pagination and sorting so it can be read and tested on its own.

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

from archihub.infra.cache import cached

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


def _holds(required, held) -> bool:
    """Whether rights ``held`` satisfy a stored ``accessRights`` value."""
    if isinstance(required, list):
        return bool(set(required) & set(held))
    return required in held


@cached("resources")
def restricted_ancestors() -> list[dict]:
    """Every resource that declares access rights AND has resources filed under it.

    ``[{"id", "accessRights", "parents": [ancestor ids]}]``. These are the only
    resources whose access rights can be inherited, so they are all a listing
    needs to decide what a descendant inherits. Independent of the caller, which
    is what lets it be cached.
    """
    mongo = _mongo()
    rows = list(
        mongo.get_all_records(
            "resources",
            {"$nor": _NO_RIGHTS_CLAUSES},
            fields={"accessRights": 1, "parents.id": 1},
        )
    )
    if not rows:
        return []

    ids = [str(row["_id"]) for row in rows]
    with_children = set(mongo.distinct("resources", "parents.id", {"parents.id": {"$in": ids}}))

    return [
        {
            "id": str(row["_id"]),
            "accessRights": row.get("accessRights"),
            "parents": [p.get("id") for p in row.get("parents") or [] if isinstance(p, dict) and p.get("id")],
        }
        for row in rows
        if str(row["_id"]) in with_children
    ]


def _inherited_exclusions(held: list) -> list[dict]:
    """Clauses matching the resources whose INHERITED access right the caller lacks.

    A resource without rights of its own is governed by its nearest ancestor that
    has some. For each ancestor the caller may not see, that is every descendant
    without rights of its own, except those with a rights-bearing ancestor
    nearer to them - which is exactly a rights-bearing descendant of that
    ancestor. ``parents`` holds the full ancestry, so both tests are plain
    membership tests and nesting in any order is decided correctly.
    """
    nodes = restricted_ancestors()
    exclusions = []
    for node in nodes:
        if _holds(node["accessRights"], held):
            continue
        nearer = [other["id"] for other in nodes if node["id"] in other["parents"]]
        exclusions.append(
            {
                "$and": [
                    {"$or": _NO_RIGHTS_CLAUSES},
                    {"parents.id": node["id"]},
                    {"parents.id": {"$nin": nearer}},
                ]
            }
        )
    return exclusions


def access_rights_clause(username: str | None) -> dict:
    """The clause a non-admin's queries must satisfy.

    A resource is visible when its effective access right - its own, or else the
    one it inherits from its nearest rights-bearing ancestor - is absent or held
    by the caller. The same rule as :func:`may_view_resource`, stated as a query.
    """
    held = user_access_rights(username)
    own = {"$or": [{"accessRights": {"$in": held}}, *_NO_RIGHTS_CLAUSES]}
    exclusions = _inherited_exclusions(held)
    if not exclusions:
        return own
    return {"$and": [own, {"$nor": exclusions}]}


def metadata_open_to_all() -> bool:
    """Whether the administrator has opened every resource's metadata to all users.

    The ``metadata_access`` entry of the ``access_rights`` settings. An instance
    whose settings predate the entry reads as closed: the setting widens what
    people see, so its absence must not.
    """
    from archihub.api.system.services import get_setting_value

    return get_setting_value("access_rights", "metadata_access") is True


def navigation_clause(
    username: str | None, is_admin: bool, *, for_filing: bool = False
) -> dict | None:
    """The access clause the navigation tree applies, or ``None`` for no restriction.

    While metadata is not open to all users, the tree shows a caller only the
    resources whose effective access rights they hold, the same rule the listing
    applies; an anonymous caller holds none. Administrators see everything.

    ``for_filing`` applies the rule regardless of the setting: the tree is then
    a parent picker, and a parent the caller may not see is one they cannot use.
    """
    if is_admin:
        return None
    if not for_filing and metadata_open_to_all():
        return None
    return access_rights_clause(username)


def effective_access_right(resource: dict) -> str | None:
    """The access right that actually governs a resource.

    ACCESS RIGHTS ARE INHERITED. A resource that declares none is governed by
    the nearest ancestor that does - so restricting a fonds restricts everything
    filed under it, which is how archival access conditions are normally
    expressed. Missing this is what makes the difference between "this series is
    reserved" and "this series is reserved, but every item in it is public".

    A resource with no ``parents`` key, or an ancestor with no ``accessRights``
    key, is handled; both occur in real documents. The stored ``parents`` order
    decides which ancestor wins - :func:`hierarchy.ancestors` sorts it
    nearest-first, so the nearest ancestor's condition is the one that applies.
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

    # The field is declared a single id, but list-valued documents exist in real
    # data - which is why the "no rights" clauses above have to enumerate the
    # empty list too. A list means any one of them is sufficient.
    return _holds(required, user_access_rights(username))


def holds_edit_role(username: str, post_type: str | None, is_admin: bool) -> bool:
    """Whether the content type's ``editRoles`` admit this caller.

    A type declaring none is unconstrained by this check - which is why it can
    never be the *only* check on a write path. for what
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

    ``createdBy`` is read with ``.get``: documents predating the field exist.
    """
    from archihub.api.users.services import has_role

    if is_admin:
        return True
    if resource.get("createdBy") == username:
        return True
    return has_role(username, "super_editor")


def is_public(resource: dict) -> bool:
    """Whether an anonymous caller may see this resource.

    THREE CONDITIONS, and they mirror the authenticated rule with the caller's
    rights fixed at "none":

    * it is **published** - a draft is work in progress and the recycle bin is
      not a public archive;
    * its **effective** access right is absent, which is inherited, so an item
      filed under a reserved fonds is not public even if it declares nothing
      itself;
    * its content type declares no ``viewRoles`` - a type restricted to some
      role cannot be visible to someone holding none.

    Stated once here rather than in each public service, so every public route
    applies the same rule.
    """
    if resource.get("status") != "published":
        return False
    if effective_access_right(resource):
        return False

    post_type = resource.get("post_type")
    if not post_type:
        return True

    from archihub.api.resources.hierarchy import type_roles

    return not type_roles(post_type).get("viewRoles")


def may_see_deleted(username: str | None, is_admin: bool) -> bool:
    """Only administrators may browse the recycle bin."""
    return is_admin


def may_see_all_drafts(is_publisher: bool, is_admin: bool) -> bool:
    """Whether the caller may see drafts other than their own.

    ONLY SOMEONE WHO IS BOTH publisher AND admin sees everyone's drafts; anyone
    else sees their own. This is stricter than "either role", deliberately: it
    fails closed, and widening who can read other people's unpublished work is a
    decision for whoever hands out these roles.
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
