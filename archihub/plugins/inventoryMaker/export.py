"""Building the inventory spreadsheets.

Port of the four ``@shared_task`` bodies in
``app/plugins/inventoryMaker/__init__.py`` plus ``services.py``, which between
them contained **five** copies of the same ``clean_string`` helper and four
copies of the same "make a folder, pick a uuid, write an xlsx" block.

TWO THINGS THAT NEVER GO IN AN INVENTORY

``filepath`` and ``hash``. The records sheet listed both for every file, so an
inventory — a spreadsheet an archivist mails to a colleague — carried the
absolute layout of the server's storage. That is the same field the records API
goes out of its way never to return (see CLAUDE.md: "Two things never leave the
records API"), and exporting it through a different door does not make it less
of a disclosure. The sheet keeps what an inventory is for: what the file is
called, what type it is and how big it is. BACKEND_FINDINGS S34.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

#: Control characters Excel refuses in a cell. Tab, newline and carriage return
#: are kept; everything below 0x20 else is stripped.
_ALLOWED_CONTROL = {9, 10, 13}


def clean_cell(value) -> str:
    """A value Excel will accept, from whatever the database held."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in value if ord(ch) > 31 or ord(ch) in _ALLOWED_CONTROL)


def user_export_directory(user: str):
    """``<user files>/<user>/inventoryMaker``, created if needed.

    Resolved rather than concatenated: the originals built this as
    ``USER_FILES_PATH + '/' + user + '/inventoryMaker'``.
    """
    from archihub.core import files as filestore
    from archihub.core.settings import get_settings

    directory = filestore.resolve_within(get_settings().user_files_path, user, "inventoryMaker")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_workbook(directory: Path, sheets: dict[str, list[dict]], *, name: str | None = None) -> str:
    """Write one spreadsheet and return its name.

    Written under a ``.partial`` name and moved into place, so a run that dies
    mid-write cannot leave a truncated workbook for the download route to serve
    as complete — the same discipline as the bulk-download archives.
    """
    import pandas as pd

    filename = f"{name or uuid.uuid4()}.xlsx"
    destination = directory / filename
    scratch = directory / f"{filename}.partial"

    try:
        with pd.ExcelWriter(scratch) as writer:
            for sheet_name, rows in sheets.items():
                pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)
        scratch.replace(destination)
    finally:
        if scratch.exists():
            scratch.unlink(missing_ok=True)

    return filename


# ---------------------------------------------------------------------------
# Resource rows
# ---------------------------------------------------------------------------


def metadata_fields(post_types: list[str]) -> list[dict]:
    """The union of the declared metadata fields across several content types.

    De-duplicated by ``destiny``, and ``file`` fields dropped — an inventory
    describes the catalogue, not the attachments.
    """
    from archihub.api.types import services as types_services

    seen: dict[str, dict] = {}
    for slug in post_types:
        post_type = types_services.get_by_slug(slug)
        fields = ((post_type or {}).get("metadata") or {}).get("fields") or []
        for field in fields:
            destiny = field.get("destiny")
            if destiny and destiny not in seen and field.get("type") != "file":
                seen[destiny] = field
    return list(seen.values())


def header_row(fields: list[dict]) -> dict:
    """The first data row, mapping each column label to the field it came from.

    Preserved from the original: the sheet's first row is a legend, not data,
    so an inventory can be read back in knowing which metadata path each column
    corresponds to.
    """
    row = {"Tipo de contenido": "post_type", "id": "id", "ident": "ident"}
    for field in fields:
        row[field.get("label", field.get("destiny"))] = field.get("destiny")
    return row


def resource_row(resource: dict, fields: list[dict], option_terms) -> dict:
    """One resource as a spreadsheet row."""
    from archihub.api.resources.validation import get_value_by_path

    row = {
        "id": str(resource.get("_id")),
        "ident": resource.get("ident"),
        "Tipo de contenido": resource.get("post_type"),
    }

    for field in fields:
        label = field.get("label", field.get("destiny"))
        kind = field.get("type")
        value = get_value_by_path(resource, field.get("destiny", ""))

        if kind in ("text", "text-area"):
            row[label] = clean_cell(value)
        elif kind == "select":
            row[f"{label}_id"] = clean_cell(value)
            if value and value != "none":
                row[label] = option_terms([value]).get(str(value), "")
        elif kind == "select-multiple2":
            ids = [str(entry.get("id")) for entry in value or [] if isinstance(entry, dict) and entry.get("id")]
            row[f"{label}_ids"] = ", ".join(ids)
            if ids:
                terms = option_terms(ids)
                row[label] = ", ".join(terms[i] for i in ids if i in terms)
        elif kind == "simple-date":
            row[label] = value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else ""
        elif kind == "number":
            row[label] = value

    return row


def option_term_lookup():
    """A memoised ``[option id] -> term`` resolver.

    The original issued one Mongo query PER SELECT FIELD PER RESOURCE — an
    inventory of 5000 resources with four vocabulary fields made 20000 lookups
    against a few dozen distinct terms.
    """
    from bson.errors import InvalidId
    from bson.objectid import ObjectId

    from archihub.infra.mongo import get_mongo

    cache: dict[str, str] = {}

    def resolve(ids: list) -> dict[str, str]:
        wanted = [str(i) for i in ids if str(i) not in cache]
        if wanted:
            object_ids = []
            for value in wanted:
                try:
                    object_ids.append(ObjectId(value))
                except (InvalidId, TypeError):
                    cache[value] = ""
            if object_ids:
                for row in get_mongo().get_all_records(
                    "options", {"_id": {"$in": object_ids}}, fields={"_id": 1, "term": 1}
                ):
                    cache[str(row["_id"])] = row.get("term", "")
            for value in wanted:
                cache.setdefault(value, "")
        return {str(i): cache.get(str(i), "") for i in ids}

    return resolve


def record_row(record: dict) -> dict:
    """One file as a spreadsheet row.

    No ``filepath`` and no ``hash`` — see the module docstring.
    """
    return {
        "id": str(record.get("_id")),
        "name": record.get("displayName") or record.get("name"),
        "mime": record.get("mime"),
        "size": record.get("size"),
    }
