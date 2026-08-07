"""Resource hierarchy: ancestry, children, and the navigation tree.

Extracted from ``app/api/resources/services.py``, where the tree logic was
spread across five cache-decorated functions (``get_children``,
``get_children_cache``, ``get_tree``, ``get_parents``, ``get_parent``,
``has_parent_postType``, ``validate_parent``) interleaved with the rest of a
2,714-line module.

It is separated for the same reason ``access.py`` was: this is the code that
decides what the archive *looks like* to a user navigating it, and the write
path's parent validation shares every one of these helpers. Both are easier to
reason about - and to test without a database - on their own.

FOUR DEFECTS ARE CORRECTED HERE rather than carried forward; each is called out
at the function that fixes it, and all four are recorded in BACKEND_FINDINGS:

* ancestry recursion had no cycle guard (S15),
* nothing prevented a caller from *creating* such a cycle (S15),
* the parent-content-type allowlist was never actually enforced (F20),
* the tree's has-children probe used a different status filter from the tree
  level itself, so in draft view a folder holding only published children
  rendered as a leaf (F21).
"""

from __future__ import annotations

import logging

from bson.objectid import ObjectId

from archihub.core.errors import PermissionDeniedError, ValidationError
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "resources"
TYPES_COLLECTION = "post_types"

TREE_PAGE_SIZE = 10

# Depth ceiling for ancestry walks. Real archives nest a handful of levels; this
# is not a modelling limit, it is the backstop that keeps a malformed graph from
# exhausting the stack. See :func:`ancestors`.
MAX_ANCESTRY_DEPTH = 64

#: The status values a tree request may ask for.
TREE_STATUSES = ("published", "draft", "deleted")


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _to_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ancestry
# ---------------------------------------------------------------------------


def direct_parents(resource_id: str) -> list[dict]:
    """The resource's immediate parents, always as a list.

    ``parent`` is stored as a dict on older documents and a list on newer ones;
    both spellings are present in real data, so both are normalised here rather
    than at each of the seven call sites.
    """
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return []

    resource = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"parent": 1})
    if not resource:
        return []

    parent = resource.get("parent")
    if not parent:
        return []
    if isinstance(parent, dict):
        return [parent]
    if isinstance(parent, list):
        return list(parent)
    return []


def ancestors(resource_id: str, level: int = 1, _seen: frozenset[str] | None = None) -> list[dict]:
    """Every ancestor of a resource, nearest first, de-duplicated.

    CYCLE GUARD (BACKEND_FINDINGS S15). The original recursed on each parent
    with no record of where it had already been. A resource graph containing a
    cycle - A parent of B, B parent of A - therefore recursed until the
    interpreter's stack limit and raised ``RecursionError``.

    That is not hypothetical: :func:`validate_parent` below is what admits new
    parent links, and the original only refused a resource that named *itself*.
    Naming a resource that is already a descendant was accepted, which is
    exactly how such a pair gets created. Once it exists, every read that
    resolves a breadcrumb fails permanently, for everyone.

    Guarded two ways - a visited set, and a depth ceiling for the case where the
    cycle is longer than the set catches cheaply. Hitting either logs a warning
    and returns what was resolved so far, because a partial breadcrumb is a far
    better outcome for the reader than a 500.
    """
    seen = _seen if _seen is not None else frozenset()

    if resource_id in seen:
        logger.warning("Cycle in resource ancestry at %s; stopping the walk", resource_id)
        return []
    if level > MAX_ANCESTRY_DEPTH:
        logger.warning(
            "Resource ancestry deeper than %s levels at %s; stopping the walk",
            MAX_ANCESTRY_DEPTH,
            resource_id,
        )
        return []

    parents = direct_parents(resource_id)
    if not parents:
        return []

    seen = seen | {resource_id}

    resolved: list[dict] = []
    for parent in parents:
        parent_id = parent.get("id")
        if not parent_id:
            continue
        if parent_id in seen:
            # Already on the path from the origin, so following it would close a
            # loop. Dropping the edge - rather than recording the ancestor and
            # then refusing to descend - keeps a resource from being listed as
            # its own ancestor in the breadcrumb.
            logger.warning("Cycle in resource ancestry: %s -> %s", resource_id, parent_id)
            continue

        entry = dict(parent)
        entry["level"] = level
        entry["parentOf"] = [resource_id]
        resolved.append(entry)
        resolved.extend(ancestors(parent_id, level + 1, seen))

    # Nearest first. Depth-first recursion alone does not give that: where two
    # parents share a grandparent, the grandparent would land between them.
    return sorted(_dedupe_by_id(resolved), key=lambda entry: entry["level"])


def _dedupe_by_id(entries: list[dict]) -> list[dict]:
    """First occurrence of each id wins; ``parentOf`` lists are merged into it.

    Nearest-first ordering means the first occurrence carries the smallest
    ``level``, which is the one the breadcrumb wants.
    """
    unique: list[dict] = []
    by_id: dict[str, dict] = {}

    for entry in entries:
        entry_id = entry.get("id")
        if entry_id is None:
            continue

        existing = by_id.get(entry_id)
        if existing is None:
            by_id[entry_id] = entry
            unique.append(entry)
            continue

        merged = set(_flatten(existing.get("parentOf") or [])) | set(
            _flatten(entry.get("parentOf") or [])
        )
        existing["parentOf"] = sorted(merged)

    return unique


def _flatten(items):
    """``parentOf`` is occasionally a list of lists in stored data."""
    for item in items:
        if isinstance(item, list):
            yield from _flatten(item)
        else:
            yield item


def has_changed_parent(resource_id: str, new_parent) -> bool:
    """Whether an update moves the resource somewhere else in the tree.

    Only the *set of ids* matters - a reordered or re-annotated parent list is
    not a move, and treating it as one would trigger a full descendant rewrite
    on every save.
    """
    current = direct_parents(resource_id)
    proposed = new_parent or []
    if isinstance(proposed, dict):
        proposed = [proposed]

    if not current and not proposed:
        return False
    if not current or not proposed:
        return True

    return {p.get("id") for p in current} != {p.get("id") for p in proposed}


def direct_children(resource_id: str) -> list[dict]:
    """Immediate children, whatever their type or status."""
    rows = _mongo().get_all_records(
        COLLECTION, {"parent.id": resource_id}, fields={"_id": 1, "post_type": 1, "parent": 1}
    )
    return [
        {"id": str(row["_id"]), "post_type": row.get("post_type"), "parent": row.get("parent")}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Parent validation
# ---------------------------------------------------------------------------


def parent_type_allowed(child_type: str, parent_type: str) -> bool:
    """Whether ``child_type`` declares ``parent_type`` as an acceptable parent.

    BEHAVIOUR CHANGE, DELIBERATE (BACKEND_FINDINGS F20). The legacy equivalent
    could never refuse anything, for three independent reasons: it was reached
    only from a branch whose own condition made it unreachable; it returned a
    ``(dict, 404)`` tuple - which is truthy - when the type did not exist; and
    it was consulted only when parent and child shared a content type, so a
    parent of some *other*, undeclared type was never examined at all. The
    ``parentType`` allowlist that content types have always been able to declare
    was, in practice, decorative.

    It is enforced here - but only where a type has actually declared one. An
    absent or empty ``parentType`` means "unconstrained", not "nothing is
    allowed". That distinction matters: existing instances have types with no
    ``parentType`` at all, and reading the empty list as a prohibition would
    make every save of those resources start failing at once.
    """
    record = _mongo().get_record(TYPES_COLLECTION, {"slug": child_type}, {"parentType": 1})
    if not record:
        raise ValidationError(_("Post type does not exist"))

    allowed = record.get("parentType") or []
    if not allowed:
        return True

    for entry in allowed:
        if entry.get("id") == parent_type:
            return True
        # A hierarchical entry accepts any descendant of that type, not just a
        # direct instance of it.
        if entry.get("hierarchical"):
            return True

    return False


def validate_parent(body: dict, update: bool = False) -> dict:
    """Normalise and check a resource's declared parents.

    Fills in two fields the rest of the domain relies on:

    * ``parent``  - the direct parents, with any that are merely ancestors of
      another named parent removed (naming both a parent and its grandparent is
      how the frontend's picker can leave things, and keeping both would make
      the resource appear twice in its own breadcrumb);
    * ``parents`` - the full transitive closure, which is what the listing and
      the search index filter on.

    CYCLE PREVENTION (BACKEND_FINDINGS S15). The original refused only a
    resource naming *itself*. It did not refuse a resource naming one of its own
    descendants, which produces exactly the cycle that made ancestry resolution
    recurse forever. Detecting it costs nothing here: the proposed parent's
    ancestors are already being resolved, and if this resource appears among
    them then the proposed parent is beneath it.
    """
    parent = body.get("parent")
    if not parent:
        body["parent"] = []
        body["parents"] = []
        return body

    if isinstance(parent, dict):
        parent = [parent]

    # A malformed entry means the picker sent something unusable; treating the
    # whole set as absent matches the original and keeps a bad payload from
    # half-applying.
    if any("id" not in p for p in parent):
        body["parent"] = []
        body["parents"] = []
        return body

    resource_id = str(body.get("_id") or "")

    all_ancestors: list[dict] = []
    for p in parent:
        if update and p["id"] == resource_id:
            raise ValidationError(_("The resource cannot have itself as parent"))
        all_ancestors.extend(ancestors(p["id"]))

    if update and resource_id:
        ancestor_ids = {a.get("id") for a in all_ancestors}
        if resource_id in ancestor_ids:
            raise ValidationError(_("The resource cannot have one of its descendants as parent"))

    # Drop any named parent that is already an ancestor of another named parent.
    redundant = {a.get("id") for a in all_ancestors}
    direct = [p for p in parent if p["id"] not in redundant]

    closure: list[dict] = []
    for p in direct:
        entry = dict(p)
        if "post_type" not in entry:
            entry["post_type"] = _post_type_of(entry["id"])

        _check_parent_is_acceptable(body.get("post_type"), entry["post_type"])

        closure.append(entry)
        closure.extend(ancestors(entry["id"]))

    body["parents"] = _dedupe_by_id(closure)
    body["parent"] = direct
    return body


def _post_type_of(resource_id: str) -> str:
    object_id = _to_object_id(resource_id)
    record = (
        _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"post_type": 1})
        if object_id is not None
        else None
    )
    if not record:
        raise ValidationError(_("The parent resource does not exist"))
    return record.get("post_type")


def _check_parent_is_acceptable(child_type: str | None, parent_type: str | None) -> None:
    """Both halves of the content-model rule, in one place.

    A same-type parent additionally requires the type to be marked hierarchical;
    a different-type parent must appear in the child type's ``parentType``
    allowlist. The original only ever evaluated the first of these.
    """
    from archihub.api.types.services import is_hierarchical

    if not child_type or not parent_type:
        return

    if parent_type == child_type:
        result = is_hierarchical(child_type)
        # is_hierarchical answers with a (payload, status) tuple for an unknown
        # type; a missing type is a validation failure, not a hierarchy answer.
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValidationError(_("Post type does not exist"))
        hierarchical, _has_parents = result
        if not isinstance(hierarchical, bool) or not hierarchical:
            raise ValidationError(_("The resource isn't hierarchical"))
        return

    if not parent_type_allowed(child_type, parent_type):
        raise ValidationError(
            _("The resource post type is not allowed to have a parent of this type")
        )


# ---------------------------------------------------------------------------
# The navigation tree
# ---------------------------------------------------------------------------


def type_roles(slug: str) -> dict:
    """``editRoles``/``viewRoles`` for a content type, empty lists when unset."""
    record = _mongo().get_record(TYPES_COLLECTION, {"slug": slug}, {"editRoles": 1, "viewRoles": 1})
    if not record:
        return {"editRoles": [], "viewRoles": []}
    return {
        "editRoles": record.get("editRoles") or [],
        "viewRoles": record.get("viewRoles") or [],
    }


def visible_type_slugs(username: str, slugs: list[str]) -> list[str]:
    """Narrow a list of content types to those this caller may see.

    A type with no ``viewRoles`` is visible to everyone; otherwise the caller
    must hold one of them, or be an administrator.

    Order is preserved and duplicates are removed. The original appended the
    slug once *per matching role*, so a user holding three of a type's view
    roles put that type into the query three times.
    """
    from archihub.api.users.services import has_role

    is_admin = has_role(username, "admin")

    visible: list[str] = []
    for slug in slugs:
        if slug in visible:
            continue
        roles = type_roles(slug)["viewRoles"]
        if not roles or is_admin or any(has_role(username, role) for role in roles):
            visible.append(slug)

    return visible


def _status_filter(status: str, username: str, is_admin: bool):
    """The Mongo clause for a requested tree status.

    ``draft`` deliberately includes published resources: a draft-mode tree is
    for finding where a draft belongs, which means seeing the published
    structure around it.
    """
    if status == "deleted":
        if not is_admin:
            raise PermissionDeniedError(_("You don't have the required authorization"))
        return "deleted"
    if status == "draft":
        return {"$in": ["draft", "published"]}
    return "published"


def _node_title(resource: dict) -> str:
    """The label shown in the tree.

    A resource whose first-level metadata is missing its title is malformed, but
    the original subscripted straight through it - so one such row anywhere in a
    branch raised ``KeyError`` and took the entire tree response down with it.
    """
    metadata = resource.get("metadata")
    if isinstance(metadata, dict):
        first_level = metadata.get("firstLevel")
        if isinstance(first_level, dict):
            title = first_level.get("title")
            if title:
                return title

    logger.warning("Resource %s has no firstLevel.title; rendering it untitled", resource.get("_id"))
    return _("Untitled")


def _type_display(slugs: set[str]) -> dict[str, dict]:
    """Name and icon for each content type, in one query.

    The original resolved these per node, calling ``get_icon`` and
    ``get_by_slug`` inside the loop - two additional round trips for every row
    of every tree level, all of them repeating the same handful of lookups.
    """
    if not slugs:
        return {}

    rows = _mongo().get_all_records(
        TYPES_COLLECTION,
        {"slug": {"$in": sorted(slugs)}},
        fields={"slug": 1, "name": 1, "icon": 1},
    )
    return {
        row["slug"]: {"name": row.get("name"), "icon": row.get("icon")}
        for row in rows
        if row.get("slug")
    }


def _ids_with_children(ids: list[str], slugs: list[str], status_clause) -> set[str]:
    """Which of ``ids`` have at least one descendant the caller could navigate to.

    One query for the whole level, replacing the original's one query per node.

    F21: the status used here is the *same* clause the level itself was fetched
    with. The original passed the raw requested status instead, so in draft mode
    the level query matched drafts and published resources while this probe
    matched drafts only - and a folder containing nothing but published children
    was drawn as a leaf, with no way to expand it.

    One clause of the original is not reproduced: it also required a descendant
    to have *some* ancestor whose ``post_type`` was in the visible set. Because
    the node being probed is itself such an ancestor, that was satisfied for any
    well-formed document and only ever excluded rows whose stored ``parents``
    entries were missing their ``post_type`` - i.e. it hid real children when
    the data was incomplete, which is the wrong way round for a navigation aid.
    """
    if not ids or not slugs:
        return set()

    rows = _mongo().get_all_records(
        COLLECTION,
        {
            "post_type": {"$in": slugs},
            "parents.id": {"$in": ids},
            "status": status_clause,
        },
        fields={"parents.id": 1},
    )

    wanted = set(ids)
    found: set[str] = set()
    for row in rows:
        for parent in row.get("parents") or []:
            parent_id = parent.get("id")
            if parent_id in wanted:
                found.add(parent_id)

    return found


def get_tree(
    root: str,
    slugs: list[str],
    user: str,
    post_type: str | None = None,
    page: int | None = None,
    status: str = "published",
) -> tuple[list | dict, int]:
    """One level of the navigation tree.

    ``root`` is a resource id, or ``'all'`` for the top level. ``slugs`` are the
    content types the caller has already been narrowed to by
    :func:`visible_type_slugs`; an empty list means there is nothing this caller
    may see, which is an empty level rather than an error.
    """
    from archihub.api.users.services import has_role

    if status not in TREE_STATUSES:
        return {"msg": _("Unknown status")}, 400

    if not slugs:
        return [], 200

    try:
        status_clause = _status_filter(status, user, has_role(user, "admin"))
    except PermissionDeniedError as exc:
        # Rendered here rather than raised, because this service returns
        # (payload, status) like the rest of the domain - and rendered with the
        # legacy status rather than the exception's own 403, because
        # `upgrade_front` compares this code exactly. Grep
        # LEGACY_ROLE_FAILURE_STATUS for every site awaiting that flip.
        return {"msg": exc.message}, LEGACY_ROLE_FAILURE_STATUS

    # The level itself is always drawn from every type the caller may see;
    # ``post_type`` narrows only the has-children probe below, which is what the
    # original did too - the caller has already narrowed ``slugs`` for it.
    if root == "all":
        filters = {
            "post_type": {"$in": slugs},
            "parent": {"$in": [None, []]},
            "status": status_clause,
        }
    else:
        filters = {
            "post_type": {"$in": slugs},
            "parent.id": root,
            "status": status_clause,
        }

    limit = TREE_PAGE_SIZE if page is not None else 0
    skip = (page or 0) * TREE_PAGE_SIZE if page is not None else 0

    rows = list(
        _mongo().get_all_records(
            COLLECTION,
            filters,
            sort=[("metadata.firstLevel.title", 1)],
            fields={"metadata.firstLevel.title": 1, "post_type": 1, "parent": 1},
            limit=limit,
            skip=skip,
        )
    )

    nodes = [
        {"name": _node_title(row), "post_type": row.get("post_type"), "id": str(row["_id"])}
        for row in rows
    ]

    display = _type_display({n["post_type"] for n in nodes if n["post_type"]})
    child_types = [post_type] if post_type else slugs
    with_children = _ids_with_children([n["id"] for n in nodes], child_types, status_clause)

    for node in nodes:
        info = display.get(node["post_type"], {})
        node["children"] = node["id"] in with_children
        node["icon"] = info.get("icon")
        node["type"] = info.get("name")

    return nodes, 200
