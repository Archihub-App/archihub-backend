"""File storage and delivery.

Every route in ``resources`` and ``records`` either accepts an upload or serves
a stored file, so all of them depend on the rules this module fixes in one
place.

THE THREE DECISIONS, and the evidence behind them:

1. **Uploads are bounded.** Flask configured no ``MAX_CONTENT_LENGTH``, so they
   were unbounded; inheriting that by accident is worse than choosing a number.
   ``settings.max_upload_bytes`` (5 GiB by default - archival masters are large)
   is enforced *while streaming*, so an oversized upload is refused after one
   chunk past the limit rather than after it has been written.

2. **``os.fsync`` applies to the destination.** Flushing and syncing the
   *incoming* upload is meaningless - you cannot fsync data you are reading.
   The durability belongs to the file being written.

   The plan predicted this would *raise* under Starlette, because
   ``UploadFile.file`` is a ``SpooledTemporaryFile``. It does not, on either
   Python this runs on: ``fileno()`` calls ``rollover()`` first, so the real
   consequence would have been spilling every in-memory upload to a temporary
   file and fsyncing that. Wasted I/O rather than an error - but the correction
   is the same, and nothing here touches the source's descriptor.

3. **Range requests are Starlette's job.** Verified against the installed
   version: ``FileResponse`` implements single- and multi-range handling
   natively, including ``206``, ``Content-Range`` and ``416``. Serving a stored
   file therefore needs no custom range code, and the multimedia players'
   seeking works.

FILENAMES. :func:`store_upload` writes directly to a UUID name, never to the
client's own. Writing under the client's name and renaming afterwards leaves a
window in which two concurrent uploads of the same filename land on one path:
the second overwrites the first, both then rename, and one upload is lost with
no error.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from archihub.core.i18n import gettext as _
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

#: Read size for the streaming copy. Large enough that the syscall overhead is
#: irrelevant for multi-gigabyte masters, small enough not to matter in memory.
CHUNK_SIZE = 1024 * 1024

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class UploadTooLarge(Exception):
    """Raised when an upload exceeds the configured ceiling."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(_("The file exceeds the maximum allowed size"))


class UnsupportedFile(Exception):
    """Raised for a filename this endpoint will not accept."""


@dataclass(frozen=True)
class StoredFile:
    """What :func:`store_upload` wrote."""

    path: Path
    filename: str
    sha256: str
    size: int
    original_filename: str

    @property
    def extension(self) -> str:
        return self.path.suffix.lstrip(".").lower()


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def secure_name(filename: str | None) -> str:
    """A filename safe to place in a path.

    Equivalent in intent to Werkzeug's ``secure_filename``, kept here so the
    package does not become a dependency of the new stack for one function - and
    so its one sharp edge is handled: ``secure_filename`` returns the **empty
    string** for input consisting only of separators or dots (``"..."``,
    ``"/../"``). The original then did ``os.path.join(directory, "")``, which is
    the directory itself, and tried to write a file over it.
    """
    if not filename:
        raise UnsupportedFile(_("The file has no name"))

    normalised = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    # Take the basename after normalising both separators, so neither
    # `a/b.txt` nor a Windows-style `a\b.txt` can introduce a path segment.
    normalised = normalised.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE.sub("_", normalised).strip("._")

    if not cleaned:
        raise UnsupportedFile(_("The file has no name"))
    return cleaned


def extension_of(filename: str) -> str:
    """The lowercase extension, without the dot. Empty when there is none."""
    _stem, _dot, suffix = filename.rpartition(".")
    return suffix.lower() if _dot else ""


def is_allowed(filename: str, allowed_extensions) -> bool:
    """Whether a filename's extension is in the allowed set.

    EXTENSION CHECKING IS NOT CONTENT CHECKING. This tells you only what the
    uploader claimed. Use :func:`sniff_media_type` as well wherever the bytes are
    about to be handed to a parser - ffmpeg, LibreOffice and pdf2image all shell
    out, so what the file actually is decides what runs.
    """
    extension = extension_of(filename)
    return bool(extension) and extension in {e.lower().lstrip(".") for e in allowed_extensions}


def unique_name(original: str) -> str:
    """A collision-free storage name that keeps the original's extension."""
    extension = extension_of(original)
    return f"{uuid.uuid4()}.{extension}" if extension else str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------


def dated_directory(root: str | os.PathLike, when: datetime | None = None) -> Path:
    """``<root>/YYYY/MM/DD``, created if absent.

    The layout the archive already uses on disk; keeping it means stored paths
    stay valid across the cutover.
    """
    moment = when or datetime.now()
    path = Path(root) / moment.strftime("%Y") / moment.strftime("%m") / moment.strftime("%d")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_within(root: str | os.PathLike, *parts: str) -> Path:
    """Join ``parts`` under ``root``, refusing anything that escapes it.

    Stored paths come out of the database, and a document holding ``../..``
    would otherwise reach outside the media root. Cheap to check, and the check
    has to live somewhere both the read and write paths can use.
    """
    base = Path(root).resolve()
    candidate = (base / Path(*parts)).resolve()

    if base != candidate and base not in candidate.parents:
        logger.warning("Refusing a path outside the media root: %r", candidate)
        raise UnsupportedFile(_("Invalid file path"))

    return candidate


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def store_upload(
    source,
    directory: str | os.PathLike,
    original_filename: str,
    *,
    max_bytes: int | None = None,
    fsync: bool = True,
) -> StoredFile:
    """Stream an upload to ``directory`` under a fresh unique name.

    ``source`` is any readable binary file-like object - Starlette's
    ``UploadFile.file``, a plain open file, an ``io.BytesIO``.

    The content hash is computed **during** the copy. The original wrote the
    file and then re-read all of it to hash it, which for archival masters means
    reading several gigabytes back off disk for no reason.

    A file that exceeds the ceiling is refused and its partial write removed, so
    a rejected upload leaves nothing behind.
    """
    limit = max_bytes if max_bytes is not None else get_settings().max_upload_bytes
    safe_original = secure_name(original_filename)

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    storage_name = unique_name(safe_original)
    destination = target_dir / storage_name

    digest = hashlib.sha256()
    written = 0

    try:
        with open(destination, "wb") as handle:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break

                written += len(chunk)
                if limit and written > limit:
                    raise UploadTooLarge(limit)

                handle.write(chunk)
                digest.update(chunk)

            handle.flush()
            if fsync:
                # The descriptor we own, not the caller's. See the module
                # docstring - this is the SpooledTemporaryFile fix.
                os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return StoredFile(
        path=destination,
        filename=storage_name,
        sha256=digest.hexdigest(),
        size=written,
        original_filename=safe_original,
    )


def store_existing_file(
    source_path: str | os.PathLike,
    directory: str | os.PathLike,
    original_filename: str | None = None,
) -> StoredFile:
    """As :func:`store_upload`, for a file already on disk.

    The path taken when a plugin hands over something it produced itself rather
    than something a browser sent.
    """
    source = Path(source_path)
    with open(source, "rb") as handle:
        return store_upload(
            handle,
            directory,
            original_filename or source.name,
            # Locally-produced files are not subject to the request ceiling; a
            # derivative can legitimately be larger than what was uploaded.
            max_bytes=0,
        )


def hash_file(path: str | os.PathLike) -> str:
    """SHA-256 of a file already on disk."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remove_quietly(path: str | os.PathLike) -> None:
    """Delete a file, logging rather than raising if it cannot be removed.

    Used on cleanup paths where the caller has already succeeded at the thing
    the user asked for - failing the request because a temporary file lingered
    would be the wrong trade.
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove %s", path, exc_info=True)


def remove_tree_quietly(path: str | os.PathLike) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        logger.warning("Could not remove directory %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# Content sniffing
# ---------------------------------------------------------------------------


def sniff_media_type(path: str | os.PathLike) -> str | None:
    """The media type of a file's actual bytes, or ``None`` if undeterminable.

    ``python-magic`` is already a declared dependency but was never used for
    this. Call it before handing bytes to ffmpeg, LibreOffice or a PDF parser -
    the extension only says what the uploader claimed.
    """
    try:
        import magic
    except Exception:
        logger.debug("python-magic unavailable; skipping content sniffing")
        return None

    try:
        return magic.from_file(str(path), mime=True)
    except Exception:
        logger.warning("Could not determine the content type of %s", path, exc_info=True)
        return None


def content_matches_extension(path: str | os.PathLike, expected_types) -> bool:
    """Whether a file's real content type is among ``expected_types``.

    Returns ``True`` when sniffing is unavailable: this is a defence-in-depth
    check layered on top of the extension allowlist, and turning it into a hard
    dependency would mean an environment without libmagic could accept nothing.
    """
    detected = sniff_media_type(path)
    if detected is None:
        return True
    return any(detected.startswith(expected) for expected in expected_types)
