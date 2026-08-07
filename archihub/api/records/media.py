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


def parse_fragment_bounds(start_ms, end_ms) -> tuple[float, float] | None:
    """Validate a requested time range, or ``None`` if none was asked for.

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
