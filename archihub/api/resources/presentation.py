"""Turning a stored resource into what the detail screen renders.

The detail response is not the document. It carries a `fields` array — every
metadata field resolved to a label, a display value and a type the interface
knows how to draw — plus the navigation furniture around it: the resource's
icon, the child content types beneath it, and its ancestors named.

**Both detail routes need this, and they are the same screen.**
`ResourcesService.getOne` sends an authenticated request when there is a token
and an anonymous one otherwise, into `GET /resources/{id}` or
`GET /resources/public/{id}` respectively, and renders the result with the same
component. So the two produce the same shape and differ only in the caller's
rights — which is one argument here, not a second implementation. The originals
were two copies that had already drifted: the public one dropped the per-field
access check down to "any `accessRights` at all hides it", which is right for an
anonymous caller and wrong as a copy.

**Field-level access rights are a real thing here.** A form can mark individual
fields as restricted; those are replaced with a refusal string rather than
omitted, so the interface shows that something exists and is not for you.
"""

from __future__ import annotations

import datetime
import logging

from archihub.api.resources.validation import get_value_by_path
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "resources"

#: Field kinds that never appear in the rendered list: files have their own
#: panel, separators are layout.
SKIPPED_TYPES = ("file", "separator")

#: Shown in place of a field the caller may not read.
MSG_RESTRICTED = "You don't have the required authorization"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _object_id(value):
    from bson.objectid import ObjectId

    try:
        return ObjectId(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The whole detail document
# ---------------------------------------------------------------------------


def describe(resource: dict, user: str | None, *, public: bool = False) -> dict:
    """Annotate a resource for display. Mutates and returns it."""
    resource["icon"] = _icon_of(resource.get("post_type"))
    resource["parents"] = describe_parents(resource.get("parents") or [])
    resource["children"] = child_types(str(resource.get("_id") or resource.get("id") or ""))

    files_obj = resource.get("filesObj")
    if files_obj is not None:
        if files_obj:
            resource["children"] = [
                {
                    "post_type": "files",
                    "name": _("Asociated files"),
                    "icon": "archivo",
                    "slug": "files",
                },
                *resource["children"],
            ]
            resource["files"] = len(files_obj)
        else:
            resource["files"] = None

    resource["fields"] = build_fields(resource, user, public=public)
    return resource


def _icon_of(post_type: str | None):
    if not post_type:
        return None
    record = _mongo().get_record("post_types", {"slug": post_type}, fields={"icon": 1})
    return (record or {}).get("icon")


def describe_parents(parents: list) -> list:
    """Name and icon each ancestor, in two queries rather than two per ancestor.

    A dangling ancestor keeps its entry but without a name: the original
    subscripted ``r_['metadata']['firstLevel']['title']`` on the result of a
    lookup it never checked, so one stale reference took the whole detail
    response down with a ``TypeError``.
    """
    wanted = [p for p in parents if isinstance(p, dict) and p.get("id")]
    if not wanted:
        return []

    object_ids = [oid for oid in (_object_id(p["id"]) for p in wanted) if oid is not None]
    rows = _mongo().get_all_records(
        COLLECTION,
        {"_id": {"$in": object_ids}},
        fields={"metadata.firstLevel.title": 1, "post_type": 1},
    )
    by_id = {str(row["_id"]): row for row in rows}
    icons = _icons_for({row.get("post_type") for row in rows if row.get("post_type")})

    described = []
    for parent in wanted:
        row = by_id.get(str(parent["id"]))
        entry = dict(parent)
        if row:
            entry["name"] = _title_of(row)
            entry["icon"] = icons.get(row.get("post_type"))
        described.append(entry)

    return described


def _title_of(resource: dict):
    metadata = resource.get("metadata")
    if isinstance(metadata, dict):
        first = metadata.get("firstLevel")
        if isinstance(first, dict):
            return first.get("title")
    return None


def _icons_for(slugs: set) -> dict:
    if not slugs:
        return {}
    rows = _mongo().get_all_records(
        "post_types", {"slug": {"$in": sorted(slugs)}}, fields={"slug": 1, "icon": 1}
    )
    return {row["slug"]: row.get("icon") for row in rows if row.get("slug")}


def child_types(resource_id: str) -> list[dict]:
    """The content types that exist beneath this resource, for its navigation tabs."""
    if not resource_id:
        return []

    from archihub.api.system.services import get_setting_value

    # `tipos_vista_individual` is the operator-configured list of content types
    # that appear as navigation tabs on a detail screen.
    visible = get_setting_value("post_types_settings", "tipos_vista_individual")
    if not visible:
        return []

    mongo = _mongo()
    slugs = mongo.distinct(
        COLLECTION, "post_type", {"parents.id": resource_id, "post_type": {"$in": visible}}
    )
    if not slugs:
        return []

    rows = mongo.get_all_records(
        "post_types", {"slug": {"$in": sorted(slugs)}}, fields={"slug": 1, "name": 1, "icon": 1}
    )
    by_slug = {row["slug"]: row for row in rows if row.get("slug")}

    described = []
    for slug in slugs:
        row = by_slug.get(slug)
        if not row:
            # A type that was deleted while resources still reference it. The
            # original subscripted the lookup directly and raised.
            continue
        described.append(
            {
                "post_type": slug,
                "name": row.get("name"),
                "icon": row.get("icon"),
                "slug": row.get("slug"),
            }
        )
    return described


# ---------------------------------------------------------------------------
# The rendered field list
# ---------------------------------------------------------------------------


def build_fields(resource: dict, user: str | None, *, public: bool = False) -> list[dict]:
    """Every metadata field of the resource's form, resolved for display."""
    from archihub.api.types.services import get_metadata

    try:
        form = get_metadata(resource.get("post_type") or "")
    except RuntimeError:
        logger.warning("Resource %s has no resolvable form", resource.get("_id"))
        return []

    rendered: list[dict] = []
    for field in (form or {}).get("fields") or []:
        if field.get("type") in SKIPPED_TYPES or not field.get("destiny"):
            continue

        if not _may_read_field(field, user, public=public):
            rendered.append(
                {"label": field.get("label"), "value": _(MSG_RESTRICTED), "type": "text"}
            )
            continue

        entry = _render(field, resource)
        if entry is not None:
            rendered.append(entry)

    return rendered


def _may_read_field(field: dict, user: str | None, *, public: bool) -> bool:
    """Whether this caller may read one restricted field.

    The original's loop did not stop at the first failure - it kept iterating
    and let a later right re-set ``canView`` to True, so holding *any* one of a
    field's rights was not required to be the last one checked. Written as
    "holds at least one" here, which is what the surrounding code means by it.
    """
    required = field.get("accessRights")
    if not required:
        return True
    if public:
        return False

    from archihub.api.records.access import holds
    from archihub.api.users.services import has_role

    return has_role(user, "admin") or holds(user, required)


def _render(field: dict, resource: dict) -> dict | None:
    """One field, or ``None`` when it has no value worth showing."""
    kind = field.get("type")
    label = field.get("label")
    value = get_value_by_path(resource, field["destiny"])

    if not value:
        return None

    if kind in ("text", "text-area"):
        return {
            "label": label,
            "value": value,
            "type": kind,
            "isTitle": field.get("destiny") == "metadata.firstLevel.title",
        }

    if kind == "pattern":
        return {"label": label, "value": value, "type": "text"}

    if kind == "number":
        return {"label": label, "value": value, "type": "number"}

    if kind == "location":
        return {"label": label, "value": value, "type": "location"}

    if kind == "simple-date":
        if isinstance(value, datetime.datetime):
            value = value.isoformat()
        return {"label": label, "value": value, "type": "simple-date"}

    if kind == "select":
        term = _term_of(value)
        return {"label": label, "value": [term], "type": "select"} if term else None

    if kind == "select-multiple2":
        terms = [
            term
            for term in (_term_of(item.get("id")) for item in value if isinstance(item, dict))
            if term
        ]
        return {"label": label, "value": terms, "type": "select"} if terms else None

    if kind == "author":
        return {"label": label, "value": [_author_name(v) for v in value], "type": "author"}

    if kind == "relation":
        return {"label": label, "value": _relations(value), "type": "relation"}

    if kind == "repeater":
        return {"label": label, "value": _repeater(field, value), "type": "repeater"}

    return None


def _term_of(option_id):
    """The display term of a list option, or ``None``."""
    if not option_id:
        return None
    from archihub.api.lists.services import get_option_by_id

    option = get_option_by_id(option_id)
    return option.get("term") if isinstance(option, dict) else None


def _author_name(value) -> str:
    """``Surname|Given`` or ``Surname, Given`` rendered as a plain name."""
    if not isinstance(value, str):
        return str(value)
    for separator in ("|", ","):
        if separator in value:
            return " ".join(part.strip() for part in value.split(separator) if part.strip())
    return value


def _relations(value) -> list[dict]:
    """Related resources, named, in one query rather than one per relation."""
    entries = [v for v in value if isinstance(v, dict) and v.get("id")]
    if not entries:
        return []

    object_ids = [oid for oid in (_object_id(e["id"]) for e in entries) if oid is not None]
    rows = _mongo().get_all_records(
        COLLECTION, {"_id": {"$in": object_ids}}, fields={"metadata.firstLevel.title": 1}
    )
    titles = {str(row["_id"]): _title_of(row) for row in rows}
    icons = _icons_for({e.get("post_type") for e in entries if e.get("post_type")})

    described = []
    for entry in entries:
        # A relation pointing at a deleted resource is dropped rather than
        # raising, which is what the original did by subscripting the lookup.
        if str(entry["id"]) not in titles:
            continue
        described.append(
            {
                "id": entry["id"],
                "post_type": entry.get("post_type"),
                "name": titles[str(entry["id"])],
                "icon": icons.get(entry.get("post_type")),
            }
        )
    return described


#: Subfield kinds a repeater renders. Anything else is skipped.
REPEATER_SUBFIELDS = ("text", "text-area", "number", "checkbox", "simple-date")


def _repeater(field: dict, value) -> list[list[dict]]:
    """Each row of a repeater, as a list of rendered subfields.

    Subfield values are read with ``.get`` rather than subscripted: the original
    indexed ``v[s['destiny']]`` directly, so a row saved before a subfield was
    added to the form raised ``KeyError`` on read.
    """
    rows = []
    for row in value:
        if not isinstance(row, dict):
            continue

        rendered = []
        for subfield in field.get("subfields") or []:
            kind = subfield.get("type")
            if kind not in REPEATER_SUBFIELDS or not subfield.get("destiny"):
                continue

            item = row.get(subfield["destiny"])
            if item is None:
                continue
            if kind == "simple-date" and isinstance(item, datetime.datetime):
                item = item.strftime("%Y-%m-%d")

            rendered.append({"label": subfield.get("name"), "value": item, "type": kind})

        rows.append(rendered)
    return rows
