"""Resource hierarchy: ancestry, children, and the navigation tree.

This is the code that decides what the archive *looks like* to someone
navigating it, and the write path's parent validation shares every helper in it.
Keeping both in one module means the rules about where a resource may sit are
stated once, and can be tested without a database.

Four rules hold throughout, each enforced at the function that owns it:

* an ancestry walk terminates even if the stored graph contains a cycle;
* a write may not create such a cycle in the first place;
* a content type's declared ``parentType`` allowlist is binding;
* whether a tree node is drawn as expandable is decided with the same status
  filter that selected the node, so a folder is never drawn as a leaf while it
  holds children the caller can see.
"""

from __future__ import annotations

import logging

from bson.objectid import ObjectId

from archihub.core.errors import PermissionDeniedError, ValidationError
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

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

    **The walk terminates whatever the stored graph looks like.** A cycle - A
    parent of B, B parent of A - would otherwise recurse until the interpreter's
    stack limit, and because a breadcrumb is resolved on nearly every read, a
    single such pair would take the archive down for everyone who touches that
    branch. :func:`validate_parent` is what keeps one from being created; this is
    the second half of the same guarantee, for graphs that predate it or were
    written by another process.

    Two independent guards: a visited set, and a depth ceiling for a cycle long
    enough that the set is not the cheaper catch. Hitting either logs a warning
    and returns what was resolved so far - a partial breadcrumb is a far better
    outcome for the reader than a failed request.
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

    The allowlist is binding wherever a type declares one, and is consulted for
    every parent - including one of a different content type, which is the case
    an allowlist mostly exists to constrain.

    An absent or empty ``parentType`` means **unconstrained**, not "nothing is
    allowed". The distinction is load-bearing: most content types declare no
    ``parentType`` at all, and reading the empty list as a prohibition would stop
    every one of those resources from being saved.
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

    **Refuses any parent that would close a cycle**, which means a resource
    naming one of its own descendants as well as one naming itself. The check
    costs nothing here because the proposed parent's ancestors are already being
    resolved for the closure: if this resource appears among them, the proposed
    parent sits beneath it.
    """
    parent = body.get("parent")
    if not parent:
        body["parent"] = []
        body["parents"] = []
        return body

    if isinstance(parent, dict):
        parent = [parent]

    # A malformed entry means the picker sent something unusable. Treating the
    # whole set as absent keeps a bad payload from half-applying, which would
    # leave the resource in a position nobody chose.
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

    A same-type parent requires the type to be marked hierarchical; a
    different-type parent must appear in the child type's ``parentType``
    allowlist. Both are checked - they constrain different arrangements, and
    neither implies the other.
    """
    from archihub.api.types.services import is_hierarchical

    if not child_type or not parent_type:
        return

    if parent_type == child_type:
        result = is_hierarchical(child_type)
        # `is_hierarchical` answers with a (payload, status) tuple, so an
        # unknown type arrives as a shape rather than as a boolean. A missing
        # type is a validation failure, not an answer about hierarchy.
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

    Order is preserved and each slug appears once, however many of the type's
    view roles the caller happens to hold - this list becomes a ``$in`` clause,
    and repeating a value there is pure waste.
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

    A resource missing its first-level title is malformed, and one of them must
    not cost the reader the rest of the branch: the node is rendered untitled and
    the anomaly is logged, rather than failing the whole tree response.
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

    Resolved for the whole level at once. A tree level holds many nodes drawn
    from a handful of content types, so per-node lookups would repeat the same
    few queries for every row.
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

    One query for the whole level.

    **``status_clause`` is the clause the level itself was fetched with, not the
    raw requested status**, and passing anything else here is the way this
    function goes quietly wrong. A draft-mode level matches drafts *and*
    published resources; probing with "draft" alone would draw a folder holding
    only published children as a leaf, with no way to expand it and nothing to
    indicate the node was mis-drawn.

    The probe deliberately does not also require a descendant to have some
    ancestor of a visible type. The node being probed is itself such an ancestor,
    so the clause can only ever exclude rows whose stored ``parents`` entries are
    missing a ``post_type`` - hiding real children when the data is incomplete,
    which is the wrong way round for a navigation aid.
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
        # Rendered rather than raised: this service returns (payload, status)
        # like the rest of the domain, and the router is what turns that into a
        # response.
        return {"msg": exc.message}, ROLE_FAILURE_STATUS

    # The level is always drawn from every type the caller may see. ``post_type``
    # narrows only the has-children probe below: the caller has already been
    # narrowed to ``slugs``, and constraining the level twice would hide nodes
    # that are legitimately part of it.
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
