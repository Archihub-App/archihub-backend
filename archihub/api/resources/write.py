"""Creating, updating, deleting and restoring resources.

The last and largest piece of the resources domain, ported from
``app/api/resources/services.py`` (``create``, ``update_by_id``,
``delete_by_id``, ``restore_by_id`` and the parent/relation maintenance around
them).

It assembles pieces that already exist rather than restating them:

* ``hierarchy.validate_parent``   - where the resource sits, and cycle refusal
* ``validation.validate_fields``  - what its metadata may contain
* ``records.storage.attach_files``- storing and deduplicating its files
* ``access``                      - who may do any of this

TWO STRUCTURAL CHANGES FROM THE ORIGINAL, both about batches:

1. **Permission is checked for every id before anything is written.** The
   original looped, and returned on the first refusal - having already deleted
   or restored the ids before it. A caller who selected twelve resources and
   lacked rights on the ninth got eight of them deleted and an error saying
   nothing had happened.

2. **Reciprocal relation updates happen after the insert, not before.** The
   original called ``update_relations_children`` before the resource had an id,
   then dereferenced ``body['_id']``. See BACKEND_FINDINGS F22.
"""

from __future__ import annotations

import datetime
import logging

from bson.objectid import ObjectId

from archihub.api.resources import access, hierarchy, validation
from archihub.core.errors import BusinessError
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "resources"

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_DELETED = "deleted"

#: Fields a client may set. Everything else on a resource is server-owned:
#: ``createdBy``/``createdAt`` record who made it, ``filesObj`` is written by
#: the file pipeline, ``favCount`` by the favourites routes, ``parents`` is
#: derived from ``parent``. The original built its update from whatever the
#: request contained, which is how the article route became a way to change all
#: of them (S17); an allowlist is the shape that does not have that failure
#: mode.
CLIENT_FIELDS = (
    "post_type", "metadata", "status", "accessRights", "parent", "ident", "atlasWiki",
)

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
# Authorisation
# ---------------------------------------------------------------------------


def may_publish(user: str, is_admin: bool) -> bool:
    """Publishing is its own privilege, separate from editing."""
    from archihub.api.users.services import has_role

    return is_admin or has_role(user, "publisher")


def may_create(user: str, post_type: str, is_admin: bool) -> bool:
    """Creating requires the content type's edit roles, where it declares any."""
    return access.holds_edit_role(user, post_type, is_admin)


def may_modify(user: str, resource: dict, is_admin: bool) -> bool:
    """Whether this caller may change or remove an existing resource.

    All three gates, which the original applied in three different partial
    combinations depending on the route: readable, the content type's edit
    roles, and ownership.
    """
    if is_admin:
        return True
    if not access.may_view_resource(user, resource, is_admin):
        return False
    if not access.holds_edit_role(user, resource.get("post_type"), is_admin):
        return False
    return access.owns_or_supervises(user, resource, is_admin)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create(body: dict, user: str, incoming_files=None) -> tuple[dict, int]:
    """Create a resource and attach its files.

    ORDER MATTERS AND IS DELIBERATE: validate everything first, then store the
    files, then insert, then wire up reciprocal relations. Storing before
    validating would leave orphaned bytes behind whenever a form was rejected -
    which is the common case while someone is still filling it in.
    """
    from archihub.api.types.services import get_metadata
    from archihub.api.users.services import has_role

    is_admin = has_role(user, "admin")

    post_type = body.get("post_type")
    if not post_type:
        return {"msg": _("The content type is required")}, 400
    if "metadata" not in body:
        return {"msg": _("The metadata is required")}, 400

    if not may_create(user, post_type, is_admin):
        logger.info("Denied %s creation of a %s", user, post_type)
        return _denied()

    status = body.get("status") or STATUS_DRAFT
    if status == STATUS_PUBLISHED and not may_publish(user, is_admin):
        return _denied()

    payload = {field: body[field] for field in CLIENT_FIELDS if field in body}
    payload["status"] = status

    try:
        payload = hierarchy.validate_parent(payload)
    except BusinessError as exc:
        return {"msg": exc.message}, exc.status_code

    payload = _call_hook("resource_pre_create", payload)

    metadata = get_metadata(post_type)
    payload, errors = validation.validate_fields(payload, metadata)
    if errors:
        return {"msg": _("Error validating fields"), "errors": errors}, 400

    file_errors = validation.validate_files(_tags_of(incoming_files), metadata)
    if file_errors:
        return {"msg": _("Error validating files"), "errors": file_errors}, 400

    payload.setdefault("ident", "ident")
    payload["createdBy"] = user
    payload["updatedBy"] = user
    payload["createdAt"] = _now()
    payload["updatedAt"] = _now()
    payload["filesObj"] = []
    payload["favCount"] = 0

    # INSERT BEFORE STORING THE FILES. A record's `parent` is the resource it
    # belongs to, so the resource has to have an id before its files can name
    # it - attaching first would file every record under a placeholder, and
    # nothing would ever find them again.
    inserted = _mongo().insert_record(COLLECTION, payload)
    resource_id = str(inserted.inserted_id)

    try:
        attached = _attach(payload, incoming_files, user, resource_id)
    except Exception:
        # Storage refused the upload (too large, wrong type). Take the resource
        # back out rather than leaving an empty one behind for a save the user
        # was told had failed.
        _mongo().delete_record(COLLECTION, {"_id": inserted.inserted_id})
        raise

    if attached:
        _mongo().update_record(
            COLLECTION,
            {"_id": inserted.inserted_id},
            {"filesObj": attached, "updatedAt": _now(), "updatedBy": user},
        )
        _call_hook("resource_files_create", {"_id": resource_id, "filesObj": attached})

    # AFTER the insert: the reciprocal update needs this resource's id, and the
    # original ran it before there was one (F22).
    _sync_reciprocal_relations(resource_id, payload, metadata, before={})

    _audit(user, "resource_create", {"resource": {**payload, "_id": resource_id}})
    _call_hook("resource_create", {**payload, "_id": resource_id})

    return {
        "msg": _("Resource created successfully"),
        "id": resource_id,
        "post_type": post_type,
    }, 201


def _tags_of(incoming_files) -> list[dict]:
    return [{"tag": item.tag} for item in (incoming_files or [])]


def _attach(resource: dict, incoming_files, user: str, resource_id: str) -> list[dict]:
    """Store the incoming files and return their attachment entries.

    The resource is passed through rather than re-read: on create it has just
    been inserted, and on update the caller already holds it.
    """
    from archihub.api.records import storage

    if not incoming_files:
        return []

    return storage.attach_files(
        resource_id,
        list(incoming_files),
        user,
        resource={"post_type": resource.get("post_type"), "parents": resource.get("parents") or []},
    )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def update(resource_id: str, body: dict, user: str, incoming_files=None) -> tuple[dict, int]:
    """Replace a resource's metadata, parents and file set."""
    from archihub.api.types.services import get_metadata
    from archihub.api.users.services import has_role

    object_id = _to_object_id(resource_id)
    if object_id is None:
        return {"msg": _("Resource does not exist")}, 404

    existing = _mongo().get_record(COLLECTION, {"_id": object_id})
    if not existing:
        return {"msg": _("Resource does not exist")}, 404

    is_admin = has_role(user, "admin")
    if not may_modify(user, existing, is_admin):
        logger.info("Denied %s an update of resource %s", user, resource_id)
        return _denied()

    post_type = body.get("post_type") or existing.get("post_type")
    status = body.get("status") or existing.get("status") or STATUS_DRAFT
    if status == STATUS_PUBLISHED and not may_publish(user, is_admin):
        return _denied()

    payload = {field: body[field] for field in CLIENT_FIELDS if field in body}
    payload["_id"] = resource_id
    payload["post_type"] = post_type
    payload["status"] = status

    try:
        payload = hierarchy.validate_parent(payload, update=True)
    except BusinessError as exc:
        return {"msg": exc.message}, exc.status_code

    moved = hierarchy.has_changed_parent(resource_id, payload.get("parent"))

    payload = _call_hook("resource_pre_update", payload)

    metadata = get_metadata(post_type)
    payload, errors = validation.validate_fields(payload, metadata)
    if errors:
        return {"msg": _("Error validating fields"), "errors": errors}, 400

    kept = _surviving_files(existing, body.get("deletedFiles") or [])
    file_errors = validation.validate_files([*kept, *_tags_of(incoming_files)], metadata)
    if file_errors:
        return {"msg": _("Error validating files"), "errors": file_errors}, 400

    attached = _attach(payload, incoming_files, user, resource_id)
    files = _apply_order(kept + attached, body.get("updatedFiles") or [])

    payload.pop("_id", None)
    payload["filesObj"] = files
    payload["updatedAt"] = _now()
    payload["updatedBy"] = user or "system"

    # Snapshot the relation fields BEFORE writing. `existing` came out of the
    # database and the write is about to replace the same fields; reading it
    # afterwards would compare the new state against itself and conclude
    # nothing had changed.
    before = _relation_ids(existing, metadata)

    _mongo().update_record(COLLECTION, {"_id": object_id}, payload)

    if moved:
        _rewrite_descendant_ancestry(resource_id, user)

    _sync_reciprocal_relations(resource_id, payload, metadata, before)

    _audit(user, "resource_update", {"resource": {**payload, "_id": resource_id}})
    _call_hook("resource_update", {**payload, "_id": resource_id})

    return {"msg": _("Resource updated successfully")}, 200


def _surviving_files(existing: dict, deleted_ids) -> list[dict]:
    """The resource's current files, minus the ones this request removes.

    De-duplicated by id. The original de-duplicated by comparing whole dicts as
    tuples, so two entries for the same file differing only in ``order`` both
    survived - and the viewer then showed it twice.
    """
    removed = set(deleted_ids or [])
    kept: list[dict] = []
    seen: set[str] = set()

    for entry in existing.get("filesObj") or []:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if not entry_id or entry_id in removed or entry_id in seen:
            continue
        seen.add(entry_id)
        kept.append(dict(entry))

    return kept


def _apply_order(files: list[dict], updates) -> list[dict]:
    positions = {
        u["id"]: u["order"]
        for u in (updates or [])
        if isinstance(u, dict) and u.get("id") and isinstance(u.get("order"), int)
    }
    for entry in files:
        if entry.get("id") in positions:
            entry["order"] = positions[entry["id"]]
    return files


# ---------------------------------------------------------------------------
# Relations and ancestry maintenance
# ---------------------------------------------------------------------------


def _self_relation_fields(metadata: dict, post_type) -> list[str]:
    """Paths of the relation fields that point at this resource's own type."""
    return [
        field["destiny"]
        for field in (metadata or {}).get("fields") or []
        if field.get("type") == "relation"
        and field.get("relation_type") == post_type
        and field.get("destiny")
    ]


def _relation_ids(document: dict | None, metadata: dict) -> dict[str, set[str]]:
    """A snapshot of each self-relation field's referenced ids."""
    if not document:
        return {}
    return {
        path: _ids_at(document, path)
        for path in _self_relation_fields(metadata, document.get("post_type"))
    }


def _sync_reciprocal_relations(
    resource_id: str, payload: dict, metadata: dict, before: dict[str, set[str]]
) -> None:
    """Keep same-type relation fields pointing both ways.

    When a resource names another of its own content type as related, the other
    one should list it back. Runs after the write, so the id exists (F22).
    """
    for destiny in _self_relation_fields(metadata, payload.get("post_type")):
        now = _ids_at(payload, destiny)
        was = before.get(destiny, set())

        for removed in was - now:
            _unlink(removed, destiny, resource_id)
        for added in now - was:
            _link(added, destiny, resource_id, payload.get("post_type"))


def _ids_at(document, path: str) -> set[str]:
    value = validation.get_value_by_path(document or {}, path)
    if not isinstance(value, list):
        return set()
    return {v.get("id") for v in value if isinstance(v, dict) and v.get("id")}


def _link(target_id: str, path: str, resource_id: str, post_type) -> None:
    target = _load(target_id)
    if target is None:
        return

    current = validation.get_value_by_path(target, path) or []
    if any(isinstance(c, dict) and c.get("id") == resource_id for c in current):
        return

    updated = [*current, {"id": resource_id, "post_type": post_type}]
    _write_path(target_id, path, updated)


def _unlink(target_id: str, path: str, resource_id: str) -> None:
    target = _load(target_id)
    if target is None:
        return

    current = validation.get_value_by_path(target, path) or []
    remaining = [c for c in current if not (isinstance(c, dict) and c.get("id") == resource_id)]
    if len(remaining) == len(current):
        return

    _write_path(target_id, path, remaining)


def _load(resource_id: str):
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return None
    return _mongo().get_record(COLLECTION, {"_id": object_id})


def _write_path(resource_id: str, path: str, value) -> None:
    """Write one dotted path, and only that path.

    The original rebuilt the entire target document and wrote it back through
    ``ResourceUpdate``, so a reciprocal relation update rewrote every field of a
    resource nobody had asked to change.
    """
    _mongo().update_record(
        COLLECTION,
        {"_id": _to_object_id(resource_id)},
        {path: value, "updatedAt": _now(), "updatedBy": "system"},
    )


def _rewrite_descendant_ancestry(resource_id: str, user: str) -> None:
    """Recompute ``parents`` for everything filed below a moved resource.

    Bounded by a visited set: the graph is supposed to be acyclic and
    ``validate_parent`` now refuses to create a cycle, but this walks stored
    data that may predate that check.
    """
    seen: set[str] = set()
    queue = [resource_id]

    while queue:
        current = queue.pop()
        if current in seen:
            logger.warning("Cycle while rewriting ancestry below %s", resource_id)
            continue
        seen.add(current)

        for child in hierarchy.direct_children(current):
            child_id = child["id"]
            if child_id in seen:
                continue

            ancestry = hierarchy.ancestors(child_id)
            _mongo().update_record(
                COLLECTION,
                {"_id": _to_object_id(child_id)},
                {"parents": ancestry, "updatedAt": _now(), "updatedBy": user or "system"},
            )
            queue.append(child_id)


# ---------------------------------------------------------------------------
# Delete and restore
# ---------------------------------------------------------------------------


def delete(ids, user: str) -> tuple[dict, int]:
    """Move resources to the recycle bin, with their descendants.

    Nothing is destroyed: ``status`` becomes ``deleted`` and an administrator
    can restore it.
    """
    from archihub.api.users.services import has_role

    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        return {"msg": _("A list of resource ids is required")}, 400

    is_admin = has_role(user, "admin")
    resources, error = _load_all_modifiable(ids, user, is_admin)
    if error is not None:
        return error

    deleted: list[str] = []
    for resource_id, resource in resources:
        _mark_deleted(resource_id, user)
        _cascade_delete(resource_id, user)
        _detach_records(resource, resource_id, user)

        _call_hook("resource_delete", {"_id": resource_id})
        _audit(user, "resource_delete", {"resource": resource_id})
        deleted.append(resource_id)

    return {"msg": _("Resources deleted"), "ids": deleted}, 200


def _load_all_modifiable(ids, user: str, is_admin: bool):
    """Resolve every id and check permission on all of them before writing any.

    THE POINT OF THIS FUNCTION. The original checked and acted in the same loop
    and returned on the first refusal, so a caller who selected twelve resources
    and lacked rights on the ninth got eight of them deleted and an error
    reporting that nothing had happened.
    """
    resolved = []
    for resource_id in ids:
        resource = _load(resource_id)
        if not resource:
            return None, ({"msg": _("Resource does not exist")}, 404)
        if not may_modify(user, resource, is_admin):
            logger.info("Denied %s a bulk operation including resource %s", user, resource_id)
            return None, _denied()
        resolved.append((resource_id, resource))

    return resolved, None


def _mark_deleted(resource_id: str, user: str) -> None:
    _mongo().update_record(
        COLLECTION,
        {"_id": _to_object_id(resource_id)},
        {"status": STATUS_DELETED, "updatedAt": _now(), "updatedBy": user or "system"},
    )


def _cascade_delete(resource_id: str, user: str) -> None:
    """Delete everything filed below, breadth-first with a visited set."""
    seen = {resource_id}
    queue = [resource_id]

    while queue:
        current = queue.pop()
        for child in hierarchy.direct_children(current):
            child_id = child["id"]
            if child_id in seen:
                continue
            seen.add(child_id)

            _mark_deleted(child_id, user)
            _call_hook("resource_delete", {"_id": child_id})
            queue.append(child_id)


def _detach_records(resource: dict, resource_id: str, user: str) -> None:
    """Mark the resource's files deleted where nothing else holds them.

    THE ORIGINAL NEVER DID THIS. It read ``resource['files']``, but the stored
    field is ``filesObj`` - ``files`` is not a field of the ``Resource`` model at
    all, so the condition was always false and the call was dead code. Deleting
    a resource left its records pointing at it, still ``uploaded``, invisible to
    the recycle bin and to any cleanup. See BACKEND_FINDINGS F28.
    """
    from archihub.api.records import storage

    for entry in resource.get("filesObj") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue

        record = _mongo().get_record("records", {"_id": _to_object_id(entry["id"])})
        if not record:
            continue

        remaining = [
            p for p in (record.get("parent") or [])
            if isinstance(p, dict) and p.get("id") != resource_id
        ]

        update = {"parent": remaining, "updatedAt": _now(), "updatedBy": user or "system"}
        # A file held by another resource stays alive there; only the last
        # reference going away retires it.
        if not remaining:
            update["status"] = storage.STATUS_DELETED

        _mongo().update_record("records", {"_id": record["_id"]}, update)


def restore(ids, user: str, recursive: bool = False) -> tuple[dict, int]:
    """Bring resources back out of the recycle bin."""
    from archihub.api.users.services import has_role

    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        return {"msg": _("ids must be an array of string ids")}, 400
    if not isinstance(recursive, bool):
        return {"msg": _("recursive must be a boolean")}, 400

    is_admin = has_role(user, "admin")
    resources, error = _load_all_modifiable(ids, user, is_admin)
    if error is not None:
        return error

    restored: list[str] = []
    for resource_id, resource in resources:
        _restore_one(resource_id, resource, user)
        restored.append(resource_id)

        if recursive:
            restored.extend(_restore_descendants(resource_id, user, set(restored)))

    return {"msg": _("Resources restored"), "ids": restored}, 200


def _restore_one(resource_id: str, resource: dict, user: str) -> None:
    """Restore to draft, never straight to published.

    Something removed from the catalogue should not silently reappear in the
    public one; a publisher decides that separately.
    """
    _mongo().update_record(
        COLLECTION,
        {"_id": _to_object_id(resource_id)},
        {"status": STATUS_DRAFT, "updatedAt": _now(), "updatedBy": user or "system"},
    )

    _restore_records_of(resource, user)
    _call_hook("resource_restore", {"_id": resource_id})
    _audit(user, "resource_restore", {"resource": resource_id})


def _restore_records_of(resource: dict, user: str) -> None:
    from archihub.api.records import storage

    for entry in resource.get("filesObj") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue

        record = _mongo().get_record("records", {"_id": _to_object_id(entry["id"])})
        if not record or record.get("status") != storage.STATUS_DELETED:
            continue

        derived = ((record.get("processing") or {}).get("files")) or []
        _mongo().update_record(
            "records",
            {"_id": record["_id"]},
            {
                "status": storage.STATUS_PROCESSED if derived else storage.STATUS_UPLOADED,
                "updatedAt": _now(),
                "updatedBy": user or "system",
            },
        )


def _restore_descendants(resource_id: str, user: str, already: set[str]) -> list[str]:
    restored: list[str] = []
    seen = set(already) | {resource_id}
    queue = [resource_id]

    while queue:
        current = queue.pop()
        for child in hierarchy.direct_children(current):
            child_id = child["id"]
            if child_id in seen:
                continue
            seen.add(child_id)

            child_resource = _load(child_id)
            if not child_resource or child_resource.get("status") != STATUS_DELETED:
                continue

            _restore_one(child_id, child_resource, user)
            restored.append(child_id)
            queue.append(child_id)

    return restored


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _audit(user: str | None, action: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, action, details)


def _call_hook(name: str, payload: dict) -> dict:
    from archihub.core.hooks import get_hook_handler

    try:
        result = get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("%s hook failed", name)
        return payload
    return result if isinstance(result, dict) else payload
