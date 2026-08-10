"""Catalogued resources.

Port of ``app/api/resources/services.py`` (2,714 lines) - the largest domain in
the application. Being ported in slices; this one covers the READ path:

* ``get_all``  - the paginated catalogue listing behind ``POST /resources/getall``
* ``get_by_id`` - a single resource
* the cross-domain helpers other modules import (``get_resource_type``,
  ``get_access_rights_of``)

The write path (create/update/delete/restore, the article editor, file ordering,
the tree, and the 11 hook call sites) follows in later slices.

ACCESS CONTROL LIVES IN ``access.py``, not here. In the original it was inline in
the middle of this listing query, interleaved with pagination and sorting, which
is how the draft-visibility defect (BACKEND_FINDINGS F18) survived unnoticed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from bson import json_util
from bson.objectid import ObjectId

from archihub.api.resources import access, presentation
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "resources"
PAGE_SIZE = 20

# Columns the listing never treats as displayable metadata: they are either
# structural or rendered separately.
_NON_METADATA_COLUMNS = {"", "createdAt", "ident", "files", "accessRights"}


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    return json.loads(json_util.dumps(result))


def _to_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _isoformat_in_place(document: dict, field_path: str) -> None:
    """Convert a dotted-path datetime to ISO, in place.

    Metadata fields are nested arbitrarily deep, and a raw ``datetime`` is not
    JSON-serialisable.
    """
    keys = field_path.split(".")
    current = document
    for key in keys[:-1]:
        if not isinstance(current, dict) or key not in current:
            return
        current = current[key]

    last = keys[-1]
    if isinstance(current, dict) and isinstance(current.get(last), datetime):
        current[last] = current[last].isoformat()


# ---------------------------------------------------------------------------
# Cross-domain helpers
# ---------------------------------------------------------------------------


def get_resource_type(resource_id: str) -> str | None:
    """The content type of one resource. Used by usertasks and the indexers."""
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return None

    resource = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"post_type": 1})
    if not resource:
        # Legacy raised here. A missing resource is an ordinary outcome for a
        # caller enriching a list, and raising turned one stale reference into a
        # failed page.
        logger.info("No resource %s when resolving its content type", resource_id)
        return None
    return resource.get("post_type")


def get_access_rights_of(resource_id: str):
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return None

    resource = _mongo().get_record(
        COLLECTION, {"_id": object_id}, fields={"accessRights": 1, "parents": 1}
    )
    return (resource or {}).get("accessRights")


def can_view_type(username: str, post_type: str) -> bool:
    """Whether the caller may see a content type at all.

    A type may declare ``viewRoles``; holding any one of them - or being an
    administrator - is sufficient. A type with no viewRoles is visible to
    everyone.
    """
    from archihub.api.users.services import has_role

    record = _mongo().get_record("post_types", {"slug": post_type}, {"viewRoles": 1})
    view_roles = (record or {}).get("viewRoles") or []
    if not view_roles:
        return True

    return has_role(username, "admin") or any(has_role(username, role) for role in view_roles)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _metadata_field_types(post_types: list[str], active_columns: list[str]) -> list[dict]:
    """Resolve the declared type of each requested column, across the types asked for."""
    from archihub.api.types.services import get_metadata

    seen: dict[str, str] = {}
    for post_type in post_types:
        try:
            metadata = get_metadata(post_type)
        except Exception:
            logger.warning("Could not resolve metadata for %s", post_type, exc_info=True)
            continue

        for field in (metadata or {}).get("fields", []) or []:
            destiny = field.get("destiny")
            if destiny in active_columns and destiny not in seen:
                seen[destiny] = field.get("type")

    return [{"destiny": d, "type": t} for d, t in seen.items()]


def get_all(body: dict, user: str) -> tuple[dict, int]:
    """The paginated catalogue listing."""
    try:
        from archihub.api.users.services import has_role

        post_types = body.get("post_type") or []
        if isinstance(post_types, str):
            post_types = [post_types]

        for post_type in post_types:
            if not can_view_type(user, post_type):
                return {"msg": _("You don't have the required authorization")}, 401

        active_columns = [c for c in (body.get("activeColumns") or []) if c not in _NON_METADATA_COLUMNS]
        metadata_fields = _metadata_field_types(post_types, active_columns)

        base: dict = {"post_type": {"$in": post_types}}
        if body.get("parents"):
            base["parents.id"] = body["parents"].get("id")
        if body.get("files"):
            base["filesObj"] = {"$exists": True, "$ne": []}

        filters, error = access.build_listing_filters(
            base,
            username=user,
            is_admin=has_role(user, "admin"),
            is_publisher=has_role(user, "publisher"),
            status=body.get("status") or "published",
        )
        if error:
            return {"msg": _("You don't have the required authorization")}, 401

        # `metadata.firstLevel.title` is in the base projection because the
        # listing renders a title for every row whether or not the caller asked
        # for a column. Dropping it produced a browse screen of untitled rows -
        # caught by the diff harness against the legacy backend, not by any
        # test, because no test asserted on a field nobody had thought to name.
        fields = {
            "accessRights": 1, "filesObj": 1, "ident": 1, "post_type": 1,
            "createdAt": 1, "metadata.firstLevel.title": 1,
        }
        for column in active_columns:
            fields[column] = 1
            declared = next((f for f in metadata_fields if f["destiny"] == column), None)
            # A non-text column is only meaningful when present, so require it -
            # otherwise the listing shows rows with an empty cell for the very
            # column the caller asked to sort or filter by.
            if declared and declared.get("type") != "text":
                filters[column] = {"$exists": True, "$ne": None}

        page = int(body.get("page") or 0)
        sort_by = body.get("sortBy") or "createdAt"
        direction = 1 if (body.get("sortOrder") or "asc") == "asc" else -1

        mongo = _mongo()
        resources = list(
            mongo.get_all_records(
                COLLECTION, filters, limit=PAGE_SIZE, skip=page * PAGE_SIZE,
                fields=fields, sort=[(sort_by, direction)],
            )
        )
        total = mongo.count(COLLECTION, filters)

        for resource in resources:
            resource["id"] = str(resource.pop("_id"))

            if "filesObj" in resource:
                # The listing shows a count, not the file records themselves.
                resource["files"] = len(resource.pop("filesObj") or [])

            resource["accessRights"] = _access_right_term(resource.get("accessRights"))

            for field_path in fields:
                _isoformat_in_place(resource, field_path)

        return {"total": total, "resources": parse_result(resources)}, 200
    except Exception as exc:
        logger.exception("Could not list resources")
        return {"msg": str(exc)}, 500


def _access_right_term(access_right):
    """Resolve an access-right id to its display term, or None."""
    if not access_right:
        return None
    try:
        from archihub.api.lists.services import get_option_by_id

        option = get_option_by_id(access_right)
        return option.get("term") if isinstance(option, dict) else None
    except Exception:
        logger.debug("Could not resolve access right %r", access_right, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_by_id(resource_id: str, user: str) -> tuple[dict, int]:
    """One resource, if the caller may see it.

    THREE GATES, all of which the original applied and all of which must stay:
    the recycle bin is administrators-only, the governing access right must be
    held (and it is **inherited from ancestors** - see
    ``access.effective_access_right``), and the content type may restrict
    viewing by role.

    The checks are applied to the fetched document rather than folded into the
    query, so "does not exist" and "exists but you may not see it" produce the
    same 404. The original answered 401 for the second case, which confirmed the
    resource was there; ``upgrade_front`` compares this response against 200
    only, so the change is invisible to it.
    """
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return {"msg": _("Resource does not exist")}, 404

    try:
        from archihub.api.users.services import has_role

        resource = _mongo().get_record(COLLECTION, {"_id": object_id})
        if not resource:
            return {"msg": _("Resource does not exist")}, 404

        if not _may_open(user, resource, has_role(user, "admin")):
            logger.info("Denied %s access to resource %s", user, resource_id)
            return {"msg": _("Resource does not exist")}, 404

        # The detail screen renders a resolved `fields` array plus navigation
        # furniture, not the raw document - and the same component renders the
        # public response, so both go through the same describer.
        resource = presentation.describe(resource, user)
        resource["id"] = str(resource.pop("_id"))
        return parse_result(resource), 200
    except Exception as exc:
        logger.exception("Could not load resource %s", resource_id)
        return {"msg": str(exc)}, 500


def load_visible(
    resource_id: str, user: str, fields: dict | None = None
) -> tuple[dict | None, tuple[dict, int] | None]:
    """``(resource, error)``. Exactly one of them is not ``None``.

    The same three gates ``get_by_id`` applies, for callers that need the raw
    document rather than the rendered one - the gallery viewers, which want
    ``filesObj`` and nothing else. Kept beside ``get_by_id`` so the rule has one
    home; a caller that re-derives it from ``resource["accessRights"]`` misses
    ancestor inheritance, which is exactly how it went wrong once already.

    The projection must include ``accessRights``, ``parents``, ``status`` and
    ``post_type`` for the gates to be answerable, so they are added to whatever
    the caller asked for.
    """
    from archihub.api.users.services import has_role

    object_id = _to_object_id(resource_id)
    if object_id is None:
        return None, ({"msg": _("Resource does not exist")}, 404)

    projection = None
    if fields:
        projection = {**fields, "accessRights": 1, "parents": 1, "status": 1, "post_type": 1}

    resource = _mongo().get_record(COLLECTION, {"_id": object_id}, fields=projection)
    if not resource:
        return None, ({"msg": _("Resource does not exist")}, 404)

    if not _may_open(user, resource, has_role(user, "admin")):
        logger.info("Denied %s access to resource %s", user, resource_id)
        return None, ({"msg": _("Resource does not exist")}, 404)

    return resource, None


def _may_open(user: str, resource: dict, is_admin: bool) -> bool:
    """The full visibility rule for a single resource."""
    if resource.get("status") == "deleted" and not access.may_see_deleted(user, is_admin):
        return False

    if not access.may_view_resource(user, resource, is_admin):
        return False

    post_type = resource.get("post_type")
    return not post_type or can_view_type(user, post_type)


def get_fav_count(resource_id: str) -> tuple[dict, int]:
    """How many users have favourited a resource.

    Two legacy failure modes become ordinary answers here: the original *raised*
    for an unknown resource, out of a route with no handler, so a stale id
    returned 500 instead of 404; and it subscripted ``favCount`` directly, so a
    resource that had simply never been favourited - the field is only written
    on the first favourite - also produced a 500. Zero is the right answer for
    that one.
    """
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return {"msg": _("Resource does not exist")}, 404

    resource = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"favCount": 1})
    if not resource:
        return {"msg": _("Resource does not exist")}, 404

    return {"favCount": resource.get("favCount") or 0}, 200


