"""Serving stored files.

Replaces Flask's ``send_file`` at the ~20 sites that use it. The question
PLAN_FASTAPI.md section 6 raised was whether Starlette preserves the Range
behaviour ``upgrade_front``'s audio and video players depend on for seeking.

IT DOES, natively, in the installed version (Starlette 1.4.1): ``FileResponse``
parses ``Range``, answers ``206 Partial Content`` with ``Content-Range`` for a
single range, produces a ``multipart/byteranges`` body for several, and answers
``416`` for an unsatisfiable one. So there is no custom range code here - only
the things Starlette does *not* decide for us: which path is safe to serve,
what the download filename should be, and when a temporary file gets cleaned up.

A NOTE ON WHAT CHANGED. Flask's ``send_file`` guesses the media type from the
filename and so did every legacy call site. That is kept, because the stored
extension is what the frontend already relies on - but where the bytes are
about to be interpreted by the *browser* rather than downloaded, prefer passing
an explicit ``media_type`` derived from the record, not the filename.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from archihub.core.files import remove_quietly

logger = logging.getLogger(__name__)

DEFAULT_MEDIA_TYPE = "application/octet-stream"


def file_response(
    path: str | os.PathLike,
    *,
    download_name: str | None = None,
    as_attachment: bool = False,
    media_type: str | None = None,
    delete_after: bool = False,
    max_age: int | None = None,
) -> Response:
    """Serve a file from disk, with Range support.

    ``as_attachment`` sets ``Content-Disposition: attachment``, which is what
    the legacy ``send_file(..., as_attachment=True)`` calls meant. Everything
    else is served inline so the browser's media elements can play it.

    ``delete_after`` removes the file once the response has been sent - the
    replacement for Flask's ``response.call_on_close(...)``, which the fragment
    extractors use to clean up transcoded clips. It runs as a Starlette
    background task, i.e. **after** the last byte reaches the client, so a
    seeking player is not served a file that has already been deleted.
    """
    resolved = Path(path)
    if not resolved.is_file():
        logger.info("Asked to serve a file that is not there: %s", resolved)
        raise FileNotFoundError(resolved)

    name = download_name or resolved.name
    resolved_media_type = media_type or guess_media_type(name)

    headers = {}
    if max_age is not None:
        headers["Cache-Control"] = f"private, max-age={max_age}"

    return FileResponse(
        resolved,
        media_type=resolved_media_type,
        filename=name if as_attachment else None,
        headers=headers or None,
        background=BackgroundTask(remove_quietly, resolved) if delete_after else None,
    )


def guess_media_type(filename: str) -> str:
    """Media type from a filename, defaulting to a safe binary type.

    ``mimetypes`` has no opinion about several formats an archive routinely
    holds, and returning ``None`` would make Starlette fall back to a type that
    browsers sometimes sniff. The additions below are the ones that came up in
    the stored data.
    """
    guessed, _encoding = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    return _EXTRA_TYPES.get(filename.rpartition(".")[2].lower(), DEFAULT_MEDIA_TYPE)


_EXTRA_TYPES = {
    "jp2": "image/jp2",
    "jpf": "image/jpx",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "flac": "audio/flac",
    "opus": "audio/opus",
    "m4a": "audio/mp4",
    "heic": "image/heic",
    "avif": "image/avif",
    "epub": "application/epub+zip",
}


def bytes_response(
    payload: bytes,
    *,
    media_type: str = DEFAULT_MEDIA_TYPE,
    download_name: str | None = None,
) -> Response:
    """Serve an in-memory payload.

    The replacement for the two ``send_file(BytesIO(...))`` calls in ``snaps``,
    which render a single JPEG frame. Deliberately **not** a ``FileResponse``:
    these are small, already in memory, and have no path to range over.
    """
    headers = {}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'

    return Response(content=payload, media_type=media_type, headers=headers or None)
