"""Turning a stored resource into the document that gets indexed.

Port of the body of ``index_resources_task`` in
``app/api/system/tasks/elasticTasks.py``, extracted from the loop that drove it.

WHY IT IS ITS OWN MODULE: so you can ask what a *given* resource indexes as.
Built inline in a loop that swallows exceptions, the only observable is whether
the run reported a number at the end - which it does whether the documents were
accepted or every one of them was rejected.

WHAT IS AND IS NOT INDEXED. The index backs search, and search results are shown
to anonymous visitors, so a field reaches the index only if the content type
declares it as searchable metadata. ``file`` is excluded by the schema itself,
and ``repeater`` is excluded DELIBERATELY - see ``_apply_repeater_dates`` for
why turning it on could remove resources from the index.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class _TextOnly(HTMLParser):
    """Collects character data, discarding tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def strip_html(text: str | None) -> str | None:
    """Article markup to plain text, with punctuation spacing tidied."""
    if text is None:
        return None
    parser = _TextOnly()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # HTMLParser is lenient, but a deeply broken fragment can still raise.
        # An unsearchable paragraph is better than an unindexed resource.
        logger.warning("Could not strip markup from an article paragraph")
        return None
    cleaned = re.sub(r"\s+", " ", parser.text)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])([^\s])", r"\1 \2", cleaned)
    return cleaned.strip()


def to_utc_iso(value: object) -> object:
    """A datetime as the millisecond-precision UTC string Elasticsearch wants.

    Naive values are read as UTC, which is what the rest of the application
    assumes of what it stores. Anything that is not a datetime passes through -
    the schema says a field is a date, the stored document may disagree.
    """
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def get_by_path(document: object, path: str):
    """``a.b.c`` out of nested dicts, or ``None`` if any step is missing."""
    value = document
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value.get(key)
    return value


def set_by_path(document: dict, path: str, value) -> dict:
    """``a.b.c`` into nested dicts, creating the intermediate levels.

    An intermediate step that exists but is not a dict is REPLACED rather than
    subscripted. The original did ``temp = temp[key]`` unconditionally, so a
    schema declaring both ``date`` and ``date.from`` raised ``TypeError`` on the
    second - inside the swallow-everything handler, so that resource silently
    vanished from the index.
    """
    keys = path.split(".")
    target = document
    for key in keys[:-1]:
        if not isinstance(target.get(key), dict):
            target[key] = {}
        target = target[key]
    target[keys[-1]] = value
    return document


# ---------------------------------------------------------------------------
# Field kinds
# ---------------------------------------------------------------------------


def _apply_select_multiple(document: dict, resource: dict, destiny: str) -> None:
    """A multi-select stores ``[{term, id}, ...]``; index the terms only.

    De-duplicated, and ORDERED - the original built a ``set`` and handed the
    result straight to Elasticsearch, so the same resource produced a different
    document on every run purely from set iteration order. That makes any
    diff of the index against itself meaningless, and it is why this sorts.
    """
    value = get_by_path(resource, destiny)
    if not isinstance(value, list):
        return
    terms = sorted({str(entry["term"]) for entry in value if isinstance(entry, dict) and "term" in entry})
    set_by_path(document, destiny, terms)


def _apply_repeater_dates(resource: dict, field: dict) -> None:
    """Normalise the dates inside a repeater's rows.

    REPEATERS ARE NOT INDEXED, and this function is what is left of the
    original's attempt to index them: it converted the dates of each row in
    place, on the RESOURCE, and never wrote the result into the document - and
    the branch above that copies ordinary fields excludes ``repeater``. So a
    repeater's contents have never been searchable, despite the mapping
    declaring the field.

    Reproduced rather than fixed on purpose. Indexing them is not a one-line
    change: rows are free-form, so a field holding a number in one resource and
    text in another is a mapping conflict that makes Elasticsearch REJECT the
    whole document - i.e. turning this on could remove resources from the index
    that are in it today., to be done with a
    mapping decision behind it rather than in passing.
    """
    rows = get_by_path(resource, field.get("destiny", ""))
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        for subfield in field.get("subfields") or []:
            if subfield.get("type") != "simple-date":
                continue
            existing = get_by_path(row, subfield.get("destiny", ""))
            if existing is not None:
                set_by_path(row, subfield["destiny"], to_utc_iso(existing))


def _apply_location(document: dict, destiny: str, centroid_lookup) -> None:
    """A location field to GeoJSON points Elasticsearch can search.

    Two stored shapes, both from the same form control: an explicit
    ``coordinates`` pair, or a set of administrative levels naming a boundary
    whose centroid stands in for it.
    """
    value = get_by_path(document, destiny)
    if not isinstance(value, list):
        return

    points: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue

        if "coordinates" in entry:
            coordinates = entry.get("coordinates")
            if not coordinates:
                continue
            if len(coordinates) != 2:
                # The original raised here, aborting the whole resource inside
                # the swallowing handler. One unusable point should cost that
                # point, not the resource's searchability.
                logger.warning("Ignoring a location with %d coordinates", len(coordinates))
                continue
            points.append({"type": "Point", "coordinates": [coordinates[0], coordinates[1]]})
            continue

        # Administrative levels, most specific first: level_2 within level_1
        # within level_0. The first one that resolves wins.
        for level in (2, 1, 0):
            entry_level = entry.get(f"level_{level}")
            if not isinstance(entry_level, dict):
                continue
            ident = entry_level.get("ident")
            if not ident:
                continue
            parent = None
            if level > 0:
                parent_level = entry.get(f"level_{level - 1}")
                parent = parent_level.get("ident") if isinstance(parent_level, dict) else None
            centroid = centroid_lookup(ident, parent, level)
            if centroid:
                points.extend(centroid)
                break

    set_by_path(document, destiny, points)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class NotIndexable(Exception):
    """This resource is deliberately left out of the index."""


def build_resource_document(
    resource: dict,
    fields: list[dict],
    *,
    is_article: bool,
    records: list[dict],
    centroid_lookup,
    hook_call=None,
) -> dict:
    """The Elasticsearch document for one resource.

    ``records`` is the already-resolved file list (see ``resolve_records``);
    it is passed in rather than looked up here so a full run can batch those
    queries instead of issuing one per resource.
    """
    document: dict = {}

    for field in fields:
        kind = field.get("type")
        destiny = field.get("destiny") or ""
        if not destiny:
            continue

        if kind not in ("file", "simple-date", "repeater"):
            value = get_by_path(resource, destiny)
            if value is not None:
                set_by_path(document, destiny, value)
        elif kind == "simple-date":
            value = get_by_path(resource, destiny)
            if isinstance(value, datetime):
                set_by_path(document, destiny, to_utc_iso(value))

        if kind == "select-multiple2":
            _apply_select_multiple(document, resource, destiny)
        elif kind == "repeater":
            _apply_repeater_dates(resource, field)
        elif kind == "location":
            _apply_location(document, destiny, centroid_lookup)

    if "status" not in resource:
        # Nothing decides whether this is publicly visible, so nothing may
        # decide it here either.
        raise NotIndexable("resource has no status")

    document["post_type"] = resource.get("post_type")
    document["article"] = _article_text(resource) if is_article else None

    if "createdAt" in resource:
        document["createdAt"] = to_utc_iso(resource["createdAt"])
    for key in ("parents", "parent", "ident"):
        if key in resource:
            document[key] = resource[key]

    document["status"] = resource["status"]
    document["files"] = len(resource.get("filesObj") or [])
    document["records"] = records

    # Default before the hook so a plugin can see and change it; the resource's
    # own value is applied after, so it always wins.
    document["accessRights"] = "public"

    if hook_call is not None:
        extended = hook_call("resource_index", document, resource)
        if extended:
            document = extended

    if resource.get("accessRights"):
        document["accessRights"] = resource["accessRights"]

    return document


def _article_text(resource: dict) -> str | None:
    """An article's paragraphs flattened into one searchable string."""
    parts: list[str] = []
    for block in resource.get("articleBody") or []:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        content = strip_html(block.get("content"))
        if content:
            parts.append(content)
    return " ".join(parts) if parts else None


def file_order(file_entry: dict) -> int:
    """A file's position, tolerating a stored value that is not a number."""
    try:
        return int(file_entry.get("order", 0))
    except (TypeError, ValueError):
        return 0


def resolve_records(resource: dict, record_types: dict[str, str]) -> list[dict]:
    """The resource's files, in display order, with their processing type.

    ``record_types`` maps a record id to its ``fileProcessing.type``; a record
    missing from it has not been processed yet and is left out, as in the
    original.
    """
    files = sorted(
        (f for f in (resource.get("filesObj") or []) if isinstance(f, dict) and "id" in f),
        key=file_order,
    )
    return [
        {"id": str(f["id"]), "type": record_types[str(f["id"])], "tag": f.get("tag")}
        for f in files
        if str(f["id"]) in record_types
    ]
