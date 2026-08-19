"""The files attached to a resource: listing them, counting them, zipping them.

Shared by the authenticated routes and their public mirrors: the only thing that
differs between the two is *which records the caller may see*, and that is one
parameter rather than a second implementation. Two copies of this logic drift,
and they drift precisely on the visibility rule.

THE BULK DOWNLOAD IS WHERE THE CARE IS NEEDED. It combines the two things that
are individually dangerous - a filesystem write and an access decision - and the
public mirror reaches it with no authentication at all. Four rules hold:

* **The requested kind indexes a fixed map and never becomes part of a path.**
  Joining a request value into the archive filename is a file write to wherever
  the caller points it.
* **The archive name is a digest of what goes into it** - the resource, the kind,
  and the ids it contains. So no client string reaches the path, *and* the name
  changes when the contents do; caching on a fixed name serves a stale archive
  for the life of the deployment, long after the files were corrected.
* **A record the caller may not see is excluded, not renamed.** Blanking a
  display name while still writing the file by its stored path puts restricted
  material into an anonymous download.
* **Entry names inside the archive are sanitised.** A stored name containing
  ``..`` writes outside the extraction directory on whoever opens it.
"""

from __future__ import annotations

import hashlib
import logging

from archihub.core import files as filestore
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

RECORDS_COLLECTION = "records"
PAGE_SIZE = 10

#: What a bulk download may ask for, and how each kind resolves to a file. Keys
#: are the wire values; nothing else reaches the filesystem.
DOWNLOAD_KINDS = ("original", "small")

#: Directory under the media root where archives are cached.
ZIP_DIRECTORY = "zipfiles"
ZIP_PREFIX = "bundle-"

#: How long a cached archive may sit before the next request removes it.
STALE_ZIP_SECONDS = 24 * 60 * 60

#: Placeholder entry the listing appends when images are grouped into a gallery.
GALLERY_ID = "imgGallery"


class DownloadRefused(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


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
# Ordering
# ---------------------------------------------------------------------------


def ordered_file_entries(resource: dict) -> list[dict]:
    """A resource's ``filesObj`` in display order, with gaps filled.

    Entries may carry an explicit ``order``; those that do not are assigned the
    lowest unused position, so a freshly attached file lands at the end rather
    than jumping to the front of a sequence someone curated.
    """
    entries = [
        dict(entry)
        for entry in (resource.get("filesObj") or [])
        if isinstance(entry, dict) and entry.get("id")
    ]

    used = {e["order"] for e in entries if isinstance(e.get("order"), int)}
    candidate = 0
    for entry in entries:
        if not isinstance(entry.get("order"), int):
            while candidate in used:
                candidate += 1
            entry["order"] = candidate
            used.add(candidate)

    return sorted(entries, key=lambda e: e["order"])


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_files(
    resource: dict,
    user: str | None,
    page: int = 0,
    group_images: bool = False,
    *,
    public: bool = False,
) -> tuple[dict, int]:
    """One page of a resource's files, in display order.

    ``public=True`` fixes the caller's rights at "none", which is the only
    difference between this and the authenticated listing.
    """
    entries = ordered_file_entries(resource)
    if not entries:
        return {"data": [], "total": 0}, 200

    page = max(int(page or 0), 0)
    window = entries[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]

    records = _load_records([e["id"] for e in window], group_images)
    by_id = {str(r["_id"]): r for r in records}

    data = []
    for entry in window:
        record = by_id.get(str(entry["id"]))
        if record is None:
            continue
        data.append(_describe(record, entry, user, public=public))

    total = len(entries)
    if group_images:
        gallery = _gallery_entry(entries)
        if gallery is not None:
            data.append(gallery)
            # The grouped images collapse into a single row.
            total = total - gallery["_imageCount"] + 1
            gallery.pop("_imageCount")

    return {"data": data, "total": total}, 200


def _load_records(ids: list[str], group_images: bool) -> list[dict]:
    object_ids = [oid for oid in (_object_id(i) for i in ids) if oid is not None]
    if not object_ids:
        return []

    filters: dict = {"_id": {"$in": object_ids}}
    if group_images:
        filters["$or"] = [
            {"processing.fileProcessing.type": {"$exists": False}},
            {"processing.fileProcessing.type": {"$ne": "image"}},
        ]

    return list(
        _mongo().get_all_records(
            RECORDS_COLLECTION,
            filters,
            fields={
                "name": 1, "size": 1, "accessRights": 1, "displayName": 1,
                "processing.fileProcessing.type": 1, "hash": 1,
            },
        )
    )


def _describe(record: dict, entry: dict, user: str | None, *, public: bool) -> dict:
    """One row of the listing.

    ``filepath`` is never included - not even briefly. It is not fetched at all,
    rather than fetched and removed before returning: the second shape leaves the
    value one missed branch away from the response.
    """
    visible = _may_see(record, user, public=public)
    kind = ((record.get("processing") or {}).get("fileProcessing") or {}).get("type")

    described = {
        "id": str(record["_id"]) if visible else None,
        "hash": record.get("hash") if visible else "",
        "tag": entry.get("tag"),
        "displayName": (
            record.get("displayName") or record.get("name")
            if visible
            else _("You do not have permission to view this record")
        ),
    }
    if record.get("accessRights"):
        described["accessRights"] = record["accessRights"]
    if kind:
        described["processing"] = {"fileProcessing": {"type": kind}}

    return described


def _may_see(record: dict, user: str | None, *, public: bool) -> bool:
    from archihub.api.records import access

    required = record.get("accessRights")
    if not required:
        return True
    if public:
        return False

    from archihub.api.users.services import has_role

    return has_role(user, "admin") or access.holds(user, required)


def _gallery_entry(entries: list[dict]) -> dict | None:
    """The single row standing in for a resource's grouped images."""
    object_ids = [oid for oid in (_object_id(e["id"]) for e in entries) if oid is not None]
    if not object_ids:
        return None

    count = _mongo().count(
        RECORDS_COLLECTION,
        {"_id": {"$in": object_ids}, "processing.fileProcessing.type": "image"},
    )
    if not count:
        return None

    return {
        "id": GALLERY_ID,
        "_id": GALLERY_ID,
        "displayName": _("{count} images", count=count),
        "hash": "",
        "tag": _("Image gallery"),
        "processing": {"fileProcessing": {"type": "image gallery"}},
        "_imageCount": count,
    }


def count_images(resource: dict) -> tuple[dict, int]:
    """How many image records a resource holds; the gallery viewer's page count."""
    entries = ordered_file_entries(resource)
    object_ids = [oid for oid in (_object_id(e["id"]) for e in entries) if oid is not None]
    if not object_ids:
        return {"msg": _("Resource does not have images")}, 404

    count = _mongo().count(
        RECORDS_COLLECTION,
        {"_id": {"$in": object_ids}, "processing.fileProcessing.type": "image"},
    )
    if not count:
        return {"msg": _("Resource does not have images")}, 404

    return {"pages": count}, 200


# ---------------------------------------------------------------------------
# Bulk download
# ---------------------------------------------------------------------------


def bulk_download(resource: dict, kind: str, user: str | None, *, public: bool = False):
    """A zip of everything attached to a resource that the caller may have.

    A single file is served directly rather than wrapped in an archive.
    """
    from archihub.api.records import media

    if kind not in DOWNLOAD_KINDS:
        raise DownloadRefused(_("Unsupported download type"), 400)
    if not media.downloads_enabled():
        # Checked on the public route too. An archive with downloads switched
        # off must not go on serving them to anonymous callers.
        raise DownloadRefused(_("Files download isn't active"), 400)

    entries = ordered_file_entries(resource)
    records = _downloadable(entries, user, public=public)
    if not records:
        raise DownloadRefused(_("Resource does not have files"), 404)

    if len(records) == 1:
        path, name = media.download_path(records[0], kind)
        if not path.is_file():
            raise DownloadRefused(_("Record does not have files"), 404)
        from archihub.core.responses import file_response

        return file_response(path, download_name=name, as_attachment=True)

    return _archive(resource, records, kind)


def _downloadable(entries: list[dict], user: str | None, *, public: bool) -> list[dict]:
    """The records behind these entries that the caller is actually allowed.

    A record they may not see is **left out**, not included under a blanked
    name: the archive is written from each record's stored path, so anything that
    survives this filter ends up in the download whatever it is called.
    """
    object_ids = [oid for oid in (_object_id(e["id"]) for e in entries) if oid is not None]
    if not object_ids:
        return []

    records = list(
        _mongo().get_all_records(
            RECORDS_COLLECTION,
            {"_id": {"$in": object_ids}},
            fields={
                "name": 1, "displayName": 1, "accessRights": 1, "filepath": 1,
                "processing.fileProcessing.type": 1, "processing.fileProcessing.path": 1,
            },
        )
    )
    order = {str(e["id"]): index for index, e in enumerate(entries)}
    records.sort(key=lambda r: order.get(str(r["_id"]), len(order)))

    return [r for r in records if _may_see(r, user, public=public)]


def archive_name(resource_id: str, records: list[dict], kind: str) -> str:
    """A cache filename derived from what the archive will contain.

    Derived from the contents, never from the request. Two properties follow:
    no client-supplied string reaches the path, and **the name changes when the
    contents change**. A fixed name would serve the first archive ever built for
    the life of the deployment, so a resource whose files were corrected would go
    on handing out the old set.
    """
    digest = hashlib.sha256()
    digest.update(str(resource_id).encode())
    digest.update(kind.encode())
    for record in records:
        digest.update(str(record["_id"]).encode())
    return f"{ZIP_PREFIX}{digest.hexdigest()[:32]}.zip"


def _archive(resource: dict, records: list[dict], kind: str):
    """Build (or reuse) the archive and serve it."""
    import zipfile

    from archihub.api.records import media
    from archihub.core.responses import file_response
    from archihub.core.settings import get_settings

    directory = filestore.resolve_within(get_settings().web_files_path, ZIP_DIRECTORY)
    directory.mkdir(parents=True, exist_ok=True)
    sweep_stale_archives(directory)

    resource_id = str(resource.get("_id") or resource.get("id") or "")
    destination = directory / archive_name(resource_id, records, kind)

    if not destination.is_file():
        # Written under a temporary name and moved into place. A request that
        # dies halfway would otherwise leave a truncated archive that the next
        # request finds, judges present, and serves as complete.
        staging = destination.with_suffix(".partial")
        try:
            with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
                written = 0
                for record in records:
                    try:
                        path, name = media.download_path(record, kind)
                    except (media.DownloadRefused, filestore.UnsupportedFile):
                        continue
                    if not path.is_file():
                        logger.info("Skipping a missing file in a bulk download: %s", path)
                        continue
                    archive.write(path, _entry_name(record, name))
                    written += 1

            if not written:
                filestore.remove_quietly(staging)
                raise DownloadRefused(_("Resource does not have files"), 404)

            staging.replace(destination)
        except DownloadRefused:
            raise
        except OSError:
            filestore.remove_quietly(staging)
            logger.exception("Could not build a bulk download archive")
            raise DownloadRefused(_("The download could not be prepared"), 500) from None

    return file_response(
        destination, download_name=destination.name, as_attachment=True
    )


def _entry_name(record: dict, fallback: str) -> str:
    """The name a file gets *inside* the archive.

    Sanitised, because a stored name containing ``..`` or a leading ``/`` writes
    outside the extraction directory on whoever opens the archive - the harm
    lands on the person who downloaded it, not on this server.
    """
    return filestore.secure_name(record.get("name") or fallback) or fallback


def sweep_stale_archives(directory) -> int:
    """Drop cached archives nobody has asked for in a day.

    They are derivable from the database at any time, so keeping them is a cache
    decision rather than a storage one. Only files this module generated are
    considered - the directory is not exclusively ours.
    """
    import time

    from pathlib import Path

    cutoff = time.time() - STALE_ZIP_SECONDS
    removed = 0

    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return 0

    for entry in entries:
        if not entry.name.startswith(ZIP_PREFIX):
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            logger.debug("Could not remove a stale archive: %s", entry, exc_info=True)

    if removed:
        logger.info("Removed %d stale download archive(s)", removed)
    return removed


#: Directories under WEB_FILES_PATH holding files this application generated and
#: can regenerate. An administrator may empty either from the settings screen.
#: An ALLOWLIST, not a parameter: the value reaches a filesystem path, so there
#: is no safe way to accept an arbitrary one. Adding a directory is a deliberate
#: act, made here.
GENERATED_DIRECTORIES = {
    "zipfiles": "Zip files deleted",
    "inventoryMaker": "Inventory files deleted",
}


def delete_generated(directory: str) -> tuple[dict, int]:
    """Empty one of the generated-file directories.

    Only regular files, and only directly inside the directory. The originals,
    the web derivatives and users' own uploads live elsewhere under the same
    root, so a symlink placed here must not become a way to delete them; and
    calling ``os.remove`` on every entry indiscriminately stops at the first
    subdirectory, leaving the rest of the clean-up undone.
    """
    from archihub.core.i18n import gettext as _
    from archihub.core.settings import get_settings

    message = GENERATED_DIRECTORIES.get(directory)
    if message is None:
        raise ValueError(f"Not a generated-file directory: {directory!r}")

    try:
        path = filestore.resolve_within(get_settings().web_files_path, directory)
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.exception("Could not open the %s directory", directory)
        return {"msg": _("Error deleting the files")}, 500

    removed = 0
    for entry in path.iterdir():
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
                removed += 1
        except OSError:
            logger.warning("Could not remove %s", entry)

    logger.info("Removed %d file(s) from %s", removed, directory)
    return {"msg": _(message)}, 200
