"""Saved views: named entry points into the archive.

A view names a root, a parent to scope to, and the content types it shows. The
explore screens are built from one, and two of the routes here are **public** -
the public site lists views and reads their contents.

That public exposure is what shapes this module.

**`filesObj` is server-owned.** A view's thumbnail is a record attached through
the upload path, and nothing else. The originals passed the request body
straight into ``View(**body)`` / ``ViewUpdate(**update_body)``, and both models
declare ``filesObj`` - so a client could point a view's thumbnail at *any*
record id. ``_thumbnail`` then read that record and base64-encoded its
derivative into the response of ``get_all``, which is **unauthenticated**. That
is a route from "can edit a view" to "can publish any image in the archive".


**The thumbnail is only ever read from a record attached to that view.**
Belt and braces alongside the allowlist: even a ``filesObj`` written directly to
the database by something else cannot make a foreign record public through here.
"""

from __future__ import annotations

import base64
import logging

from bson.objectid import ObjectId

from archihub.core import files as filestore
from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "views"
RECORDS_COLLECTION = "records"

#: Content types a view's own record entries carry. A view is not a resource,
#: but its thumbnail hangs off it the same way a resource's files do.
VIEW_POST_TYPE = "view"

#: What a client may set on a view. `filesObj` is deliberately absent - it is
#: written only by the upload path.
CLIENT_FIELDS = ("name", "slug", "description", "parent", "root", "visible", "defaultView")

#: Fields a view must have to be usable at all.
REQUIRED_FIELDS = ("name", "slug", "root")

#: The tag the thumbnail record carries.
THUMBNAIL_TAG = "thumbnail"

#: Media kinds counted on the view-info screen.
COUNTED_FILE_TYPES = ("video", "audio", "document", "image", "database")


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


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
# Thumbnails
# ---------------------------------------------------------------------------


def _thumbnail(view: dict) -> str | None:
    """The view's thumbnail as a data URI, or ``None``.

    Inline rather than a URL because ``get_all`` is public and renders a grid of
    cards: a URL per card would be a request per card, each needing its own
    visibility decision.

    Only a record whose parent is **this view** is served. See the module
    docstring.
    """
    entries = [f for f in (view.get("filesObj") or []) if isinstance(f, dict) and f.get("id")]
    if not entries:
        return None

    chosen = next((f for f in entries if f.get("tag") == THUMBNAIL_TAG), entries[0])
    object_id = _to_object_id(chosen["id"])
    if object_id is None:
        return None

    record = _mongo().get_record(
        RECORDS_COLLECTION,
        {"_id": object_id},
        fields={"processing.fileProcessing": 1, "parent": 1, "accessRights": 1},
    )
    if not record:
        return None

    if not _belongs_to(record, str(view.get("_id") or "")):
        logger.warning(
            "View %s references a record that is not attached to it; refusing to serve it",
            view.get("_id"),
        )
        return None

    if record.get("accessRights"):
        # A thumbnail is published to anonymous callers. A restricted record can
        # never be one, whatever the view says.
        return None

    file_processing = (record.get("processing") or {}).get("fileProcessing") or {}
    if file_processing.get("type") != "image" or not file_processing.get("path"):
        # Processing has not produced a derivative yet. The card renders without
        # an image rather than failing.
        return None

    try:
        path = filestore.resolve_within(
            get_settings().web_files_path, file_processing["path"] + "_medium.jpg"
        )
    except filestore.UnsupportedFile:
        return None

    if not path.is_file():
        return None

    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("utf-8")


def _belongs_to(record: dict, view_id: str) -> bool:
    if not view_id:
        return False
    return any(
        isinstance(parent, dict) and str(parent.get("id")) == view_id
        for parent in (record.get("parent") or [])
    )


def _presented(view: dict) -> dict:
    """A view as the client sees it: thumbnail resolved, file entries dropped."""
    view["thumbnail"] = _thumbnail(view)
    view.pop("filesObj", None)
    return view


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def get(view_id: str) -> tuple[dict, int]:
    """One view, for the editor that maintains it."""
    object_id = _to_object_id(view_id)
    if object_id is None:
        return {"msg": _("View not found")}, 404

    view = _mongo().get_record(COLLECTION, {"_id": object_id})
    if not view:
        return {"msg": _("View not found")}, 404

    _presented(view)
    view["id"] = str(view.pop("_id"))
    return parse_result(view), 200


def get_all() -> tuple[list, int]:
    """Every view, as cards. Public."""
    views = _mongo().get_all_records(COLLECTION, {}, sort=[("name", 1)])

    return parse_result(
        [
            {
                "id": str(view["_id"]),
                "name": view.get("name"),
                "description": view.get("description"),
                "slug": view.get("slug"),
                "thumbnail": _thumbnail(view),
            }
            for view in views
        ]
    ), 200


def get_view_info(slug: str) -> tuple[dict, int]:
    """Everything the explore screen needs to render a view. Public.

    The original read ``view['visible']`` at the top of the function and only
    checked ``if not view`` thirty lines later, so an unknown slug raised
    ``TypeError`` on ``None`` and reached the client as a 500 rather than a 404.
    """
    view = _mongo().get_record(
        COLLECTION,
        {"slug": slug},
        fields={"name": 1, "description": 1, "parent": 1, "root": 1, "visible": 1, "defaultView": 1},
    )
    if not view:
        return {"msg": _("View not found")}, 404

    from archihub.api.types.services import get_by_slug, get_icon

    types = []
    tree_types = []
    forms = []
    fields = []

    for type_slug in view.get("visible") or []:
        post_type = get_by_slug(type_slug)
        if not isinstance(post_type, dict):
            # A view still naming a content type that has been deleted. The
            # original subscripted the lookup and took the whole screen down.
            logger.info("View %s names a missing content type %s", slug, type_slug)
            continue

        metadata = post_type.get("metadata")
        if isinstance(metadata, dict) and metadata.get("slug") not in [f["slug"] for f in forms]:
            forms.append({"slug": metadata.get("slug"), "name": metadata.get("name")})
            fields.append(metadata.get("fields"))

        for parent in _type_parents(post_type):
            if parent.get("slug") not in [t.get("slug") for t in tree_types]:
                tree_types.append(parent)

        types.append(
            {
                "slug": type_slug,
                "description": post_type.get("description"),
                "name": post_type.get("name"),
                "icon": post_type.get("icon"),
                "form": post_type.get("form"),
            }
        )

    view.pop("_id", None)
    view.pop("visible", None)
    view["types"] = types
    view["tree_types"] = tree_types
    view["forms"] = {"forms": forms, "fields": fields}
    view["icon"] = get_icon(view.get("root"))
    view["files"] = _file_counts(view, types)

    return parse_result(view), 200


def _type_parents(post_type: dict) -> list[dict]:
    from archihub.api.types.services import get_parents

    try:
        parents = get_parents(post_type)
    except Exception:
        logger.warning("Could not resolve parents of a content type", exc_info=True)
        return []
    return [p for p in (parents or []) if isinstance(p, dict)]


def _file_counts(view: dict, types: list[dict]) -> dict:
    """How many files the view covers, broken down by media kind.

    PUBLIC, AND SO COUNTS ONLY WHAT IS PUBLIC. The original counted every
    matching record with no access-rights or publication filter at all, on an
    unauthenticated route - so the totals disclosed how much reserved and
    unpublished material an archive holds.
    """
    mongo = _mongo()

    if view.get("parent"):
        base: dict = {"$or": [{"parents.id": view["parent"]}, {"parent.id": view["parent"]}]}
    else:
        base = {"parent.post_type": {"$in": [t["slug"] for t in types]}}

    base["status"] = {"$ne": "deleted"}
    base["$and"] = [{"$or": [{"accessRights": None}, {"accessRights": {"$exists": False}}]}]

    counts = []
    for kind in COUNTED_FILE_TYPES:
        counts.append(
            {"_id": kind, "count": mongo.count(RECORDS_COLLECTION, {**base, "processing.fileProcessing.type": kind})}
        )
    counts.sort(key=lambda entry: entry["count"], reverse=True)

    return {"total": mongo.count(RECORDS_COLLECTION, base), "data": counts}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _client_fields(body: dict) -> dict:
    return {key: body[key] for key in CLIENT_FIELDS if key in body}


def _validate(payload: dict, *, creating: bool) -> str | None:
    if creating:
        for field in REQUIRED_FIELDS:
            if not payload.get(field):
                return _("{field} is missing", field=field)

    if "visible" in payload and not isinstance(payload["visible"], list):
        return _("visible must be a list of content types")

    return None


def _one_image(incoming) -> tuple[object | None, str | None]:
    """The single thumbnail upload, if any.

    A view has one image. The original checked the count and the *claimed* type
    - a mimetype the client sent, or an extension - and only found out what the
    bytes really were after handing them to the image processor.
    """
    if not incoming:
        return None, None
    if len(incoming) > 1:
        return None, _("A view can only have one file")

    upload = incoming[0]
    if not filestore.is_allowed(upload.filename or "", IMAGE_EXTENSIONS):
        return None, _("Only image files are allowed for views")

    return upload, None


#: Extensions a view thumbnail may have. Narrower than the record allowlist:
#: this is a card image, not archival material.
IMAGE_EXTENSIONS = {"jpg", "jpeg", "jfif", "png", "gif", "tif", "tiff", "heic", "bmp", "webp"}


def _derive_thumbnail(attached: list, user: str) -> str | None:
    """Produce the uploaded thumbnail's web versions. Returns an error message.

    **Attaching the file is not enough to make it visible.** `_thumbnail` serves
    ``<web_files>/<path>_medium.jpg``, which only exists once the file has been
    processed - so a view whose image was merely stored renders with no image at
    all, silently, because the read path treats a missing derivative as "not
    ready yet" rather than an error.

    Done here and now rather than queued, which is deliberate and is what the
    legacy service did too: the operator is looking at the form, and a thumbnail
    that appears some seconds later - or not at all, if no worker is running -
    reads as the upload having failed. Nothing else about a view is asynchronous.

    The two automatic paths in `filesProcessing` cannot cover this: both select
    *resources* by content type, and a view is not a resource.
    """
    from archihub.plugins.framework import interop

    if not attached:
        return None

    record_id = attached[0].get("id")
    record = _mongo().get_record(
        RECORDS_COLLECTION,
        {"_id": _to_object_id(str(record_id))},
        fields={"mime": 1, "filepath": 1},
    )
    if not record:
        return _("Record does not exist")

    try:
        interop.derive_web_versions(record)
    except interop.CapabilityUnavailable as exc:
        # An instance with filesProcessing switched off. Say so plainly: the
        # view would otherwise be created with an image that can never render.
        logger.warning("Cannot derive a view thumbnail: %s", exc)
        return _("File processing failed for image")
    except Exception:
        logger.exception("Failed to derive the thumbnail for record %s", record_id)
        return _("File processing failed for image")

    # Re-read rather than trusting the return value: the contract that matters is
    # what the read path will find, and only a stored `type == "image"` makes
    # `_thumbnail` serve anything.
    processed = _mongo().get_record(
        RECORDS_COLLECTION,
        {"_id": _to_object_id(str(record_id))},
        fields={"processing.fileProcessing.type": 1},
    )
    kind = (((processed or {}).get("processing") or {}).get("fileProcessing") or {}).get("type")
    if kind != "image":
        return _("File processing failed for image")

    return None


def create(body: dict, user: str, incoming: list | None = None) -> tuple[dict, int]:
    """Create a view, optionally with its thumbnail."""
    from archihub.api.records import storage

    payload = _client_fields(body)
    message = _validate(payload, creating=True)
    if message:
        return {"msg": message}, 400

    upload, message = _one_image(incoming)
    if message:
        return {"msg": message}, 400

    if _mongo().get_record(COLLECTION, {"slug": payload["slug"]}, fields={"_id": 1}):
        # The slug is how the public route addresses a view, so two views
        # sharing one makes which is served arbitrary. The original allowed it.
        return {"msg": _("A view with that slug already exists")}, 409

    payload["filesObj"] = []
    inserted = _mongo().insert_record(COLLECTION, payload)
    view_id = str(inserted.inserted_id)

    if upload is not None:
        try:
            attached = storage.attach_files(
                view_id,
                [upload],
                user,
                resource={"_id": inserted.inserted_id, "post_type": VIEW_POST_TYPE, "parents": []},
            )
        except Exception:
            # The view was inserted first so the file has a real parent id to be
            # attached to; if storing then fails, the view goes with it rather
            # than being left behind empty.
            _mongo().delete_record(COLLECTION, {"_id": inserted.inserted_id})
            raise
        message = _derive_thumbnail(attached, user)
        if message:
            # Undo the whole thing. A view carrying an image that can never
            # render is worse than no view: the operator sees it succeed and
            # only later notices a blank card, with nothing to act on.
            storage.detach_from_parent(attached[0].get("id"), view_id, user)
            _mongo().delete_record(COLLECTION, {"_id": inserted.inserted_id})
            return {"msg": message}, 500

        _mongo().update_record(COLLECTION, {"_id": inserted.inserted_id}, {"filesObj": attached[:1]})

    _audit(user, "view_create", {"data": {"id": view_id, "name": payload.get("name")}})
    return {"msg": _("View created successfully"), "id": view_id}, 201


def update(view_id: str, body: dict, user: str, incoming: list | None = None) -> tuple[dict, int]:
    """Edit a view, optionally replacing its thumbnail."""
    from archihub.api.records import storage

    object_id = _to_object_id(view_id)
    if object_id is None:
        return {"msg": _("View not found")}, 404

    view = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"filesObj": 1, "slug": 1})
    if not view:
        return {"msg": _("View not found")}, 404

    payload = _client_fields(body)
    message = _validate(payload, creating=False)
    if message:
        return {"msg": message}, 400

    upload, message = _one_image(incoming)
    if message:
        return {"msg": message}, 400

    if payload.get("slug") and payload["slug"] != view.get("slug"):
        clash = _mongo().get_record(COLLECTION, {"slug": payload["slug"]}, fields={"_id": 1})
        if clash:
            return {"msg": _("A view with that slug already exists")}, 409

    if upload is not None:
        _detach_existing(view, view_id, user)
        attached = storage.attach_files(
            view_id,
            [upload],
            user,
            resource={"_id": object_id, "post_type": VIEW_POST_TYPE, "parents": []},
        )
        message = _derive_thumbnail(attached, user)
        if message:
            # The previous thumbnail was already detached, so there is nothing
            # coherent to roll back to; refuse before writing the new reference
            # rather than pointing the view at an image that cannot render.
            storage.detach_from_parent(attached[0].get("id"), view_id, user)
            return {"msg": message}, 500

        payload["filesObj"] = attached[:1]

    if not payload:
        return {"msg": _("Nothing to update")}, 400

    _mongo().update_record(COLLECTION, {"_id": object_id}, payload)
    _audit(
        user,
        "view_update",
        {"data": {"id": view_id, "updated_fields": sorted(payload), "name": payload.get("name", "")}},
    )
    return {"msg": _("View updated successfully")}, 200


def delete(view_id: str, user: str) -> tuple[dict, int]:
    """Remove a view and retire the thumbnail it owned."""
    object_id = _to_object_id(view_id)
    if object_id is None:
        return {"msg": _("View not found")}, 404

    view = _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"filesObj": 1, "name": 1})
    if not view:
        # The original deleted nothing and reported success, so a stale id in
        # the interface looked like it had worked.
        return {"msg": _("View not found")}, 404

    _detach_existing(view, view_id, user)
    _mongo().delete_record(COLLECTION, {"_id": object_id})

    _audit(user, "view_delete", {"data": {"id": view_id, "name": view.get("name", "")}})
    return {"msg": _("View deleted successfully")}, 200


def _detach_existing(view: dict, view_id: str, user: str) -> None:
    """Unhook the view's current thumbnail records from it."""
    from archihub.api.records.storage import detach_from_parent

    for entry in view.get("filesObj") or []:
        if isinstance(entry, dict) and entry.get("id"):
            try:
                detach_from_parent(entry["id"], view_id, user)
            except Exception:
                logger.exception("Could not detach thumbnail %s from view %s", entry["id"], view_id)


def _audit(user: str | None, action: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, action, details)
