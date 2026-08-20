"""Creating, listing and deleting snaps.

A snap stores *coordinates* into a record, never pixels: a bounding box and a
page for a document or image, a start and end time for a recording. Rendering
happens on every read (``render.py``), which is what keeps a snap correct when
the underlying derivative is regenerated - and what makes the record's access
rule the thing that governs it.

**A SNAP IS NOT A CAPABILITY.** Both sides check: creating one requires the
record to be visible to the creator, and reading one requires ownership *and*
that the record is still visible. Checking only on read leaves the archive one
layer deep, on the wrong side of the write - any authenticated user could snap
any record and store its filename.

**The stored data is validated at creation.** It is read back much later, by a
different code path, and sometimes on another user's screen — article blocks
embed snaps — so a malformed box or a missing page is not the creator's problem
alone.
"""

from __future__ import annotations

import datetime
import logging

from bson.objectid import ObjectId

from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "snaps"
PAGE_SIZE = 20

#: The kinds of source a snap can be taken from. Anything else is refused at
#: creation rather than discovered at render time.
SNAP_TYPES = ("document", "image", "video", "audio")

#: Snap kinds addressed by a rectangle on a page, versus by a time range.
SPATIAL_TYPES = ("document", "image")
TEMPORAL_TYPES = ("video", "audio")

MSG_NOT_FOUND = "Snap not found"
MSG_UNAUTHORIZED = "You don't have the required authorization"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _to_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def parse_result(result):
    import json

    from bson import json_util

    return json.loads(json_util.dumps(result))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_data(snap_type: str, data) -> tuple[dict | None, str | None]:
    """``(data, error)``. What a snap of this kind must carry to be renderable.

    Written as validation rather than trusted because the values become
    arithmetic on an image (``width * data['bbox']['x']``) and arguments to
    ffmpeg. The original stored whatever arrived and discovered the problem at
    render time, as a ``KeyError`` reaching the client as a 500 - on whoever
    happened to be looking at the page, not necessarily the person who made it.
    """
    if not isinstance(data, dict):
        return None, _("data is missing")

    if snap_type in SPATIAL_TYPES:
        return _validate_box(snap_type, data)
    return _validate_range(data)


def _validate_box(snap_type: str, data: dict) -> tuple[dict | None, str | None]:
    box = data.get("bbox")
    if not isinstance(box, dict):
        return None, _("bbox is missing")

    cleaned = {}
    for key in ("x", "y", "width", "height"):
        value = box.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, _("bbox is missing")
        # Coordinates are fractions of the rendered image, so they live in
        # [0, 1]. Out-of-range values crop outside the picture, which PIL
        # happily pads with black rather than refusing.
        if not 0 <= float(value) <= 1:
            return None, _("bbox is outside the image")
        cleaned[key] = float(value)

    if cleaned["width"] <= 0 or cleaned["height"] <= 0:
        return None, _("bbox is outside the image")
    if cleaned["x"] + cleaned["width"] > 1 or cleaned["y"] + cleaned["height"] > 1:
        return None, _("bbox is outside the image")

    result = {"bbox": cleaned}

    if snap_type == "document":
        page = data.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            # Pages are 1-indexed here. The original subtracted one and passed
            # the result through, so page 0 asked for index -1 and returned the
            # last page of the document.
            return None, _("You must specify a page")
        result["page"] = page

    return result, None


def _validate_range(data: dict) -> tuple[dict | None, str | None]:
    begin, end = data.get("begin"), data.get("end")
    for value in (begin, end):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, _("Invalid start_ms or end_ms")

    begin, end = float(begin), float(end)
    if begin < 0 or end <= begin:
        return None, _("Invalid start_ms or end_ms")

    return {"begin": begin, "end": end}, None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create(user: str, body: dict) -> tuple[dict, int]:
    """Save a snap of a record the caller can actually see."""
    from archihub.api.records.services import load_visible

    record_id = body.get("record_id")
    snap_type = body.get("type")

    if not record_id:
        return {"msg": _("id is missing")}, 400
    if snap_type not in SNAP_TYPES:
        return {"msg": _("Unsupported snap type")}, 400

    # The access check the original did not make. `load_visible` answers 404 for
    # a record that does not exist and a role failure for one this caller may
    # not open, so neither confirms anything the caller should not know.
    record, error = load_visible(record_id, user)
    if error is not None:
        return error

    data, message = validate_data(snap_type, body.get("data"))
    if message is not None:
        return {"msg": message}, 400

    if not _source_matches(record, snap_type):
        return {"msg": _("Unsupported snap type")}, 400

    snap = {
        "user": user,
        "record_id": record_id,
        "record_name": record.get("displayName") or record.get("name"),
        "type": snap_type,
        "data": data,
        "createdAt": _now(),
    }
    inserted = _mongo().insert_record(COLLECTION, snap)

    _audit(user, "snap_create", {"snap": {"id": str(inserted.inserted_id)}})
    return {"msg": _("Snap created successfully"), "id": str(inserted.inserted_id)}, 201


def _source_matches(record: dict, snap_type: str) -> bool:
    """Whether the record is the kind of thing this snap claims to cut from.

    A time range out of a scanned page is not renderable, and finding that out
    at read time - which is what the original did - means a stored snap that can
    never display.
    """
    processing = record.get("processing") or {}
    entry = processing.get("fileProcessing") if isinstance(processing, dict) else None
    kind = entry.get("type") if isinstance(entry, dict) else None
    if kind is None:
        return False
    return kind == snap_type


# ---------------------------------------------------------------------------
# Read and delete
# ---------------------------------------------------------------------------


def load_own(snap_id: str, user: str) -> tuple[dict | None, tuple[dict, int] | None]:
    """``(snap, error)``. A snap belongs to the person who made it.

    Not even an administrator reads someone else's - a snap is a personal
    working note, and the original made the same choice. Stated here so every
    caller applies it identically.
    """
    object_id = _to_object_id(snap_id)
    if object_id is None:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    snap = _mongo().get_record(
        COLLECTION,
        {"_id": object_id},
        fields={"user": 1, "record_id": 1, "data": 1, "type": 1, "record_name": 1},
    )
    if not snap:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    if snap.get("user") != user:
        logger.info("Denied %s access to snap %s", user, snap_id)
        return None, ({"msg": _(MSG_UNAUTHORIZED)}, ROLE_FAILURE_STATUS)

    return snap, None


def delete(snap_id: str, user: str) -> tuple[dict, int]:
    snap, error = load_own(snap_id, user)
    if error is not None:
        return error

    _mongo().delete_record(COLLECTION, {"_id": ObjectId(snap_id)})
    _audit(user, "snap_delete", {"snap": {"id": snap_id}})
    return {"msg": _("Snap deleted successfully")}, 200


def list_for_user(user: str, body: dict) -> tuple[dict, int]:
    """One page of a user's snaps of a given kind.

    Backs ``POST /users/snaps``. The heavy ``data`` field is projected out - a
    listing shows names, and rendering needs the source file anyway.
    """
    snap_type = body.get("type")
    if snap_type not in SNAP_TYPES:
        return {"msg": _("Unsupported snap type")}, 400

    page = body.get("page") or 0
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        page = 0

    filters = {"user": user, "type": snap_type}
    mongo = _mongo()
    snaps = list(
        mongo.get_all_records(
            COLLECTION,
            filters,
            fields={"data": 0},
            sort=[("createdAt", -1)],
            limit=PAGE_SIZE,
            skip=page * PAGE_SIZE,
        )
    )

    for snap in snaps:
        snap["_id"] = str(snap["_id"])

    return parse_result({"results": snaps, "total": mongo.count(COLLECTION, filters)}), 200


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _audit(user: str | None, action: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, action, details)
