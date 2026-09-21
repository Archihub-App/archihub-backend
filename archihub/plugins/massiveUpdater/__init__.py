"""massiveUpdater — import a spreadsheet back into the catalogue.

The counterpart to ``inventoryMaker``: an archivist exports a sheet, edits it,
and uploads it here. The *file format* — the sheet names, the two-row header
convention, the column meanings — is the contract with spreadsheets already in
people's hands, so it does not change.

Every row of every sheet is imported: content types can be created as well as
updated, every metadata standard in a sheet is written, a ``parent`` column is
resolved per row, and the ``overwrite`` checkbox is honoured (see
``BLANK_CLEARS``). One bad row is reported and skipped; it never ends the import.

VALIDATION GOES THROUGH THE SAME PATH A FORM SUBMISSION DOES. Each row is
assembled and handed to ``resources.write``/``plugins.framework.data``, which
apply the content type's own rules, so an import cannot set fields a person
editing the same resource could not.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from fastapi import Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from archihub.core.i18n import gettext as _
from archihub.core.responses import json_response
from archihub.core.security.jwt import CurrentUser
from archihub.plugins.framework.base import (
    ArchiPlugin,
    BrokerUnavailable,
    queue,
    require_roles,
    task_result_file,
)

logger = logging.getLogger(__name__)

SLUG = "massiveUpdater"
TASK_UPDATE = "massiveUpdater.update_inventory"

ALLOWED_EXTENSIONS = {"xlsx"}

#: Sheet name -> importer. The first one present in the workbook decides what
#: the file is.
SHEET_ORDER = ("Tipo", "Estandar", "Listado", "Recursos")

#: Rows an import may contain. A spreadsheet is an unbounded input that turns
#: into unbounded database writes.
MAX_ROWS = 20000

#: A module constant rather than an inline literal, because an implicitly
#: concatenated string inside `_()` is one msgid at run time and several
#: fragments to every extraction tool - so the catalogue never matches it and
#: the message silently stays untranslated.
QUEUED_MESSAGE = (
    "The file was uploaded and the task added to the processing queue. "
    "You can check your profile when it has finished and download the report."
)


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


class MassiveUpdater(ArchiPlugin):
    def add_routes(self) -> None:
        plugin = self

        @self.router.post(
            "/lunch",
            status_code=201,
            responses={
                201: {"description": "Queued"},
                400: {"description": "No file, or not a spreadsheet"},
            },
        )
        def launch(
            data: str = Form(...),
            files: list[UploadFile] = File(default_factory=list),
            current_user: CurrentUser = Depends(require_roles("admin", "processing", "editor")),
        ) -> JSONResponse:
            """Upload one or more spreadsheets and queue their import.

            The path keeps its spelling — ``/lunch``, not ``/launch``. It is what
            every plugin launch screen in ``upgrade_front`` calls.
            """
            import json

            from archihub.core import files as filestore
            from archihub.core.settings import get_settings

            try:
                body = json.loads(data)
            except (TypeError, ValueError):
                return json_response({"msg": _("Invalid settings payload")}, 400)
            if not isinstance(body, dict):
                return json_response({"msg": _("Invalid settings payload")}, 400)

            error = plugin.validate_settings_fields(body, "lunch")
            if error:
                return json_response({"msg": error}, 400)

            if not files:
                return json_response({"msg": _("No file was uploaded")}, 400)

            # EVERY file is checked before ANY is stored, so a refused request
            # never leaves a job running that the caller was told had not started.
            for upload in files:
                if not filestore.is_allowed(upload.filename or "", ALLOWED_EXTENSIONS):
                    return json_response({"msg": _("File type not allowed")}, 400)

            settings = get_settings()
            queued = 0
            for upload in files:
                try:
                    stored = filestore.store_upload(
                        upload.file, settings.temporal_files_path, upload.filename or "import.xlsx"
                    )
                except filestore.UploadTooLarge:
                    return json_response({"msg": _("The file is too large")}, 400)
                except Exception:
                    logger.exception("Could not store an uploaded spreadsheet")
                    return json_response({"msg": _("Error uploading the file")}, 500)

                try:
                    queue(
                        update_task,
                        TASK_UPDATE,
                        current_user.username,
                        "file_download",
                        str(stored.path),
                        bool(body.get("overwrite")),
                        current_user.username,
                    )
                except BrokerUnavailable:
                    filestore.remove_quietly(stored.path)
                    return json_response({"msg": _("The task queue is unavailable")}, 503)
                queued += 1

            logger.info("Queued %d spreadsheet import(s) for %s", queued, current_user.username)
            return json_response({"msg": _(QUEUED_MESSAGE)}, 201)

        @self.router.get("/filedownload/{task_id}")
        def file_download(
            task_id: str,
            current_user: CurrentUser = Depends(require_roles("admin", "processing")),
        ):
            """Download an import's report."""
            from archihub.api.users.services import has_role
            from archihub.core.responses import file_response

            result, status_code = task_result_file(
                task_id, current_user.username, is_admin=has_role(current_user.username, "admin")
            )
            if status_code != 200:
                return json_response(result, status_code)
            return file_response(result, download_name=result.name, as_attachment=True)


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


@shared_task(ignore_result=False, name=TASK_UPDATE)
def update_task(path: str, overwrite: bool, user: str) -> str:
    """Import a spreadsheet and write a report of what happened.

    The uploaded file is removed whether the import succeeds or not.
    """
    from archihub.core import files as filestore
    from archihub.plugins.inventoryMaker import export

    applied: list[dict] = []
    errors: list[dict] = []

    try:
        _import_workbook(path, user, overwrite, applied, errors)
    except Exception as exc:
        logger.exception("Spreadsheet import failed outright")
        errors.append({"index": "-", "id": "-", "error": str(exc)[:300]})
    finally:
        filestore.remove_quietly(path)

    directory = export.user_export_directory(user)
    # The report goes beside the inventories, which is where the download route looks.
    filename = export.write_workbook(
        directory, {"Errores": errors or [{}], "Reporte": applied or [{}]}
    )
    logger.info("Import finished: %d applied, %d errors", len(applied), len(errors))
    return f"/{user}/inventoryMaker/{filename}"


def _import_workbook(path: str, user: str, overwrite: bool, applied: list, errors: list) -> None:
    import pandas as pd

    workbook = pd.ExcelFile(path)
    present = set(workbook.sheet_names)

    for sheet in SHEET_ORDER:
        if sheet in present:
            break
    else:
        raise ValueError(
            f"The workbook has none of the expected sheets ({', '.join(SHEET_ORDER)})"
        )

    frame = _rows(workbook, sheet)
    if len(frame) > MAX_ROWS:
        raise ValueError(f"The workbook has {len(frame)} rows; the limit is {MAX_ROWS}")

    if sheet == "Recursos":
        _import_resources(frame, user, overwrite, applied, errors)
    elif sheet == "Tipo":
        _import_types(frame, user, applied, errors)
    elif sheet == "Estandar":
        _import_forms(frame, _rows(workbook, "Campos", header_row=False), user, applied, errors)
    elif sheet == "Listado":
        _import_lists(frame, user, applied, errors)


def _rows(workbook, sheet: str, *, header_row: bool = True):
    """One sheet as a frame, applying the two-row header convention.

    An inventory's first data row is a legend mapping each column to the metadata
    path it came from; it becomes the header and is dropped from the data.
    """
    import pandas as pd

    frame = pd.read_excel(workbook, sheet_name=sheet)
    if header_row and len(frame) > 0:
        frame.columns = frame.iloc[0]
        frame = frame[1:]
    return frame


def _cell(row, column: str):
    """A cell's value, or ``None`` for anything blank."""
    import pandas as pd

    if column not in row:
        return None
    value = row[column]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


# -- resources --------------------------------------------------------------


def _import_resources(frame, user: str, overwrite: bool, applied: list, errors: list) -> None:
    """Update or create one resource per row."""
    from archihub.api.types import services as types_services

    for index, row in frame.iterrows():
        try:
            resource_id = _cell(row, "id")

            if resource_id:
                existing = _mongo().get_record(
                    "resources",
                    {"_id": _object_id(resource_id)},
                    fields={"_id": 1, "post_type": 1, "status": 1},
                )
                if not existing:
                    errors.append({"index": index, "id": resource_id, "error": "Resource not found"})
                    continue
                post_type = existing["post_type"]
            else:
                post_type = _cell(row, "post_type")
                if not post_type:
                    errors.append({"index": index, "id": "", "error": "No content type given"})
                    continue

            metadata = types_services.get_metadata(post_type)
            if not metadata:
                errors.append({"index": index, "id": resource_id or "", "error": "Unknown content type"})
                continue

            update = _resource_update(row, metadata.get("fields") or [], post_type, overwrite, errors, index)

            parent = _resolve_parent(row, user)
            if parent is _UNRESOLVED:
                errors.append({"index": index, "id": resource_id or "", "error": "Parent not found"})
                continue
            if parent is not None:
                update["parent"] = parent
                update["parents"] = [parent]

            if resource_id:
                from archihub.plugins.framework import data as plugin_data

                payload, status = plugin_data.update_resource(str(resource_id), update)
            else:
                from archihub.api.resources.write import create

                update.setdefault("status", "published")
                update.setdefault("filesIds", [])
                payload, status = create(update, user)

            if status not in (200, 201):
                errors.append({"index": index, "id": resource_id or "", "error": str(payload.get("msg"))[:300]})
                continue

            applied.append({"id": str(resource_id or payload.get("_id", "")), "status": "Actualizado"})
        except Exception as exc:
            logger.warning("Row %s failed", index, exc_info=True)
            errors.append({"index": index, "id": str(_cell(row, "id") or ""), "error": str(exc)[:300]})


#: Sentinel: a parent was named but could not be resolved.
_UNRESOLVED = object()

#: When the operator ticks "blank clears content", an empty cell writes an empty
#: value instead of being skipped.
BLANK_CLEARS = "overwrite"


def _resource_update(row, fields: list[dict], post_type: str, overwrite: bool, errors: list, index) -> dict:
    """Assemble a resource body from one spreadsheet row.

    Values are converted, not validated — ``validate_fields`` downstream applies
    the content type's own rules, so this does not carry a second, divergent
    copy of them.
    """
    from archihub.api.resources.validation import set_value_by_path

    update: dict = {"post_type": post_type}

    for field in fields:
        destiny = field.get("destiny")
        if not destiny or destiny not in row:
            continue

        value = _cell(row, destiny)
        if value is None:
            if overwrite:
                set_value_by_path(update, destiny, None)
            continue

        kind = field.get("type")
        try:
            if kind in ("text", "text-area"):
                set_value_by_path(update, destiny, str(value))
            elif kind == "number":
                set_value_by_path(update, destiny, value)
            elif kind == "simple-date":
                set_value_by_path(update, destiny, _parse_date(value))
            elif kind in ("select", "select-multiple2"):
                resolved = _resolve_options(field, value, errors, index)
                if resolved is None:
                    continue
                set_value_by_path(update, destiny, resolved[0] if kind == "select" else resolved)
            elif kind == "location":
                geocoded = _geocode(str(value))
                if geocoded is None:
                    errors.append(
                        {"index": index, "id": str(_cell(row, "id") or ""),
                         "error": f"Could not resolve coordinates for: {value}"}
                    )
                    continue
                set_value_by_path(update, destiny, [{"coordinates": geocoded}])
        except Exception as exc:
            errors.append({"index": index, "id": str(_cell(row, "id") or ""), "error": str(exc)[:300]})

    return update


def _parse_date(value):
    """A spreadsheet cell as a datetime. A bare year means its first day."""
    from datetime import datetime

    from dateutil import parser

    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        text = f"{text}-01-01"
    return parser.parse(text)


def _resolve_options(field: dict, value, errors: list, index) -> list | None:
    """Vocabulary terms (or ids) to option ids.

    A term that is not in the list is an error on that row, and the row keeps
    whatever it had rather than being written with a partial list.
    """
    import re

    from archihub.api.lists.services import get_by_id as get_list

    list_id = field.get("list")
    if not list_id:
        return None

    listing, status = get_list(str(list_id))
    if status != 200:
        raise ValueError(f"Unknown vocabulary {list_id}")

    options = listing.get("options") or []
    by_id = {str(o.get("id")): str(o.get("id")) for o in options if o.get("id")}
    by_term = {str(o.get("term")): str(o.get("id")) for o in options if o.get("term")}

    resolved = []
    for raw in str(value).split(","):
        term = raw.strip()
        if not term:
            continue
        if re.fullmatch(r"[0-9a-fA-F]{24}", term) and term in by_id:
            resolved.append(by_id[term])
        elif term in by_term:
            resolved.append(by_term[term])
        else:
            raise ValueError(f"Option {term!r} is not in vocabulary {list_id}")

    return resolved or None


def _geocode(text: str) -> list[float] | None:
    """``[lng, lat]`` for a place name, via ArcGIS's public geocoder.

    Both requests carry a timeout, so an unresponsive geocoder cannot hold a
    Celery worker. A row that cannot be geocoded is reported, not fatal.
    """
    import requests

    suggest = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest"
    locate = (
        "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
    )

    try:
        suggestions = requests.get(
            suggest, params={"f": "json", "text": text, "maxSuggestions": 1}, timeout=10
        )
        suggestions.raise_for_status()
        found = (suggestions.json().get("suggestions") or [])
        if not found:
            return None

        candidates = requests.get(
            locate, params={"f": "json", "magicKey": found[0]["magicKey"]}, timeout=10
        )
        candidates.raise_for_status()
        matches = candidates.json().get("candidates") or []
        if not matches:
            return None

        location = matches[0]["location"]
        return [location["x"], location["y"]]
    except Exception:
        logger.warning("Geocoding failed for %r", text[:80])
        return None


def _resolve_parent(row, user: str):
    """The parent named in a row, if any.

    ``None`` when no parent was given, ``_UNRESOLVED`` when one was given and
    could not be found.
    """
    named = _cell(row, "parent")
    if not named:
        return None

    from archihub.api.resources.access import may_view_resource

    candidates: list[dict] = []
    object_id = _object_id(named, quiet=True)
    if object_id is not None:
        candidates.append({"_id": object_id})
    candidates.append({"metadata.firstLevel.title": str(named)})

    resource = _mongo().get_record(
        "resources", {"$or": candidates}, fields={"_id": 1, "post_type": 1}
    )
    if not resource:
        return _UNRESOLVED

    # The caller must be able to see the parent they are filing under.
    if not may_view_resource(str(resource["_id"]), user):
        return _UNRESOLVED

    return {"id": str(resource["_id"]), "post_type": resource.get("post_type")}


# -- the other three sheets -------------------------------------------------


def _import_types(frame, user: str, applied: list, errors: list) -> None:
    """Create or update content types."""
    import json

    from archihub.api.types import services as types_services

    for index, row in frame.iterrows():
        identifier = _cell(row, "id")
        slug = _cell(row, "slug")
        try:
            body = {
                "name": _cell(row, "name"),
                "slug": slug,
                "metadata": _cell(row, "metadata"),
                "description": _cell(row, "description"),
                "icon": _cell(row, "icon"),
                "hierarchical": _cell(row, "hierarchical"),
                "parentType": _json_list(_cell(row, "parentType")),
                "editRoles": _json_list(_cell(row, "editRoles")),
                "viewRoles": _json_list(_cell(row, "viewRoles")),
            }

            existing = types_services.get_by_slug(str(slug)) if slug else None
            if existing:
                payload, status = types_services.update_by_slug(
                    str(slug), {k: v for k, v in body.items() if k != "slug"}, user
                )
                expected = 200
            else:
                payload, status = types_services.create(body, user)
                expected = 201

            if status != expected:
                errors.append({"index": index, "id": identifier or slug, "error": str(payload.get("msg"))[:300]})
                continue
            applied.append({"id": identifier or slug, "status": "Actualizado"})
        except Exception as exc:
            errors.append({"index": index, "id": identifier or slug, "error": str(exc)[:300]})


def _import_forms(frame, fields_frame, user: str, applied: list, errors: list) -> None:
    """Create or update metadata standards.

    Every row is imported, not only the last.
    """
    from archihub.api.forms import services as forms_services

    fields = [
        {
            "label": _cell(row, "label"),
            "type": _cell(row, "type"),
            "destiny": _cell(row, "destiny"),
            "required": bool(_cell(row, "required")),
        }
        for _, row in fields_frame.iterrows()
    ]

    for index, row in frame.iterrows():
        slug = _cell(row, "slug")
        try:
            body = {
                "name": _cell(row, "name"),
                "slug": slug,
                "description": _cell(row, "description"),
                "fields": fields,
            }

            # A metadata standard is looked up in `forms`, not `post_types`.
            existing = _mongo().get_record("forms", {"slug": str(slug)}) if slug else None
            if existing:
                payload, status = forms_services.update_by_slug(str(slug), body, user)
                expected = 200
            else:
                payload, status = forms_services.create(body, user)
                expected = 201

            if status != expected:
                errors.append({"index": index, "id": slug, "error": str(payload.get("msg"))[:300]})
                continue
            applied.append({"id": slug, "status": "Actualizado"})
        except Exception as exc:
            errors.append({"index": index, "id": slug, "error": str(exc)[:300]})


def _import_lists(frame, user: str, applied: list, errors: list) -> None:
    """Create or update controlled vocabularies."""
    from archihub.api.lists import services as lists_services

    for index, row in frame.iterrows():
        identifier = _cell(row, "id")
        try:
            raw_options = _cell(row, "options") or ""
            body = {
                "name": _cell(row, "name"),
                "description": _cell(row, "description"),
                "options": [
                    {"term": term.strip()} for term in str(raw_options).split(",") if term.strip()
                ],
            }

            if identifier and _object_id(identifier, quiet=True) is not None:
                payload, status = lists_services.update_by_id(str(identifier), body, user)
                expected = 200
            else:
                payload, status = lists_services.create(body, user)
                expected = 201

            if status != expected:
                errors.append({"index": index, "id": identifier, "error": str(payload.get("msg"))[:300]})
                continue
            applied.append({"id": identifier, "status": "Actualizado"})
        except Exception as exc:
            errors.append({"index": index, "id": identifier, "error": str(exc)[:300]})


def _json_list(value: Any) -> list:
    """A cell holding a Python-repr list, as written by the exporter."""
    import json

    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        return json.loads(str(value).replace("'", '"'))
    except ValueError:
        return []


def _object_id(value, *, quiet: bool = False):
    from bson.errors import InvalidId
    from bson.objectid import ObjectId

    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        if quiet:
            return None
        raise ValueError(f"Not an identifier: {value!r}")


plugin_info = {
    "name": "Actualización masiva de recursos",
    "description": "Plugin para actualizar masivamente los recursos del gestor documental.",
    "version": "0.1",
    "author": "Néstor Andrés Peña",
    "type": ["lunch"],
    "settings": {
        "settings_lunch": [
            {
                "type": "instructions",
                "title": "Instrucciones",
                "text": (
                    "La actualización masiva de recursos permite actualizar los recursos del "
                    "gestor documental de manera masiva. Para ello, se debe subir un archivo "
                    "Excel con los recursos a actualizar. El archivo debe tener la misma "
                    "estructura que el archivo de exportación de recursos."
                ),
            },
            {
                "type": "file",
                "id": "file",
                "label": "Archivo Excel",
                "required": True,
                "limit": 1,
                "acceptedFiles": [
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ],
            },
            {
                "type": "checkbox",
                "id": "overwrite",
                "label": "Espacio en blanco como borrado de contenido",
                "instructions": (
                    "Si se selecciona esta opción, los campos en blanco en el archivo Excel se "
                    "interpretarán como borrado de contenido."
                ),
                "default": False,
            },
        ]
    },
}


def build() -> MassiveUpdater:
    return MassiveUpdater(SLUG, plugin_info, module_file=__file__)
