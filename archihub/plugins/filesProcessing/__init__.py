"""filesProcessing — derive web-viewable versions of archived files.

WHAT IT DOES. An archived master (a TIFF, a WAV, a PDF) is not what a browser
should be served. This plugin reads the master and writes derivatives beside it
under ``WEB_FILES_PATH`` — JPEG sizes and deep-zoom tiles for images, MP3/Ogg for
audio, MP4/WebM for video, page images for documents, a CSV preview for
spreadsheets — and records where they went under the record's
``processing.fileProcessing``. Everything the multimedia viewers show comes from
here.

It runs two ways: on demand over a selection (``POST /filesProcessing/bulk``), and
automatically when files are attached to a resource of a configured content type
(the ``resource_files_create`` and ``resource_update`` hooks).

WHICH KIND OF FILE IS DECIDED BY AN ALLOWLIST, NOT BY SUBSTRING
---------------------------------------------------------------

Dispatching on substrings of a stored MIME type — ``'audio' in mime``, ``'word'
in mime``, ``'sheet' in mime`` — with an extension check as a tiebreaker looks
reasonable and is not:

```python
if len(filename.split('.')) != 2:
    return None
```

So ``interview.final.csv`` was not recognised as a CSV and went to the *document*
branch instead, where LibreOffice was asked to convert it. That is one bug from
two: the extension helper's, and the fact that a substring test needed a helper
at all. ``classify()`` below matches the MIME type against declared prefixes and
takes the extension from ``Path.suffix``, which has never had an opinion about
how many dots a name contains.

PATHS OUT OF THE DATABASE ARE RESOLVED, NEVER CONCATENATED. ``filepath`` is a
stored string and it becomes a directory that gets written into; every use goes
through ``core.files.resolve_within``.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from celery import current_task, shared_task
from fastapi import Body, Depends
from fastapi.responses import JSONResponse

from archihub.core.i18n import gettext as _
from archihub.core.responses import json_response
from archihub.core.security.jwt import CurrentUser
from archihub.plugins.filesProcessing import media
from archihub.plugins.framework import data as plugin_data
from archihub.plugins.framework import interop
from archihub.plugins.framework.base import (
    ArchiPlugin,
    BrokerUnavailable,
    object_ids,
    queue,
    require_roles,
)

logger = logging.getLogger(__name__)

SLUG = "filesProcessing"

TASK_BULK = "filesProcessing.create_webfile"
TASK_AUTOMATIC = "filesProcessingCreate.auto"

PROCESSING_KEY = "fileProcessing"

#: Resources read per page while walking a bulk selection.
PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# What kind of file is this
# ---------------------------------------------------------------------------

#: MIME prefixes, most specific first. Order matters: `application/pdf` must be
#: tested before any looser rule that could also claim it.
_MIME_RULES = (
    ("application/pdf", "pdf"),
    ("audio/", "audio"),
    ("video/", "video"),
    ("image/", "image"),
)

#: MIME substrings for the formats whose types are not neatly prefixed. Word
#: documents are `application/vnd.openxmlformats-officedocument.wordprocessingml…`
#: and spreadsheets `…spreadsheetml…`; there is no prefix that separates them.
_MIME_CONTAINS = (
    ("spreadsheetml", "spreadsheet"),
    ("ms-excel", "spreadsheet"),
    ("opendocument.spreadsheet", "spreadsheet"),
    ("wordprocessingml", "document"),
    ("msword", "document"),
    ("opendocument.text", "document"),
)

#: Extensions that override the MIME type. A CSV is served as `text/plain` by
#: many uploaders, and treating it as a document sends it to LibreOffice.
_EXTENSION_OVERRIDES = {
    ".csv": "csv",
    ".tsv": "csv",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".ods": "spreadsheet",
}


def classify(mime: str | None, filepath: str | None) -> str | None:
    """Which processing branch a file takes, or ``None`` for "leave it alone".

    Returning ``None`` rather than guessing is deliberate: an unrecognised file
    is stored and served as-is, which is correct, whereas a wrong guess spends
    minutes of a worker producing derivatives nothing will read.
    """
    extension = Path(filepath or "").suffix.lower()
    if extension in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[extension]

    mime = (mime or "").lower()
    for prefix, kind in _MIME_RULES:
        if mime.startswith(prefix):
            return kind
    for fragment, kind in _MIME_CONTAINS:
        if fragment in mime:
            return kind

    # `text/*` that is not a CSV is a document; the extension check above has
    # already claimed the CSVs.
    if mime.startswith("text/"):
        return "document"

    return None


# ---------------------------------------------------------------------------
# Processing one file
# ---------------------------------------------------------------------------


def process_record(record: dict) -> bool:
    """Derive and store web versions for one record. Returns whether it did.

    Every branch ends by writing ``processing.fileProcessing`` through
    ``store_processing_result``, which `$set`s that one path — the original
    read the whole ``processing`` block and wrote it back, so a transcription
    finishing concurrently was discarded.
    """
    from archihub.core import files as filestore
    from archihub.core.settings import get_settings

    settings = get_settings()
    stored_path = record.get("filepath")
    if not stored_path:
        logger.warning("Record %s has no filepath", record.get("_id"))
        return False

    kind = classify(record.get("mime"), stored_path)
    if kind is None:
        logger.debug("No processing defined for %s (%s)", stored_path, record.get("mime"))
        return False

    try:
        source = filestore.resolve_within(settings.original_files_path, stored_path)
    except Exception:
        logger.error("Record %s has a filepath outside the originals root", record.get("_id"))
        return False

    if not source.is_file():
        logger.warning("Record %s points at a file that is not there", record.get("_id"))
        return False

    relative_dir = str(Path(stored_path).parent)
    stem = Path(stored_path).stem

    web_dir = filestore.resolve_within(settings.web_files_path, relative_dir)
    web_dir.mkdir(parents=True, exist_ok=True)
    output_stem = web_dir / stem

    result = _derive(kind, source, output_stem, settings, relative_dir, stem)
    if result is None:
        return False

    plugin_data.store_processing_result(str(record["_id"]), PROCESSING_KEY, result)
    return True


def _derive(kind: str, source: Path, output_stem: Path, settings, relative_dir: str, stem: str):
    """Run one processing branch, returning the entry to store or ``None``."""
    stored_path = str(Path(relative_dir) / stem)

    if kind == "audio":
        media.audio(source, output_stem)
        return _entry("audio", stored_path, metadata=media.media_metadata(source))

    if kind == "video":
        has_audio, has_video = media.video(source, output_stem)
        if not (has_audio or has_video):
            return None
        return _entry(
            "video" if has_video else "audio",
            stored_path,
            metadata=media.media_metadata(source),
        )

    if kind == "image":
        metadata, has_tiles = media.image(source, output_stem)
        entry = _entry("image", stored_path, metadata=metadata)
        if has_tiles:
            entry["dzi"] = True
        return entry

    if kind == "pdf":
        media.strip_active_content(source)
        media.pdf_pages(source, output_stem)
        # The viewer reads pages from a DIRECTORY named after the file, without
        # its extension. The original computed this as
        # `os.path.join(path_dir, filename).split('.')[0]`, which truncates at
        # the first dot ANYWHERE in the path - so a resource filed under a
        # directory containing a dot lost most of its path and the viewer found
        # no pages. `stem` has already removed exactly the extension.
        return _entry("document", stored_path)

    if kind == "document":
        from archihub.core import files as filestore

        scratch = filestore.resolve_within(settings.temporal_files_path, stem)
        media.document(source, scratch, output_stem)
        return _entry("document", stored_path)

    if kind in ("csv", "spreadsheet"):
        media.tabular(source, output_stem, spreadsheet=kind == "spreadsheet")
        return _entry("database", stored_path)

    return None


def _entry(kind: str, path: str, metadata: dict | None = None) -> dict:
    entry = {"type": kind, "path": path}
    if metadata:
        entry["metadata"] = metadata
    return entry


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------


class FilesProcessing(ArchiPlugin):
    def add_routes(self) -> None:
        @self.router.post(
            "/bulk",
            status_code=201,
            responses={201: {"description": "Queued"}, 400: {"description": "Invalid selection"}},
        )
        def process_files(
            body: dict = Body(...),
            current_user: CurrentUser = Depends(require_roles("admin", "processing")),
        ) -> JSONResponse:
            """Queue derivative generation over a selection of resources."""
            error = self.validate_settings_fields(body, "bulk")
            if error:
                # The refusal is RETURNED, not merely computed. Discarding it
                # lets a body missing its content type reach the task, where the
                # failure surfaces as a worker traceback instead of a 400.
                return json_response({"msg": error}, 400)

            try:
                queue(
                    bulk_task,
                    TASK_BULK,
                    current_user.username,
                    "msg",
                    body,
                    current_user.username,
                    params={"args": body},
                )
            except BrokerUnavailable:
                return json_response({"msg": _("The task queue is unavailable")}, 503)

            return json_response({"msg": _("The task was added to the processing queue")}, 201)

    def settings_payload(self, kind: str):
        """The settings form, with the content-type picker filled in."""
        from archihub.api.types import services as types_services

        settings = self.translated_settings()
        group = _find_group(settings, "types_activation")

        if group is not None:
            types, _status = types_services.get_all()
            group["fields"] = [
                {
                    "type": "select",
                    "id": "type",
                    "default": "",
                    "options": [
                        {"value": t.get("slug"), "label": t.get("name")}
                        for t in (types if isinstance(types, list) else [])
                    ],
                    "required": True,
                },
                {"type": "number", "id": "order", "default": 0, "required": True},
            ]
            group["default"] = self.get_plugin_settings().get("types_activation") or []

        return self.select_settings(settings, kind)

    def save_settings(self, data: dict):
        """Store which content types are processed automatically, and in what order."""
        rows = data.get("types_activation")
        if rows is None:
            return {"msg": _("Missing required fields")}, 400
        if not isinstance(rows, list):
            return {"msg": _("Missing required fields")}, 400

        normalised = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("type"):
                return {"msg": _("Missing required fields")}, 400
            # `order` decides hook execution order and is compared numerically.
            # The legacy default was the STRING '0', and the hook bus sorts its
            # registrations - so a mix of stored strings and stored numbers
            # raised TypeError when the hook fired, taking the upload with it.
            try:
                order = int(row.get("order", 0) or 0)
            except (TypeError, ValueError):
                return {"msg": _("Interval value must be a positive integer")}, 400
            normalised.append({**row, "order": order})

        self.set_plugin_settings({**data, "types_activation": normalised})
        return {"msg": _("Settings updated")}, 200

    def activate_settings(self) -> None:
        """Register the automatic-processing hooks. Worker process only."""
        from archihub.core.hooks import get_hook_handler

        hooks = get_hook_handler()
        for entry in self.get_plugin_settings().get("types_activation") or []:
            if not isinstance(entry, dict) or not entry.get("type"):
                continue
            try:
                order = int(entry.get("order", 0) or 0)
            except (TypeError, ValueError):
                order = 0
            for hook_name in ("resource_files_create", "resource_update"):
                hooks.register(hook_name, automatic_task, args=[entry], queue=order)

    def build(self):
        # Registered here rather than at import: a capability offered by a
        # plugin that is not active would be a plugin running while switched off.
        interop.provide(interop.PDF_CONVERSION, SLUG, media.convert_to_pdf)
        # `views` uses this to make an uploaded thumbnail renderable. It is the
        # same `process_record` the bulk and automatic tasks run; the difference
        # is only that the caller wants it done before it answers, because a view
        # whose image has not been derived shows no image at all.
        interop.provide(interop.IMAGE_DERIVATIVES, SLUG, process_record)
        return super().build()


def _find_group(settings: dict, group_id: str) -> dict | None:
    """By id, not by list position - see scheduleSystemTasks for why."""
    for entry in settings.get("settings") or []:
        if isinstance(entry, dict) and entry.get("id") == group_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


@shared_task(ignore_result=False, name=TASK_AUTOMATIC)
def automatic_task(type_config: dict, body: dict) -> str:
    """Process the files just attached to a resource of a configured type."""
    if body.get("post_type") != (type_config or {}).get("type"):
        return "ok"

    resource_id = body.get("_id")
    if not resource_id:
        return "ok"

    records = list(
        _mongo().get_all_records(
            "records",
            {
                "parent.id": {"$in": [str(resource_id)]},
                f"processing.{PROCESSING_KEY}": {"$exists": False},
            },
            fields={"_id": 1, "mime": 1, "filepath": 1},
        )
    )

    processed = _process_all(records)
    plugin_data.broadcast_cache_clear()
    return _("Processed {size} files", size=processed)


@shared_task(ignore_result=False, name=TASK_BULK)
def bulk_task(body: dict, user: str) -> str:
    """Process every unprocessed file under a selection of resources."""
    filters = _resource_filters(body)
    overwrite = bool(body.get("overwrite"))

    total = _mongo().count("resources", filters)
    _progress(_("Starting process"), 0.0)

    processed = 0
    seen = 0

    for page in _resource_pages(filters):
        seen += len(page)
        # A page of resources at a time, so the records query is one round trip
        # per hundred resources rather than one per resource.
        records = _records_for(page, overwrite=overwrite)
        processed += _process_all(records)
        # `total` can be zero - a selection matching nothing is a legitimate
        # request. The original computed `step / total * 100` unconditionally
        # and raised ZeroDivisionError, recording the run as a failed job.
        _progress(
            _("Processing files. Step {step} of {total}", step=seen, total=total or seen),
            (seen / total * 100) if total else 100.0,
        )

    plugin_data.broadcast_cache_clear()
    return _(
        "Processed {size} files of a total of {total} resources.", size=processed, total=total
    )


def _process_all(records: list[dict]) -> int:
    """Process each record, counting successes. One failure does not stop the rest."""
    processed = 0
    for record in records:
        try:
            if process_record(record):
                processed += 1
        except media.ProcessingFailed as exc:
            # Logged with the record id, which the original's bare `print(str(e))`
            # did not carry - so a failed derivative could not be traced to a file.
            logger.warning("Record %s: %s", record.get("_id"), exc)
        except Exception:
            logger.exception("Unexpected failure processing record %s", record.get("_id"))
    return processed


def _records_for(resources: list[dict], *, overwrite: bool) -> list[dict]:
    filters: dict = {"parent.id": {"$in": [str(row["_id"]) for row in resources]}}
    if not overwrite:
        filters[f"processing.{PROCESSING_KEY}"] = {"$exists": False}

    return list(
        _mongo().get_all_records(
            "records", filters, fields={"_id": 1, "mime": 1, "filepath": 1}
        )
    )


def _resource_pages(filters: dict):
    """Pages of matching resources, paginated by ``_id``.

    Same reasoning as the indexing tasks: a ``skip`` walk re-scans what it has
    already returned and shifts under concurrent writes. The original also
    advanced `skip` by 100 while its loop condition checked the size of the page
    it had *just* read, so it re-read the first page whenever the driver
    returned fewer rows than asked for.
    """
    mongo = _mongo()
    last_id = None

    while True:
        query = dict(filters)
        if last_id is not None:
            query = {"$and": [query, {"_id": {"$gt": last_id}}]}

        page = list(
            mongo.get_all_records(
                "resources", query, sort=[("_id", 1)], limit=PAGE_SIZE, fields={"_id": 1}
            )
        )
        if not page:
            return

        yield page

        if len(page) < PAGE_SIZE:
            return
        last_id = page[-1]["_id"]


def _resource_filters(body: dict) -> dict:
    """Which resources a bulk run covers."""
    post_type = body.get("post_type")
    if not post_type:
        raise ValueError(_("No content type was specified"))

    filters: dict = {"post_type": {"$in": post_type} if isinstance(post_type, list) else post_type}

    resources = body.get("resources") or []
    parent = body.get("parent")

    if parent and not resources:
        return {
            "$or": [
                {"parents.id": parent, "post_type": filters["post_type"]},
                {"_id": object_ids([parent], "parent")[0]},
            ]
        }
    if resources:
        return {"_id": {"$in": object_ids(resources, "resources")}, **filters}
    return filters


def _progress(status: str, percent: float) -> None:
    """Report progress, if this is running inside a task.

    Guarded because ``current_task`` is ``None`` when the function is called
    directly - which the tests do, and which the original could not.
    """
    try:
        if current_task is None or current_task.request.id is None:
            return
        current_task.update_state(
            state="PROGRESS",
            meta={
                "status": status,
                "progress": percent,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    except Exception:
        logger.debug("Could not report task progress", exc_info=True)


plugin_info = {
    "name": "Procesamiento de archivos",
    "description": (
        "Plugin para procesar archivos y generar versiones para consulta en el gestor documental"
    ),
    "version": "0.1",
    "author": "Néstor Andrés Peña",
    "type": ["settings", "bulk"],
    "actions": [
        {
            "placement": "detail_resource",
            "label": "Procesar archivos",
            "roles": ["admin", "processing", "editor"],
            "endpoint": "bulk",
            "icon": "PrecisionManufacturing",
            "extraOpts": [
                {
                    "type": "checkbox",
                    "label": "Sobreescribir archivos existentes",
                    "id": "overwrite",
                    "instructions": (
                        "Sobreescribir archivos ya procesados. Si esta opción está desactivada, "
                        "el plugin solo procesará los archivos que no tengan una versión procesada."
                    ),
                    "default": False,
                    "required": False,
                }
            ],
        }
    ],
    "settings": {
        "settings": [
            {
                "type": "instructions",
                "title": "Instrucciones",
                "text": (
                    "Este plugin permite procesar archivos y generar versiones para consulta en "
                    "el gestor documental. Para ello, puede especificar el tipo de contenido "
                    "sobre el cual quiere generar las versiones."
                ),
            },
            {
                "type": "multiple",
                "title": "Tipos de contenido a generar",
                "id": "types_activation",
                "fields": [],
            },
        ],
        "settings_bulk": [
            {
                "type": "instructions",
                "title": "Instrucciones",
                "text": (
                    "Este plugin permite procesar archivos y generar versiones para consulta en "
                    "el gestor documental. Para ello, puede especificar el tipo de contenido "
                    "sobre el cual quiere generar las versiones y los filtros que desea aplicar. "
                    "Es importante notar que el proceso de generación de versiones puede tardar "
                    "varios minutos, dependiendo de la cantidad de recursos que se encuentren en "
                    "el gestor documental y el tamaño original de los archivos."
                ),
            },
            {
                "type": "checkbox",
                "label": "Sobreescribir archivos existentes",
                "id": "overwrite",
                "instructions": (
                    "Sobreescribir archivos ya procesados. Si esta opción está desactivada, el "
                    "plugin solo procesará los archivos que no tengan una versión procesada."
                ),
                "default": False,
                "required": False,
            },
        ],
    },
}


def build() -> FilesProcessing:
    return FilesProcessing(SLUG, plugin_info, module_file=__file__)
