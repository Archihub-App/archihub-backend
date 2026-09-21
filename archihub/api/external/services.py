"""Shared behaviour for the two externally-consumed APIs.

**A CLIENT-SUPPLIED OBJECT NEVER BECOMES A MONGO FILTER.** These routes need an
admin API token, but an API token is a long-lived credential handed to an
integration, so "the caller is trusted" is not a defence.

Lookups follow the pattern used for `/users` and `/logs`: a field
allowlist, string equality only, matching what the endpoint is actually for.
"""

from __future__ import annotations

import logging

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

RESOURCES_COLLECTION = "resources"
OPTIONS_COLLECTION = "options"

#: Fields an external caller may look a resource up by. Deliberately narrow:
#: this endpoint exists so an integration can find the resource it just created
#: or is about to update, by an identifier it already knows.
LOOKUP_FIELDS = ("ident", "post_type")

#: Metadata paths a lookup may also match on. Restricted to the first level,
#: which is the part every content type declares.
LOOKUP_METADATA_PREFIX = "metadata.firstLevel."

#: What a lookup returns. Fixed, so a widening of the stored document does not
#: silently widen what an integration receives.
LOOKUP_PROJECTION = {
    "_id": 1, "post_type": 1, "metadata": 1, "filesObj": 1, "parent": 1, "parents": 1,
}


class InvalidRequest(Exception):
    """The request cannot be turned into something safe to run."""


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def build_lookup(body: dict) -> dict:
    """A Mongo filter from a caller's lookup request, built field by field.

    Only allowlisted keys survive, and only string values — an object here would
    be an operator.
    """
    if not isinstance(body, dict):
        raise InvalidRequest(_("The request body must be an object"))

    filters: dict = {}
    for key, value in body.items():
        if not isinstance(key, str):
            continue
        if key not in LOOKUP_FIELDS and not key.startswith(LOOKUP_METADATA_PREFIX):
            continue
        if not isinstance(value, str):
            raise InvalidRequest(_('"{field}" must be a text value', field=key))
        filters[key] = value

    if not filters:
        raise InvalidRequest(
            _("You must supply at least one of: {fields}", fields=", ".join(LOOKUP_FIELDS))
        )

    # Fixed, not client-settable: this endpoint answers about published
    # material only.
    filters["status"] = "published"
    return filters


def find_resource(body: dict) -> tuple[dict, int]:
    """Look up one published resource by identifier."""
    try:
        filters = build_lookup(body)
    except InvalidRequest as exc:
        return {"msg": str(exc)}, 400

    resource = _mongo().get_record(RESOURCES_COLLECTION, filters, fields=LOOKUP_PROJECTION)
    if not resource:
        return {"msg": _("Resource not found")}, 404

    # `.get` rather than subscripting: a resource missing any of these fields
    # is still a valid answer.
    return {
        "id": str(resource["_id"]),
        "post_type": resource.get("post_type"),
        "metadata": resource.get("metadata") or {},
        "filesObj": resource.get("filesObj") or [],
        "parent": resource.get("parent") or [],
        "parents": resource.get("parents") or [],
    }, 200


def find_option(body: dict) -> tuple[dict, int]:
    """Look up a controlled-vocabulary option by its display term."""
    if not isinstance(body, dict):
        return {"msg": _("The request body must be an object")}, 400

    term = body.get("term")
    if not isinstance(term, str) or not term.strip():
        return {"msg": _('"{field}" must be a text value', field="term")}, 400

    option = _mongo().get_record(OPTIONS_COLLECTION, {"term": term}, fields={"_id": 1})
    if not option:
        return {"msg": _("Option not found")}, 404

    return {"id": str(option["_id"])}, 200


def system_info(username: str) -> tuple[dict, int]:
    """What an integration needs to know about this instance."""
    from archihub.api.system.services import get_system_settings

    mongo = _mongo()
    post_types = list(
        mongo.get_all_records(
            "post_types", {}, sort=[("name", 1)],
            fields={"_id": 0, "name": 1, "slug": 1, "description": 1},
        )
    )

    settings, status = get_system_settings()
    if status != 200:
        return settings, status

    return {
        "user": username,
        "post_types": post_types,
        "capabilities": settings.get("capabilities", []),
        "metrics": {
            "published_resources": mongo.count(RESOURCES_COLLECTION, {"status": "published"}),
            "records_count": mongo.count("records", {"status": {"$ne": "deleted"}}),
        },
    }, 200


def with_defaults(body: dict, *, update: bool = False) -> dict:
    """Fill in the fields an integration is not required to send.

    Integrations rely on being able to omit these. `updateCache` is dropped:
    the cache is invalidated by every write, so there is nothing to ask for.
    """
    payload = dict(body or {})
    payload.setdefault("filesIds", [])
    payload.setdefault("status", "published")
    payload.setdefault("parent", [])
    payload.setdefault("parents", [])
    if update:
        payload.setdefault("deletedFiles", [])
    payload.pop("updateCache", None)

    if not payload.get("post_type"):
        from archihub.api.system.services import get_setting_value

        default_type = get_setting_value("post_types_settings", "default_type", 0)
        if not default_type:
            raise InvalidRequest(_("Unable to get the default cataloging type"))
        payload["post_type"] = default_type

    return payload
