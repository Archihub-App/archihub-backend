"""Targeted edits to an existing resource.

Three services that change part of a resource without going through the full
create/update path, ported from ``app/api/resources/services.py``:

* :func:`update_files_order`     - reorder the files attached to a resource
* :func:`update_granular`        - set one text field across a file's parents
* :func:`change_post_type`       - see its docstring; it does not do that

They are grouped here because they share a shape the main write path does not:
each touches a single named field, so none of them needs the metadata
validation, parent validation or file handling that ``create``/``update`` do.
That is also what makes them portable ahead of ``records``.

AUTHORISATION. The three legacy routes each invented their own rule, and none
checked everything: one looked only at access rights, one only at the content
type's ``editRoles``, one at ownership plus roles. The pieces now live in
``access.py`` and are combined explicitly per route, with the reasoning stated
at each site.
"""

from __future__ import annotations

import datetime
import logging

from bson.objectid import ObjectId

from archihub.api.resources import access
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "resources"
RECORDS_COLLECTION = "records"

#: Only free-text fields can be set through the granular route: it takes a
#: plain string, and anything else needs the full validator.
GRANULAR_FIELD_TYPES = ("text", "text-area")

MSG_UNAUTHORIZED = "You don't have the required authorization"


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


def _denied() -> tuple[dict, int]:
    return {"msg": _(MSG_UNAUTHORIZED)}, LEGACY_ROLE_FAILURE_STATUS


# ---------------------------------------------------------------------------
# File order
# ---------------------------------------------------------------------------


def update_files_order(resource_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Move files to new positions within a resource, then renumber.

    The client sends only the files it moved; everything else keeps its relative
    order and is renumbered from zero so the stored sequence stays dense.

    Authorisation is the caller's read access plus the content type's
    ``editRoles``. The original checked access rights only - so a type that
    named exactly who may edit it had that ignored on this route. Ownership is
    deliberately *not* required: reordering someone else's files is ordinary
    editorial work, and the route is already restricted to editors.
    """
    from archihub.api.users.services import has_role

    object_id = _to_object_id(resource_id)
    if object_id is None:
        return {"msg": _("Resource does not exist")}, 404

    resource = _mongo().get_record(
        COLLECTION,
        {"_id": object_id},
        fields={"filesObj": 1, "accessRights": 1, "parents": 1, "post_type": 1},
    )
    # Before anything is read off it. The original resolved access rights first,
    # which raised for a missing resource and produced a 500 where 404 was
    # documented.
    if not resource:
        return {"msg": _("Resource does not exist")}, 404

    is_admin = has_role(user, "admin")
    if not access.may_view_resource(user, resource, is_admin) or not access.holds_edit_role(
        user, resource.get("post_type"), is_admin
    ):
        logger.info("Denied %s a file reorder on resource %s", user, resource_id)
        return _denied()

    files = resource.get("filesObj") or []
    reordered = reorder(files, body.get("files") or [])

    _mongo().update_record(
        COLLECTION,
        {"_id": object_id},
        {"filesObj": reordered, "updatedAt": _now(), "updatedBy": user},
    )

    _audit(user, "resource_files_order_update", {"resource": resource_id, "files_order": body.get("files")})

    return {"msg": _("Files order updated")}, 200


def reorder(files: list[dict], moves: list[dict]) -> list[dict]:
    """Apply ``moves`` to ``files`` and renumber the result from zero.

    Pure, so the ordering rule can be reasoned about without a database.

    Entries in either list that carry no usable ``id`` are skipped rather than
    subscripted - the original indexed straight into them, so one malformed
    entry took the whole request down with a ``KeyError`` whose raw text became
    the error message.
    """
    ordered = sorted(
        (dict(f) for f in files if isinstance(f, dict) and f.get("id")),
        key=lambda f: f.get("order", float("inf")),
    )
    by_id = {f["id"]: f for f in ordered}

    targets: list[tuple[int, dict]] = []
    for move in moves:
        if not isinstance(move, dict):
            continue
        file_id = move.get("id")
        position = move.get("order")
        if file_id in by_id and isinstance(position, int) and not isinstance(position, bool):
            targets.append((position, by_id[file_id]))

    moved_ids = {f["id"] for _pos, f in targets}
    remaining = [f for f in ordered if f["id"] not in moved_ids]

    # Ascending, so each insertion index is interpreted against a list that
    # already contains every earlier-positioned item.
    targets.sort(key=lambda pair: pair[0])
    for position, moved in targets:
        remaining.insert(min(max(0, position), len(remaining)), moved)

    for index, entry in enumerate(remaining):
        entry["order"] = index

    return remaining


# ---------------------------------------------------------------------------
# Granular metadata edit
# ---------------------------------------------------------------------------


def update_granular(record_id: str, metadata_path: str, value, user: str, concat: bool = False):
    """Set one text field on every resource a file belongs to.

    Despite living under ``/resources``, the id is a **record** (a file): the
    transcription and OCR tools work file-by-file and write their result up into
    the catalogue entries that file belongs to.

    Partial success is success. A file can hang off several resources and the
    caller may be entitled to edit only some of them; updating those and
    reporting the count is more useful than refusing the whole request.
    """
    if not metadata_path or not isinstance(metadata_path, str):
        return {"msg": _("Metadata path is required")}, 400
    if not isinstance(value, str):
        return {"msg": _("Value must be a string")}, 400

    object_id = _to_object_id(record_id)
    if object_id is None:
        return {"msg": _("Record does not exist")}, 404

    record = _mongo().get_record(RECORDS_COLLECTION, {"_id": object_id}, fields={"parent": 1})
    if not record:
        return {"msg": _("Record does not exist")}, 404

    parents = record.get("parent") or []
    if isinstance(parents, dict):
        parents = [parents]
    if not parents:
        return {"msg": _("Record does not have parent resources")}, 404

    updated: list[dict] = []
    for parent in parents:
        if not isinstance(parent, dict) or "id" not in parent:
            continue

        payload, status_code = update_resource_granular(
            parent["id"], metadata_path, value, user, concat=concat, log_action=False
        )
        if status_code == 200:
            updated.append({"id": payload.get("id"), "post_type": payload.get("post_type")})

    if not updated:
        return {
            "msg": _("No parent resource could be updated (authorization, schema, or not found)")
        }, 400

    _audit(
        user,
        "resource_granular_update",
        {
            "record": record_id,
            "metadataPath": metadata_path,
            "concat": concat,
            "updatedResources": updated,
        },
    )

    return {"msg": _("Resource updated successfully"), "updated": len(updated), "resources": updated}, 200


def update_resource_granular(
    resource_id: str,
    metadata_path: str,
    value,
    user: str,
    concat: bool = False,
    log_action: bool = True,
) -> tuple[dict, int]:
    """Set one text field on one resource.

    THE FIELD PATH IS AN ALLOWLIST, not a free path: it must name a field the
    content type's form actually declares, of a free-text type. That is what
    keeps a caller from writing to arbitrary parts of the document through a
    route that takes a dotted path from the request body - so keep the schema
    lookup ahead of the write if this is ever refactored.

    Authorisation is the strictest of the three rules in this module, and it is
    the original's: ownership (creator, ``super_editor`` or admin), *and* the
    content type's ``editRoles``, *and* the publisher role if the resource is
    already published. Access rights are added on top - the original omitted
    them here, as it did on every write path.
    """
    from archihub.api.resources.validation import get_value_by_path, set_value_by_path
    from archihub.api.types.services import get_metadata
    from archihub.api.users.services import has_role

    if not metadata_path or not isinstance(metadata_path, str):
        return {"msg": _("Metadata path is required")}, 400
    if not isinstance(value, str):
        return {"msg": _("Value must be a string")}, 400

    object_id = _to_object_id(resource_id)
    if object_id is None:
        return {"msg": _("Resource does not exist")}, 404

    resource = _mongo().get_record(
        COLLECTION,
        {"_id": object_id},
        fields={
            "metadata": 1, "post_type": 1, "createdBy": 1, "status": 1,
            "accessRights": 1, "parents": 1,
        },
    )
    if not resource:
        return {"msg": _("Resource does not exist")}, 404

    denied = _refuse_granular_edit(user, resource, has_role(user, "admin"))
    if denied is not None:
        return denied

    post_type = resource.get("post_type")
    metadata = get_metadata(post_type)
    fields = (metadata or {}).get("fields") or []
    declared = next((f for f in fields if f.get("destiny") == metadata_path), None)

    if not declared or declared.get("type") not in GRANULAR_FIELD_TYPES:
        return {"msg": _("Field is not text/text-area or not found in schema")}, 400

    body = {
        "_id": str(resource_id),
        "post_type": post_type,
        "metadata": resource.get("metadata") or {},
        "status": resource.get("status") or "draft",
    }

    new_value = value
    if concat:
        current = get_value_by_path(body, metadata_path)
        current_text = current.strip() if isinstance(current, str) else ""
        incoming = value.strip()
        new_value = f"{current_text} {incoming}" if current_text and incoming else (current_text or incoming)

    set_value_by_path(body, metadata_path, new_value)
    body = _call_hook("resource_pre_update", body)

    final_value = get_value_by_path(body, metadata_path)
    if not isinstance(final_value, str):
        # A `resource_pre_update` hook can rewrite the body. The original ran
        # the text validator here and let it raise, which the outer handler
        # turned into a 500 - a validation failure reported as a server fault.
        return {"msg": _("The field {label} must be of type string", label=declared.get("label") or metadata_path)}, 400

    update = {
        "post_type": body["post_type"],
        "metadata": body["metadata"],
        "updatedAt": _now(),
        "updatedBy": user or "system",
    }
    _mongo().update_record(COLLECTION, {"_id": object_id}, update)
    _call_hook("resource_update", {**update, "_id": str(resource_id)})

    if log_action:
        _audit(
            user,
            "resource_granular_update",
            {"resource": str(resource_id), "metadataPath": metadata_path, "concat": concat},
        )

    return {
        "msg": _("Resource updated successfully"),
        "id": str(resource_id),
        "post_type": post_type,
    }, 200


def _refuse_granular_edit(user: str, resource: dict, is_admin: bool) -> tuple[dict, int] | None:
    """All four gates, in one place. ``None`` means allowed."""
    from archihub.api.users.services import has_role

    if is_admin:
        return None

    if not access.may_view_resource(user, resource, is_admin):
        return _denied()
    if not access.owns_or_supervises(user, resource, is_admin):
        return _denied()
    if not access.holds_edit_role(user, resource.get("post_type"), is_admin):
        return _denied()
    if resource.get("status") == "published" and not has_role(user, "publisher"):
        return _denied()

    return None


# ---------------------------------------------------------------------------
# change-post-type
# ---------------------------------------------------------------------------


def change_post_type(body: dict, user: str) -> tuple[dict, int]:
    """PERMISSION CHECK ONLY. This does not change a resource's content type.

    Ported deliberately as the stub it is. The legacy service checks the
    caller's edit roles over the resource's *current* type, then returns
    ``{'msg': 'Post type changed'}`` without writing anything - the
    implementation was never finished, and the Swagger docstring already says
    so. See BACKEND_FINDINGS F25.

    The success message is preserved because the frontend shows it. Actually
    moving a resource between content types is a real piece of work - the two
    types' forms declare different fields, so it needs a mapping decision for
    every field that does not exist on the target - and inventing that here
    would be a new feature wearing a port's clothes.
    """
    from archihub.api.users.services import has_role

    resource_id = body.get("id")
    if not resource_id:
        return {"msg": _("Resource does not exist")}, 400

    object_id = _to_object_id(resource_id)
    resource = (
        _mongo().get_record(COLLECTION, {"_id": object_id}, fields={"post_type": 1})
        if object_id is not None
        else None
    )
    if not resource:
        return {"msg": _("Resource does not exist")}, 404

    if not access.holds_edit_role(user, resource.get("post_type"), has_role(user, "admin")):
        return _denied()

    logger.info(
        "change-post-type called for resource %s by %s; it verifies permissions only",
        resource_id,
        user,
    )
    return {"msg": _("Post type changed")}, 200


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _call_hook(name: str, payload: dict) -> dict:
    from archihub.core.hooks import get_hook_handler

    try:
        result = get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("%s hook failed", name)
        return payload
    return result if isinstance(result, dict) else payload


def _audit(user: str, action: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, action, details)
