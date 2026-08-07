"""Attaching files to a resource.

Port of ``create`` in ``app/api/records/services.py:300`` - the function every
upload in the system goes through, and the one the resources write path is
blocked on.

WHAT IT DOES. For each incoming file: store it, hash it, and either create a
record or - if a record with that hash already exists - attach the existing one
to this resource as an additional parent. Archives receive the same scan against
several catalogue entries routinely, and storing it once is the point.

THE DEDUPLICATION IS BY CONTENT, NOT BY NAME. Two files with the same bytes and
different names are one record. That is deliberate and preserved.

All storage goes through ``archihub.core.files``; see its module docstring for
the upload ceiling, the durability rule and why the client's filename never
reaches disk.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

from bson.objectid import ObjectId

from archihub.core import files as filestore
from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "records"

#: What may be uploaded. Carried over verbatim from the legacy
#: ``ALLOWED_EXTENSIONS`` - narrowing it would reject files existing
#: deployments accept, and widening it is a decision for whoever runs the
#: instance, not a side effect of a port.
ALLOWED_EXTENSIONS = frozenset({
    "txt", "pdf", "png", "jpg", "jpeg", "gif", "oga", "ogg", "ogv", "tif", "tiff", "heic",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "zip", "rar", "7z", "mp4",
    "mp3", "wav", "avi", "mkv", "flv", "mov", "wmv", "m4a", "mxf", "cr2", "arw", "mts",
    "nef", "json", "html", "wma", "aac", "flac",
})

STATUS_UPLOADED = "uploaded"
STATUS_PROCESSED = "processed"
STATUS_DELETED = "deleted"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@dataclass
class IncomingFile:
    """One file to attach, from either of the two sources that exist.

    * an upload - ``stream`` is the client's bytes, ``filename`` their name;
    * a file a plugin already produced - ``path`` points at it on disk.

    The legacy function took a list that could hold either a Werkzeug
    ``FileStorage`` or a dict, and told them apart with ``type(f) is not dict``
    scattered through the body. One shape with two constructors is the same
    information without the branching.
    """

    filename: str
    #: A readable binary file-like object; `UploadFile.file` in practice.
    stream: object = None
    path: str | None = None
    tag: str = "file"
    order: int | None = None

    @classmethod
    def from_upload(cls, upload, tag: str = "file", order: int | None = None) -> "IncomingFile":
        """From a Starlette ``UploadFile``."""
        return cls(filename=upload.filename or "", stream=upload.file, tag=tag, order=order)

    @classmethod
    def from_path(cls, path, filename: str | None = None, tag: str = "file", order: int | None = None):
        resolved = Path(path)
        return cls(filename=filename or resolved.name, path=str(resolved), tag=tag, order=order)


@dataclass(frozen=True)
class AttachedFile:
    """One entry of a resource's ``filesObj``."""

    id: str
    tag: str
    order: int | None = None

    def as_dict(self) -> dict:
        entry = {"id": self.id, "tag": self.tag}
        if self.order is not None:
            entry["order"] = self.order
        return entry


class UnsupportedFileType(Exception):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(_("File type not allowed"))


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def get_by_hash(content_hash: str) -> dict | None:
    """The existing record for this content, if there is one."""
    if not content_hash:
        return None
    return _mongo().get_record(COLLECTION, {"hash": content_hash})


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------


def attach_files(
    resource_id: str,
    incoming: list[IncomingFile],
    user: str | None,
    *,
    resource: dict | None = None,
) -> list[dict]:
    """Store each file and return the entries to record on the resource.

    ``resource`` may be supplied by a caller that has already loaded it (the
    create path does, since the resource does not exist in the database yet).
    """
    parent = resource if resource is not None else _load_resource(resource_id)

    attached: list[AttachedFile] = []
    for item in incoming:
        attached.append(_attach_one(resource_id, parent, item, user))

    return [entry.as_dict() for entry in attached]


def _load_resource(resource_id: str) -> dict:
    object_id = None
    try:
        object_id = ObjectId(resource_id)
    except Exception:
        pass

    resource = (
        _mongo().get_record("resources", {"_id": object_id}, fields={"parents": 1, "post_type": 1})
        if object_id is not None
        else None
    )
    if not resource:
        raise ValueError(_("Resource does not exist"))
    return resource


def _attach_one(resource_id: str, resource: dict, item: IncomingFile, user: str | None) -> AttachedFile:
    safe_name = filestore.secure_name(item.filename)
    if not filestore.is_allowed(safe_name, ALLOWED_EXTENSIONS):
        raise UnsupportedFileType(safe_name)

    stored = _store(item, safe_name)

    existing = get_by_hash(stored.sha256)
    if existing:
        # Same bytes already held. Drop the copy just written and give the
        # existing record another parent.
        filestore.remove_quietly(stored.path)
        _add_parent(existing, resource_id, resource, user)
        return AttachedFile(id=str(existing["_id"]), tag=item.tag, order=item.order)

    record_id = _insert(stored, resource_id, resource, user, safe_name)
    return AttachedFile(id=record_id, tag=item.tag, order=item.order)


def _store(item: IncomingFile, safe_name: str) -> filestore.StoredFile:
    directory = filestore.dated_directory(get_settings().original_files_path)

    if item.path is not None:
        return filestore.store_existing_file(item.path, directory, safe_name)

    if item.stream is None:
        raise ValueError(_("The file has no content"))

    return filestore.store_upload(item.stream, directory, safe_name)


def _relative_path(stored: filestore.StoredFile) -> str:
    """The path as stored on the record: relative to the originals root.

    Absolute paths in the database would break the moment the media root moves,
    which it does between a local install and a compose deployment.
    """
    root = Path(get_settings().original_files_path)
    try:
        return str(stored.path.relative_to(root))
    except ValueError:
        logger.warning("Stored file %s is outside the originals root %s", stored.path, root)
        return stored.path.name


def _insert(
    stored: filestore.StoredFile,
    resource_id: str,
    resource: dict,
    user: str | None,
    safe_name: str,
) -> str:
    record = {
        "name": safe_name,
        "hash": stored.sha256,
        "size": stored.size,
        "filepath": _relative_path(stored),
        # The real content type, not one guessed from the extension. The
        # extension allowlist above says what the uploader claimed; this says
        # what the bytes are. See BACKEND_FINDINGS S14.
        "mime": filestore.sniff_media_type(stored.path)
        or _media_type_from_name(safe_name),
        "parent": [{"id": resource_id, "post_type": resource.get("post_type")}],
        "parents": list(resource.get("parents") or []),
        "status": STATUS_UPLOADED,
        "favCount": 0,
        "updatedBy": user or "system",
        "updatedAt": _now(),
    }

    inserted = _mongo().insert_record(COLLECTION, record)
    record_id = str(inserted.inserted_id)

    _audit(user, "record_create", {
        "record": {
            "name": record["name"],
            "hash": record["hash"],
            "size": record["size"],
            "filepath": record["filepath"],
        }
    })
    _call_hook("record_create", {**record, "_id": record_id})

    return record_id


def _media_type_from_name(filename: str) -> str:
    from archihub.core.responses import guess_media_type

    return guess_media_type(filename)


def _add_parent(record: dict, resource_id: str, resource: dict, user: str | None) -> None:
    """Give an existing record another owner.

    Also revives it: a record whose only previous parent was deleted is marked
    ``deleted``, and re-uploading the same file is exactly how someone restores
    it. Its status returns to ``processed`` if derivatives survived, otherwise
    ``uploaded``.
    """
    update = {
        "parent": _merge_by_id(record.get("parent"), [
            {"id": resource_id, "post_type": resource.get("post_type")}
        ]),
        "parents": _merge_by_id(record.get("parents"), resource.get("parents")),
        "updatedBy": user or "system",
        "updatedAt": _now(),
    }

    if record.get("status") == STATUS_DELETED:
        derived = ((record.get("processing") or {}).get("files")) or []
        update["status"] = STATUS_PROCESSED if derived else STATUS_UPLOADED

    _mongo().update_record(COLLECTION, {"_id": record["_id"]}, update)

    _audit(user, "record_update", {"record": str(record["_id"])})
    _call_hook("record_update_parent", {**update, "_id": str(record["_id"])})


def _merge_by_id(*groups) -> list[dict]:
    """Union of parent lists, first occurrence of each id winning.

    ORDER IS STABLE HERE, and it was not before. The original built a ``set`` of
    ids and then rebuilt the list from it, so the stored parent order was
    whatever the set happened to iterate - different between runs, since string
    hashing is salted per process. It also crashed on any entry without an
    ``id``.
    """
    merged: list[dict] = []
    seen: set[str] = set()

    for group in groups:
        for entry in group or []:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            if not entry_id or entry_id in seen:
                continue
            seen.add(entry_id)
            merged.append(entry)

    return merged


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
        # The file is stored and the record written; a failing side effect must
        # not undo that or fail the upload.
        logger.exception("%s hook failed", name)
