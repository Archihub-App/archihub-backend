"""Serving a record's derivatives.

The web-optimised versions a record's processing produced: an MP4 for video, an
MP3 for audio, three JPEG sizes for images. The original file is never served
here - it is the archival master, often hundreds of megabytes, and the players
want the derivative.

Range handling is Starlette's; see ``archihub/core/responses.py``. What is left
is resolving which file on disk a request means, and refusing to serve one that
is not underneath the media root.
"""

from __future__ import annotations

import logging
from pathlib import Path

from archihub.core import files as filestore
from archihub.core.i18n import gettext as _
from archihub.core.responses import file_response
from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

#: Suffix appended to the stored derivative path, by media type and size.
DERIVATIVE_SUFFIX = {
    ("video", None): ".mp4",
    ("audio", None): ".mp3",
    ("image", "large"): "_large.jpg",
    ("image", "medium"): "_medium.jpg",
    ("image", "small"): "_small.jpg",
}

STREAMABLE_TYPES = ("audio", "video", "image")
IMAGE_SIZES = ("small", "medium", "large")


class NotStreamable(Exception):
    """The record has no derivative that can be served."""


def derivative_of(record: dict, size: str = "large"):
    """``(path, media_kind)`` for a record's web derivative.

    The original's guard read::

        if 'processing' not in record:
            if 'fileProcessing' not in record['processing']:

    - the inner test subscripts the very key the outer one just established is
    absent, so an *unprocessed* record raised ``KeyError`` instead of the
    prepared "has not been processed" message, and that reached the client as a
    500 with the raw key name.
    """
    processing = record.get("processing") or {}
    file_processing = processing.get("fileProcessing") if isinstance(processing, dict) else None

    if not isinstance(file_processing, dict):
        raise NotStreamable(_("Record has not been processed"))

    kind = file_processing.get("type")
    if kind not in STREAMABLE_TYPES:
        raise NotStreamable(_("Record is not audio, video, or image"))

    stored = file_processing.get("path")
    if not stored:
        raise NotStreamable(_("Record has not been processed"))

    if size not in IMAGE_SIZES:
        size = "large"

    suffix = DERIVATIVE_SUFFIX[(kind, size if kind == "image" else None)]

    # `stored` comes out of the database, so it goes through the root check
    # rather than straight into a path join.
    path = filestore.resolve_within(get_settings().web_files_path, stored + suffix)
    return path, kind


def stream(record: dict, size: str = "large"):
    """A ``FileResponse`` for the record's derivative.

    Inline rather than an attachment, and with Range support, because this is
    what the ``<audio>`` and ``<video>`` elements fetch and seek within.
    """
    path, kind = derivative_of(record, size)

    if not path.is_file():
        logger.info("Derivative missing on disk for a processed record: %s", path)
        raise NotStreamable(_("Record has not been processed"))

    return file_response(path, media_type=_media_type(kind, path.name))


def _media_type(kind: str, filename: str) -> str:
    from archihub.core.responses import guess_media_type

    return guess_media_type(filename)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

#: The two things a caller may ask to download. ``original`` is the archival
#: master as deposited; ``small`` is the web derivative.
DOWNLOAD_KINDS = ("original", "small")

#: Capability that must be present in the instance's system settings for any
#: download to be served at all. An archive can turn distribution off entirely.
DOWNLOAD_CAPABILITY = "files_download"


class DownloadRefused(Exception):
    """The download cannot be served, with the status to answer."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def downloads_enabled() -> bool:
    from archihub.api.system.services import get_system_settings

    settings, _status = get_system_settings()
    return DOWNLOAD_CAPABILITY in (settings.get("capabilities") or [])


def download_path(record: dict, kind: str):
    """The file a download request means, and the name to serve it under.

    The archival master is served for ``original`` and the web derivative for
    ``small`` - except for documents, whose "derivative" is a directory of page
    images rather than a single file, so the master is served for both. That
    asymmetry is the original's and is deliberate: there is no single-file
    rendition of a document to hand back.

    The original ended with an ``if``/``elif`` over the requested kind and no
    ``else``, so an unrecognised value fell off the end returning ``None``,
    which Flask rendered as a 500 with an empty body.
    """
    if kind not in DOWNLOAD_KINDS:
        raise DownloadRefused(_("Unsupported download type"), 400)

    processing = record.get("processing") or {}
    file_processing = processing.get("fileProcessing") if isinstance(processing, dict) else None
    if not isinstance(file_processing, dict) or not file_processing.get("type"):
        raise DownloadRefused(_("Record does not have fileProcessing"), 404)

    stored_original = record.get("filepath")
    media_kind = file_processing["type"]

    if kind == "original" or media_kind == "document":
        if not stored_original:
            raise DownloadRefused(_("Record does not have files"), 404)
        path = filestore.resolve_within(get_settings().original_files_path, stored_original)
        return path, _download_name(record, path)

    suffix = DERIVATIVE_SUFFIX.get((media_kind, "large" if media_kind == "image" else None))
    if suffix is None:
        raise DownloadRefused(_("Record is not audio, video, or image"), 400)

    stored = file_processing.get("path")
    if not stored:
        raise DownloadRefused(_("Record has not been processed"), 404)

    path = filestore.resolve_within(get_settings().web_files_path, stored + suffix)
    return path, _download_name(record, path)


def _download_name(record: dict, path) -> str:
    """What the browser should call the saved file.

    The record's display name with the served file's real extension, so a
    downloaded scan is not named after the UUID it is stored under. Falls back
    to the stored name when there is no display name.
    """
    label = record.get("displayName") or record.get("name") or path.stem
    stem = filestore.secure_name(str(label)) or path.stem
    if stem.lower().endswith(path.suffix.lower()):
        return stem
    return stem + path.suffix


def download(record: dict, kind: str):
    """A ``FileResponse`` that saves rather than plays."""
    if not downloads_enabled():
        raise DownloadRefused(_("Files download isn't active"), 400)

    path, name = download_path(record, kind)
    if not path.is_file():
        logger.info("Download target missing on disk: %s", path)
        raise DownloadRefused(_("Record does not have files"), 404)

    return file_response(path, download_name=name, as_attachment=True)


# ---------------------------------------------------------------------------
# Fragment extraction
# ---------------------------------------------------------------------------

#: Longest fragment that will be transcoded, in seconds. There is no legitimate
#: caller asking for more, and the request holds a threadpool worker for the
#: whole transcode.
MAX_FRAGMENT_SECONDS = 2 * 60 * 60

#: Wall-clock ceiling on the ffmpeg process. The legacy code had none, so a
#: transcode that hung held its worker until the process was killed by hand.
FFMPEG_TIMEOUT_SECONDS = 300

#: Output settings per media kind, as the originals had them.
FRAGMENT_OUTPUT = {
    "video": (
        ".mp4",
        [
            "-c:v", "libx264",
            "-c:a", "aac",
            "-movflags", "faststart",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts",
        ],
    ),
    "audio": (".mp3", ["-c:a", "libmp3lame"]),
}


#: Prefix every generated fragment carries, so the sweep below can recognise its
#: own output and never touch anything else in the temporal directory.
FRAGMENT_PREFIX = "fragment-"

#: How long a fragment may sit unclaimed before the next extraction removes it.
STALE_FRAGMENT_SECONDS = 60 * 60


class FragmentFailed(Exception):
    """ffmpeg did not produce a usable fragment."""


def sweep_stale_fragments(directory) -> int:
    """Delete fragments left behind by requests that never completed.

    A fragment is normally removed once its response has been written
    (``delete_after``). That does not happen if the client disconnects mid-
    stream or the process dies between transcoding and sending, so without this
    the temporal directory grows without bound - the legacy code had the same
    gap with its ``call_on_close`` callback and no sweep at all.

    Runs on the extraction path because that is the only moment anything is
    known to be looking at this directory, and it costs one ``listdir``. Only
    files this module generated are considered.
    """
    import time

    cutoff = time.time() - STALE_FRAGMENT_SECONDS
    removed = 0

    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return 0

    for entry in entries:
        if not entry.name.startswith(FRAGMENT_PREFIX):
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            logger.debug("Could not remove a stale fragment: %s", entry, exc_info=True)

    if removed:
        logger.info("Removed %d stale media fragment(s)", removed)
    return removed


def fragment_command(source, destination, start: float, duration: float, kind: str) -> list[str]:
    """The ffmpeg argument list, built as a list and never a shell string.

    ``-ss``/``-t`` are placed **after** ``-i``, as the originals had them: that
    is the slow but frame-accurate seek, and a snap of a spoken phrase that
    starts a keyframe early is a wrong snap.

    Split out from the runner so the arguments can be asserted in tests without
    a transcode - and so it is obvious at a glance that nothing here is
    interpolated into a shell.
    """
    _suffix, codec_args = FRAGMENT_OUTPUT[kind]
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(source),
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        *codec_args,
        str(destination),
    ]


def extract_fragment(source, start: float, end: float, kind: str):
    """Transcode ``[start, end)`` of a media file to a temp file, and return it.

    The caller is responsible for having the response delete it - see
    :func:`stream_fragment`.
    """
    import subprocess
    import uuid

    if kind not in FRAGMENT_OUTPUT:
        raise NotStreamable(_("Record is not audio, video, or image"))

    duration = end - start
    if duration > MAX_FRAGMENT_SECONDS:
        raise FragmentFailed(_("The requested fragment is too long"))

    suffix, _codec_args = FRAGMENT_OUTPUT[kind]
    temporal = get_settings().temporal_files_path
    if not temporal:
        raise FragmentFailed(_("Temporal path is not configured"))

    directory = Path(temporal)
    directory.mkdir(parents=True, exist_ok=True)
    sweep_stale_fragments(directory)

    # A fresh name every time. The original built it from the record id and the
    # requested offsets and reused whatever it found there, so a fragment left
    # behind by a failed run was served as if it were the real thing.
    destination = directory / f"{FRAGMENT_PREFIX}{uuid.uuid4().hex}{suffix}"

    try:
        result = subprocess.run(
            fragment_command(source, destination, start, duration, kind),
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        filestore.remove_quietly(destination)
        logger.warning("ffmpeg timed out extracting a fragment from %s", source)
        raise FragmentFailed(_("The fragment could not be extracted")) from None
    except FileNotFoundError:
        filestore.remove_quietly(destination)
        logger.error("ffmpeg is not installed; fragment extraction is unavailable")
        raise FragmentFailed(_("The fragment could not be extracted")) from None

    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        filestore.remove_quietly(destination)
        # ffmpeg's stderr names paths on the server and is logged, never
        # returned - the original put it straight in the response body.
        logger.warning(
            "ffmpeg failed (%s) extracting a fragment: %s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace")[:2000],
        )
        raise FragmentFailed(_("The fragment could not be extracted"))

    return destination


def stream_fragment(record: dict, bounds: tuple[float, float], size: str = "large"):
    """A response serving just part of a recording.

    The temp file is deleted once the response has been written, which is what
    ``delete_after`` is for - the original attached a Flask ``call_on_close``
    callback to do the same thing.
    """
    path, kind = derivative_of(record, size)
    if not path.is_file():
        raise NotStreamable(_("Record has not been processed"))
    if kind not in FRAGMENT_OUTPUT:
        raise NotStreamable(_("Record is not audio, video, or image"))

    start, end = bounds
    fragment = extract_fragment(path, start, end, kind)

    from archihub.core.responses import guess_media_type

    return file_response(
        fragment,
        as_attachment=True,
        media_type=guess_media_type(fragment.name),
        delete_after=True,
    )


def parse_fragment_bounds(start_ms, end_ms) -> tuple[float, float] | None:
    """Validate a requested time range, or ``None`` if none was asked for.

    THE UNIT IS SECONDS, despite the parameter names. The legacy code passed
    these straight to ffmpeg's ``-ss``/``-t``, which take seconds, and the
    frontend fills them from an HTML media element's ``currentTime``, which is
    also seconds. Its own Swagger says "in seconds" too. The names are wrong and
    are kept only because they are the wire contract.

    Raises ``ValueError`` for a range that was asked for but does not make
    sense, so the caller can answer 400 rather than serving the whole file and
    leaving the user wondering why seeking did nothing.
    """
    if start_ms is None and end_ms is None:
        return None

    start = _as_float(start_ms)
    end = _as_float(end_ms)

    if start is None or end is None:
        raise ValueError(_("Invalid start_ms or end_ms"))
    if start < 0 or end <= start:
        raise ValueError(_("Invalid start_ms or end_ms"))

    return start, end


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
