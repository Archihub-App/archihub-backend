"""Serving stored files, and rendering JSON the way the frontend already reads it.

Serving a stored file has one requirement that is easy to lose: the Range
behaviour ``upgrade_front``'s audio and video players depend on for seeking.

IT DOES, natively, in the installed version (Starlette 1.4.1): ``FileResponse``
parses ``Range``, answers ``206 Partial Content`` with ``Content-Range`` for a
single range, produces a ``multipart/byteranges`` body for several, and answers
``416`` for an unsatisfiable one. So there is no custom range code here - only
the things Starlette does *not* decide for us: which path is safe to serve,
what the download filename should be, and when a temporary file gets cleaned up.

MEDIA TYPES. By default the type is guessed from the filename, which is what the
frontend relies on for stored files. Where the bytes are about to be interpreted
by the *browser* rather than downloaded, pass an explicit ``media_type`` derived
from the record instead - a filename states only what the uploader claimed.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from archihub.core.files import remove_quietly

logger = logging.getLogger(__name__)

DEFAULT_MEDIA_TYPE = "application/octet-stream"


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _flask_default(value):
    """Render what ``json.dumps`` refuses, the way Flask's ``jsonify`` did.

    Two conventions for dates exist in the legacy responses, and both are wire
    contract:

    * a route that ran its document through ``json_util`` (``parse_result``)
      emitted ``{"$date": ...}``. The ported services call ``serialise()`` for
      exactly those, so the conversion happens before the encoder sees it and
      this function is never reached.
    * a route that returned a raw Mongo document through ``jsonify`` emitted an
      **HTTP date** - ``"Mon, 10 Aug 2026 14:12:34 GMT"``. ``GET /users/{id}``
      is one. That is what this reproduces.

    So the two paths keep the shapes their callers already produce, and the
    difference is a deliberate property of which one a route uses rather than an
    accident of what happened to be in the document.
    """
    import datetime as _datetime
    from email.utils import format_datetime

    if isinstance(value, _datetime.datetime):
        # A naive datetime is UTC, the reading Werkzeug's `http_date` applies.
        if value.tzinfo is None:
            value = value.replace(tzinfo=_datetime.timezone.utc)
        return format_datetime(value, usegmt=True)
    if isinstance(value, _datetime.date):
        return value.isoformat()

    from bson import json_util

    # ObjectId, Decimal128, Binary, ... - json_util knows every BSON type, and
    # produces the same `{"$oid": ...}` form the rest of the API returns.
    return json_util.default(value)


class ArchiJSONResponse(JSONResponse):
    """``JSONResponse`` that cannot 500 on an unencodable value.

    Starlette's renders with a bare ``json.dumps``, so one ``datetime`` left in
    a payload takes a working route to a 500 - and only for the documents that
    happen to carry the field, which is why such a bug survives both a unit
    test and a live smoke test and only shows up against real data.
    """

    def render(self, content) -> bytes:
        import json

        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=_flask_default,
        ).encode("utf-8")


def json_response(payload, status_code: int = 200) -> ArchiJSONResponse:
    """The single place a ``(payload, status)`` service result becomes a response."""
    return ArchiJSONResponse(status_code=status_code, content=payload)


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
