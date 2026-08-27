"""How much the archive holds, and how much room is left for more.

THE TOTAL IS SUMMED, NOT WALKED. Every record carries the size of the file it
was stored with, so the whole figure is one aggregation over an index rather
than a stat() per file - milliseconds instead of a traversal of the entire media
tree. That is what makes this answerable on request at all, and it is why the
cache below is a backstop rather than the mechanism.

WHAT IS COUNTED, AND WHAT IS NOT. `total_cataloged_bytes` is the archive's own
holdings: the master files as they were deposited, plus what the database
occupies. It deliberately excludes DERIVED files - web versions, thumbnails,
deep-zoom tiles - which are reproducible from the masters and whose size answers
a different question ("what would a rebuild cost") from the one asked here
("how much material is held"). The difference between the two is visible in
`disk_capacity`, which measures the real volume with everything on it.

Sizes are binary: a gigabyte here is 1024³ bytes, which is what the operating
system reports and therefore what an operator comparing this against `df` sees.
"""

from __future__ import annotations

import logging
import shutil

from archihub.infra.cache import cached

logger = logging.getLogger(__name__)

GIGABYTE = 1024 ** 3

#: Records whose file is no longer on disk contribute nothing to a disk figure.
_NOT_DELETED = {"$ne": "deleted"}


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def format_bytes(total: int) -> str:
    """A size a person reads, in the units the operating system uses."""
    value = float(total)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


@cached("records")
def catalogued_files() -> dict:
    """Total bytes and file count across the stored masters.

    Cached on `records`, which means it is invalidated by the act of writing to
    that collection rather than by a clock: depositing a file makes the figure
    wrong immediately, and a TTL would leave it wrong for the rest of its
    window. The TTL that comes with the decorator remains as a backstop.
    """
    pipeline = [
        {"$match": {"status": _NOT_DELETED}},
        {"$group": {"_id": None, "bytes": {"$sum": "$size"}, "files": {"$sum": 1}}},
    ]
    rows = list(_mongo().aggregate("records", pipeline))
    if not rows:
        return {"bytes": 0, "files": 0}

    # `$sum` yields 0 for a field that is absent or not a number, so a record
    # stored without a size lowers the total rather than failing the query.
    return {"bytes": int(rows[0].get("bytes") or 0), "files": int(rows[0].get("files") or 0)}


def database_bytes() -> int:
    """What the database itself occupies.

    `dataSize` rather than `storageSize`: the question is how much catalogue
    there is, not how much room the storage engine has claimed for it - the
    latter includes space already freed and waiting to be reused, so it can grow
    while the archive shrinks.
    """
    try:
        return int(_mongo().db.command("dbStats").get("dataSize") or 0)
    except Exception:
        logger.warning("Could not read the database size", exc_info=True)
        return 0


def disk_capacity() -> dict:
    """The volume the masters are stored on.

    Read live: a single `statvfs`, and nothing in the database changes when the
    disk fills, so a cached answer here would go stale for the one reason an
    operator is looking at it.
    """
    from archihub.core.settings import get_settings

    root = get_settings().original_files_path
    if not root:
        return {"total_bytes": 0, "available_bytes": 0, "used_percentage": 0.0}

    try:
        usage = shutil.disk_usage(root)
    except OSError:
        # An unmounted or unreachable volume is itself worth reporting, and the
        # rest of the figures are still true.
        logger.warning("Could not read the capacity of %s", root, exc_info=True)
        return {"total_bytes": 0, "available_bytes": 0, "used_percentage": 0.0}

    used = usage.total - usage.free
    return {
        "total_bytes": usage.total,
        "available_bytes": usage.free,
        "used_percentage": round(used / usage.total * 100, 2) if usage.total else 0.0,
    }


def storage_report() -> dict:
    """The whole block, assembled from the three questions above."""
    files = catalogued_files()
    metadata = database_bytes()
    total = files["bytes"] + metadata

    return {
        "total_cataloged_bytes": total,
        "total_cataloged_gb": round(total / GIGABYTE, 2),
        "total_cataloged_formatted": format_bytes(total),
        "files_count": files["files"],
        "breakdown": {
            "multimedia_files_bytes": files["bytes"],
            "database_metadata_bytes": metadata,
        },
        "disk_capacity": disk_capacity(),
    }
