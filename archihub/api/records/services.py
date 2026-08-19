"""Reading records.

Port of the read half of ``app/api/records/services.py``. The write half - the
upload and deduplication path - is in ``storage.py``.

A SYSTEMATIC DEFECT IS FIXED HERE, at every site rather than one of them. Eight
functions in the original open with:

    resp_, status = get_by_id(id, current_user)
    if status != 200:
        return {'msg': resp_['msg']}, 500

so a **404** (no such record) and a **401** (not yours to read) both reach the
client as **500**. The message survives, the status does not. `upgrade_front`
branches on status, so a permission failure looked to it like a server fault.
Recorded as BACKEND_FINDINGS F27; the helper below returns the real status.
"""

from __future__ import annotations

import datetime
import logging

from bson.objectid import ObjectId

from archihub.api.records import access
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "records"
PAGE_SIZE = 20

MSG_NOT_FOUND = "Record does not exist"
MSG_UNAUTHORIZED = "You do not have permission to view this record"

#: Fields the detail view returns. `filepath` is included only for internal
#: callers - it is where the file lives on the server's disk and has no business
#: reaching a browser.
DETAIL_FIELDS = {
    "parent": 1, "parents": 1, "accessRights": 1, "hash": 1, "processing": 1,
    "name": 1, "displayName": 1, "size": 1, "filepath": 1, "mime": 1, "status": 1,
}

#: EXIF names worth surfacing, WITHOUT their group prefix. ExifTool writes every
#: key as ``<group>:<name>`` - ``EXIF:Make``, ``File:ImageWidth``,
#: ``Composite:Megapixels`` - and the same name appears under several groups, so
#: the allowlist is matched against the name and the stored key is kept as-is.
#:
#: Everything unlisted is dropped rather than passed through. The full block
#: routinely carries GPS coordinates, camera serial numbers and owner names, and
#: `File:Directory`/`File:FileName` state where the file sits on the server -
#: the one thing the records API goes out of its way never to return.
IMPORTANT_EXIF = (
    "Make", "Model", "DateTimeOriginal", "ExposureTime", "FNumber", "ISO",
    "FocalLength", "LensModel", "Orientation", "XResolution", "YResolution",
    "ImageWidth", "ImageHeight", "ColorSpace", "Software", "ImageSize",
    "Megapixels", "MIMEType", "FileType", "BitsPerSample",
    # Media records carry these unprefixed, and the players show both.
    "duration_ms", "bit_rate",
)


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _to_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_result(result):
    import json

    from bson import json_util

    return json.loads(json_util.dumps(result))


# ---------------------------------------------------------------------------
# The shared guard
# ---------------------------------------------------------------------------


def load_visible(record_id: str, user: str) -> tuple[dict | None, tuple[dict, int] | None]:
    """``(record, error)``. Exactly one of them is not ``None``.

    Every route that serves a record or something derived from it starts here,
    so the access rule is applied once and the *real* status is what comes back
    - not the blanket 500 the original produced.
    """
    from archihub.api.users.services import has_role

    object_id = _to_object_id(record_id)
    if object_id is None:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    record = _mongo().get_record(COLLECTION, {"_id": object_id}, fields=DETAIL_FIELDS)
    if not record:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    if not access.may_view_record(user, record, has_role(user, "admin")):
        logger.info("Denied %s access to record %s", user, record_id)
        return None, ({"msg": _(MSG_UNAUTHORIZED)}, LEGACY_ROLE_FAILURE_STATUS)

    return record, None


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_by_id(record_id: str, user: str, full_fields: bool = False) -> tuple[dict, int]:
    """One record, with its parents resolved for display."""
    record, error = load_visible(record_id, user)
    if error is not None:
        return error

    if not full_fields:
        record["processing"] = _summarise_processing(record.get("processing"))
        # Server-side storage path; never sent to a browser.
        record.pop("filepath", None)

    record["parent"] = _describe_parents(record.get("parent") or [])
    record.pop("parents", None)

    return parse_result(record), 200


def _summarise_processing(processing) -> dict | None:
    """What each processing plugin produced, without its raw output.

    The stored block holds full plugin results - complete EXIF, OCR page trees,
    transcription segments - which the detail view neither shows nor should
    ship.
    """
    if not isinstance(processing, dict):
        return processing

    summary: dict[str, dict] = {}
    for key, value in processing.items():
        if not isinstance(value, dict):
            continue

        entry: dict = {"type": value.get("type")}
        if "metadata" in value:
            entry["metadata"] = important_exif(value["metadata"])
        if key == "fileProcessing":
            if "cloud" in value:
                entry["cloud"] = value["cloud"]
            if value.get("dzi"):
                entry["dzi"] = value["dzi"]
        summary[key] = entry

    return summary


def important_exif(metadata) -> dict:
    """The presentable subset of an EXIF block.

    Everything not listed is dropped rather than passed through: the full block
    routinely carries GPS coordinates, camera serial numbers and owner names,
    none of which an archive means to publish with a scan.
    """
    if not isinstance(metadata, dict):
        return {}
    allowed = set(IMPORTANT_EXIF)
    return {
        key: value
        for key, value in metadata.items()
        if _exif_name(key) in allowed
    }


def _exif_name(key: str) -> str:
    """An ExifTool key without its group prefix.

    Matching the whole key was the defect: every stored key is
    ``<group>:<name>``, so an allowlist of bare names matched nothing at all and
    the panel came back empty - a 200 with a complete record and no metadata on
    the screen, which reads as "this scan has none".
    """
    return key.rsplit(":", 1)[-1] if isinstance(key, str) else ""


def _describe_parents(parents: list) -> list[dict]:
    """Annotate each parent with its title and icon, in two queries.

    The original ran one query per parent for the resource, another per parent
    for its type icon, and a third per parent for its access rights - and
    subscripted ``metadata.firstLevel.title`` directly, so a parent missing a
    title took the whole record fetch down with a ``KeyError``.

    A dangling parent is dropped from the result. That behaviour is kept: the
    reference is stale, and rendering a breadcrumb entry that leads nowhere is
    worse than omitting it.
    """
    wanted = [p for p in parents if isinstance(p, dict) and p.get("id")]
    if not wanted:
        return []

    object_ids = [oid for oid in (_to_object_id(p["id"]) for p in wanted) if oid is not None]
    # `list(...)`, because this is a cursor and it is read twice. Without it the
    # second pass sees an exhausted cursor, the icon set comes back empty, and
    # every parent gets `"icon": None` - a 200 with a missing icon, which is
    # invisible until someone looks at the breadcrumb.
    rows = list(
        _mongo().get_all_records(
            "resources",
            {"_id": {"$in": object_ids}},
            fields={"metadata.firstLevel.title": 1, "post_type": 1, "status": 1},
        )
    )
    by_id = {str(row["_id"]): row for row in rows}

    icons = _icons_for({row.get("post_type") for row in rows if row.get("post_type")})

    described = []
    for parent in wanted:
        resource = by_id.get(str(parent["id"]))
        if not resource:
            continue

        entry = dict(parent)
        entry["id"] = str(parent["id"])
        entry["name"] = _title_of(resource)
        entry["icon"] = icons.get(resource.get("post_type"))
        described.append(entry)

    return described


def _title_of(resource: dict) -> str:
    metadata = resource.get("metadata")
    if isinstance(metadata, dict):
        first_level = metadata.get("firstLevel")
        if isinstance(first_level, dict) and first_level.get("title"):
            return first_level["title"]
    return _("Untitled")


def _icons_for(slugs: set) -> dict:
    if not slugs:
        return {}
    rows = _mongo().get_all_records(
        "post_types", {"slug": {"$in": sorted(slugs)}}, fields={"slug": 1, "icon": 1}
    )
    return {row["slug"]: row.get("icon") for row in rows if row.get("slug")}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

_DANGEROUS_OPERATORS = {"$where", "$function", "$accumulator", "$expr", "$jsonschema"}


def reject_dangerous_operators(value) -> None:
    """Refuse server-side JavaScript and expression evaluation, at any depth.

    This endpoint is administrator-only and its contract promises a flexible
    Mongo filter, so a field allowlist would defeat the point - the blocklist is
    the right shape here, but **its safety rests on the admin gate**. Do not
    relax that role check without revisiting this.
    """
    if isinstance(value, dict):
        for key, sub_value in value.items():
            if isinstance(key, str) and key.lower() in _DANGEROUS_OPERATORS:
                raise ValueError(_('Filter operator "{op}" is not allowed', op=key))
            reject_dangerous_operators(sub_value)
    elif isinstance(value, list):
        for item in value:
            reject_dangerous_operators(item)


def get_by_filters(body: dict, user: str) -> tuple[list | dict, int]:
    """The administrator-facing record search."""
    filters = body.get("filters")
    if not isinstance(filters, dict):
        return {"msg": _("Filters are required")}, 400

    try:
        reject_dangerous_operators(filters)
    except ValueError as exc:
        return {"msg": str(exc)}, 400

    page = int(body.get("page") or 0)
    mongo = _mongo()
    records = list(
        mongo.get_all_records(COLLECTION, filters, limit=PAGE_SIZE, skip=page * PAGE_SIZE)
    )
    total = mongo.count(COLLECTION, filters)

    for record in records:
        record["id"] = str(record.pop("_id"))
        record["total"] = total
        record.pop("filepath", None)

    _audit(user, "record_get_all", {"filters": filters})

    # An empty page is a successful query with no results. The original
    # answered 404, which made "nothing matched" indistinguishable from "the
    # endpoint is wrong" and broke pagination past the last page.
    return parse_result(records), 200


def get_by_gallery_index(body: dict, user: str) -> tuple[dict, int]:
    """The nth image of a resource's gallery, in display order."""
    resource_id = body.get("id")
    index = body.get("index")

    if not resource_id:
        return {"msg": _("id is missing")}, 400
    if index is None:
        return {"msg": _("index is missing")}, 400
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return {"msg": _("index is missing")}, 400

    object_id = _to_object_id(resource_id)
    resource = (
        _mongo().get_record("resources", {"_id": object_id}, fields={"filesObj": 1})
        if object_id is not None
        else None
    )
    if not resource:
        return {"msg": _("Resource does not exist")}, 404

    files = [f for f in (resource.get("filesObj") or []) if isinstance(f, dict) and f.get("id")]
    order_of = {f["id"]: f.get("order", 0) for f in files}

    object_ids = [oid for oid in (_to_object_id(f["id"]) for f in files) if oid is not None]
    images = list(
        _mongo().get_all_records(
            COLLECTION,
            {"_id": {"$in": object_ids}, "processing.fileProcessing.type": "image"},
            fields={"_id": 1},
        )
    )

    # The original keyed the order map by the resource's string ids and looked
    # it up with the record's ObjectId, so nothing ever matched and every
    # gallery came back in Mongo's natural order rather than the curator's.
    images.sort(key=lambda image: order_of.get(str(image["_id"]), float("inf")))

    if index >= len(images):
        return {"msg": _(MSG_NOT_FOUND)}, 404

    return get_by_id(str(images[index]["_id"]), user)


# ---------------------------------------------------------------------------
# Plugin output
# ---------------------------------------------------------------------------


def get_processing_result(record: dict, slug: str) -> tuple[dict | list, int]:
    """What a named processing produced for this record.

    Large results are stored as chunk documents in a separate collection rather
    than inline, so both shapes are read. Datetimes are rendered as strings
    because the result is plugin-defined and may hold them at any depth the
    JSON encoder would otherwise refuse.
    """
    from archihub.api.records.viewers import load_chunked_result

    processing = record.get("processing")
    entry = processing.get(slug) if isinstance(processing, dict) else None
    if not isinstance(entry, dict):
        return {"msg": _("Record has not been processed with {slug}", slug=slug)}, 404

    storage = entry.get("result_storage") or {}
    if storage.get("type") == "chunked":
        result = load_chunked_result(record.get("_id"), storage)
    else:
        result = entry.get("result")
        if result is None:
            result = []

    return parse_result(result), 200


def get_processing_metadata(record: dict, slug: str) -> tuple[dict, int]:
    """The metadata block a named processing extracted.

    EXIF is filtered to the presentable subset here as it is on the detail
    route - this endpoint reads the same stored block, and serving it whole
    would hand back the GPS coordinates and camera serial numbers the detail
    route is careful to drop.
    """
    processing = record.get("processing")
    entry = processing.get(slug) if isinstance(processing, dict) else None
    if not isinstance(entry, dict):
        return {"msg": _("Record has not been processed with {slug}", slug=slug)}, 404

    result = entry.get("result")
    metadata = result.get("metadata") if isinstance(result, dict) else None
    if metadata is None:
        return {"msg": _("Record does not contain metadata")}, 404

    return parse_result(important_exif(metadata)), 200


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------


def get_fav_count(record_id: str) -> tuple[dict, int]:
    object_id = _to_object_id(record_id)
    if object_id is None:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    record = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"favCount": 1})
    if not record:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    return {"favCount": record.get("favCount") or 0}, 200


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

#: The only fields a client may set on a record through the generic update.
#: The original passed the caller's whole body into ``RecordUpdate``, which
#: also declares `parent`, `parents`, `processing` and `status` - so a display
#: rename could re-file the record or overwrite a plugin's results.
UPDATABLE_FIELDS = ("displayName", "accessRights")


def update_record_by_id(record_id: str, user: str | None, body: dict) -> tuple[dict, int]:
    """Set a record's display name and access right."""
    object_id = _to_object_id(record_id)
    if object_id is None:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    record = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"_id": 1})
    if not record:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    update = {field: body[field] for field in UPDATABLE_FIELDS if field in body}
    if "accessRights" in update and update["accessRights"] == "public":
        update["accessRights"] = None

    if not update:
        return {"msg": _("Nothing to update")}, 400

    update["updatedBy"] = user or "system"
    update["updatedAt"] = _now()

    _mongo().update_record(COLLECTION, {"_id": object_id}, update)
    _audit(user, "record_update", {"record": record_id})
    _call_hook("record_update", {**update, "_id": record_id})

    return {"msg": _("Record updated")}, 200


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _audit(user: str | None, action: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, action, details)


def _call_hook(name: str, payload: dict) -> None:
    from archihub.core.hooks import get_hook_handler

    try:
        get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("%s hook failed", name)
