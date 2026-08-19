"""Metadata validation for the resource write path.

Port of ``validate_fields``/``validate_files`` from
``app/api/resources/services.py:919`` and the field validators they call in
``app/api/system/services.py:282``.

WHAT THIS IS. ArchiHUB's content model is defined at runtime: an administrator
builds a Form, a content type points at it, and the fields it declares are what
a resource of that type may carry. Nothing about that is expressible as a static
Pydantic schema, which is why this is hand-written validation rather than a
request model (PLAN_FASTAPI.md section 7).

SHAPE CHANGE FROM THE ORIGINAL. ``validate_fields`` took a caller-supplied
``errors`` dict and mutated it, while also returning a possibly-different body -
so callers had to remember to inspect an argument they passed *in*. Here it
returns ``(body, errors)`` and the caller cannot forget half the result.

The original's 285 lines were one function: nine near-identical blocks, each
re-reading the same value up to three times and each repeating the same
condition-field handling. The repetition is what let the defects below hide -
they are present in some copies of the block and not others.
"""

from __future__ import annotations

import datetime
import logging
import numbers

from bson.objectid import ObjectId
from dateutil import parser as date_parser

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

#: Field types that carry no value of their own.
NON_VALUE_TYPES = frozenset({"file", "separator"})

#: Placed in a field whose condition is not met, per type. Types absent from
#: this mapping keep whatever they were sent - matching the original, which
#: applied condition-clearing to exactly these nine.
CLEARED_WHEN_HIDDEN: dict[str, object] = {
    "text": "",
    "text-area": "",
    "select": None,
    "number": None,
    "checkbox": False,
    "select-multiple2": [],
    "userslit": [],
    "userslist": [],
    "simple-date": None,
    "repeater": [],
    "location": None,
}

#: Substituted for an empty title on a resource that is not being published.
UNTITLED = "Sin título"

TITLE_PATH = "metadata.firstLevel.title"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


# ---------------------------------------------------------------------------
# Dotted-path access
# ---------------------------------------------------------------------------


def get_value_by_path(document, path: str):
    """Read a dotted path, or ``None`` if any segment is missing.

    The original tested ``key in value`` without first checking that ``value``
    is a mapping, so a path descending through a string or a list raised
    ``TypeError`` - which the caller caught and reported as a validation error
    against the field, blaming the user's data for a traversal mistake.
    """
    current = document
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def set_value_by_path(document: dict, path: str, value) -> dict:
    """Write a dotted path, creating intermediate dicts as needed."""
    keys = path.split(".")
    current = document
    for key in keys[:-1]:
        nxt = current.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            current[key] = nxt
        current = nxt
    current[keys[-1]] = value
    return document


# ---------------------------------------------------------------------------
# Per-type validators
#
# Each returns (value, error). `value` is what should be stored - most types
# pass it through unchanged, but simple-date parses and relation rewrites. An
# `error` is a translated, user-facing string.
# ---------------------------------------------------------------------------


def _label(field: dict) -> str:
    return field.get("label") or field.get("name") or field.get("destiny") or ""


def _validate_text(value, field):
    if not isinstance(value, str):
        return value, _("The field {label} must be of type string", label=_label(field))
    return value, None


def _validate_number(value, field):
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        return value, _("The field {label} must be a number", label=_label(field))
    return value, None


def _validate_checkbox(value, field):
    if not isinstance(value, bool):
        return value, _("The field {label} must be a boolean", label=_label(field))
    return value, None


def _validate_text_array(value, field):
    if not isinstance(value, list):
        return value, _("The field {label} must be of type array", label=_label(field))

    min_items = field.get("min_items")
    if isinstance(min_items, int) and len(value) < min_items:
        return value, _(
            "The field {label} must have at least {min_items} items",
            label=_label(field),
            min_items=min_items,
        )

    max_items = field.get("max_items")
    if isinstance(max_items, int) and len(value) > max_items:
        return value, _(
            "The field {label} must have at most {max_items} items",
            label=_label(field),
            max_items=max_items,
        )

    if "items" in field and any(not isinstance(item, str) for item in value):
        return value, _("The field {label} must be of type string", label=_label(field))

    return value, None


def _validate_author_array(value, field):
    """Authors are ``"surname,given"`` or ``"surname|given"`` strings.

    At least one half must be non-empty; both empty is meaningless.
    """
    if not isinstance(value, list):
        return value, _("The field {label} must be of type array", label=_label(field))

    for item in value:
        if not isinstance(item, str):
            return value, _("The field {label} must be of type string", label=_label(field))

        parts = item.split(",") if "," in item else item.split("|")
        if len(parts) != 2 or (parts[0] == "" and parts[1] == ""):
            return value, _("The field {label} has an invalid author", label=_label(field))

    return value, None


def _validate_location(value, field):
    if not isinstance(value, list):
        return value, _("The field {label} must be a list", label=_label(field))
    if any(not isinstance(item, dict) for item in value):
        return value, _("The field {label} must be a list of dicts", label=_label(field))
    return value, None


def _validate_simple_date(value, field):
    """Accepts an ISO string or a datetime; stores a datetime.

    Surrounding double quotes are stripped first - the frontend has been known
    to send a JSON-encoded string inside the field.
    """
    if isinstance(value, str):
        try:
            value = date_parser.isoparse(value.replace('"', ""))
        except (ValueError, OverflowError):
            return value, _("The field {label} must be of type date", label=_label(field))

    if not isinstance(value, datetime.datetime):
        return value, _("The field {label} must be of type date", label=_label(field))

    return value, None


def _validate_userslist(value, field):
    if not isinstance(value, list):
        return value, _("The field {label} must be a list of users", label=_label(field))

    for entry in value:
        if not isinstance(entry, dict) or "id" not in entry:
            return value, _("The field {label} must be a list of users", label=_label(field))
        if not _record_exists("users", entry["id"]):
            return value, _("The field {label} must be a list of users", label=_label(field))

    return value, None


def _validate_relation(value, field):
    """Related resources, reduced to ``{id, post_type}`` pairs.

    Every referenced resource must exist and be of the declared
    ``relation_type``; anything else is dropped from the stored value, since a
    dangling reference renders as a broken link on every page that shows it.
    """
    if not isinstance(value, list):
        return value, _("There is an error in {label}", label=_label(field))

    relation_type = field.get("relation_type")
    resolved = []

    for entry in value:
        if not isinstance(entry, dict) or "id" not in entry:
            return value, _("There is an error in {label}", label=_label(field))

        record = _get_resource(entry["id"])
        if not record or record.get("post_type") != relation_type:
            return value, _("There is an error in {label}", label=_label(field))

        resolved.append({"id": entry["id"], "post_type": relation_type})

    return resolved, None


def _record_exists(collection: str, record_id) -> bool:
    object_id = _to_object_id(record_id)
    if object_id is None:
        return False
    return _mongo().get_record(collection, {"_id": object_id}, fields={"_id": 1}) is not None


def _get_resource(resource_id):
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return None
    return _mongo().get_record("resources", {"_id": object_id}, fields={"post_type": 1})


def _to_object_id(value):
    """A malformed id is a validation failure, not a crash.

    The original passed client input straight to ``ObjectId()``, whose
    ``InvalidId`` was caught by the blanket per-field handler and reported to
    the user as the bson library's own message.
    """
    try:
        return ObjectId(value)
    except Exception:
        return None


VALIDATORS = {
    "text": _validate_text,
    "text-area": _validate_text,
    "select": _validate_text,
    "number": _validate_number,
    "checkbox": _validate_checkbox,
    "select-multiple2": _validate_text_array,
    "author": _validate_author_array,
    "location": _validate_location,
    "simple-date": _validate_simple_date,
    # `userslit` is the id the forms builder actually stores; see the note in
    # api/search/mapping.py. Keyed on the correct spelling alone, a User list
    # field went unvalidated - it reached the database as whatever was sent.
    "userslit": _validate_userslist,
    "userslist": _validate_userslist,
    "relation": _validate_relation,
}

#: Subfield types a repeater row may contain. A repeater cannot nest.
REPEATER_VALIDATORS = {
    "text": _validate_text,
    "text-area": _validate_text,
    "number": _validate_number,
    "checkbox": _validate_checkbox,
    "simple-date": _validate_simple_date,
}


# ---------------------------------------------------------------------------
# Conditional fields
# ---------------------------------------------------------------------------


def resolve_condition_field(field: dict, fields: list[dict]) -> dict | None:
    """The checkbox a conditional field depends on, if any.

    ``conditionField`` is an *index* into the form's field list.

    BUG FIXED (BACKEND_FINDINGS F23). The original wrote::

        hasCondition = int(field['conditionField']) if 'conditionField' in field else False
        conditionField = metadata['fields'][hasCondition] if hasCondition else False

    Index ``0`` is falsy, so a field conditioned on the *first* field of the
    form was treated as unconditional - its value was kept even when the
    controlling checkbox was unticked. An out-of-range or non-numeric index
    raised instead, and the blanket handler reported the raw Python error
    against the field.
    """
    if "conditionField" not in field:
        return None

    try:
        index = int(field["conditionField"])
    except (TypeError, ValueError):
        logger.warning(
            "Field %s declares a non-numeric conditionField %r; ignoring it",
            field.get("destiny"),
            field["conditionField"],
        )
        return None

    if not 0 <= index < len(fields):
        logger.warning(
            "Field %s declares conditionField %s, out of range for a form of %s fields",
            field.get("destiny"),
            index,
            len(fields),
        )
        return None

    return fields[index]


def _condition_is_met(field: dict, fields: list[dict], body: dict) -> bool:
    """Whether a conditional field should keep its value.

    Only a checkbox can act as the condition, which is what the original
    supported; a condition on any other type is ignored rather than guessed at.
    """
    condition = resolve_condition_field(field, fields)
    if condition is None or condition.get("type") != "checkbox":
        return True
    return bool(get_value_by_path(body, condition.get("destiny") or ""))


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def validate_fields(body: dict, metadata: dict) -> tuple[dict, dict]:
    """Validate a resource body against its content type's form.

    Returns ``(body, errors)``. ``body`` may have been normalised - dates
    parsed, relations reduced to id/type pairs, hidden conditional fields
    cleared, an absent title filled in.

    REQUIREDNESS ONLY BITES ON PUBLISH. A draft may be missing anything; that is
    the point of a draft. Preserved from the original.
    """
    fields = (metadata or {}).get("fields") or []
    errors: dict[str, str] = {}
    is_published = body.get("status") == "published"

    for field in fields:
        destiny = field.get("destiny")
        field_type = field.get("type")

        if not destiny or field_type in NON_VALUE_TYPES or destiny == "ident":
            continue

        body = _call_validate_field_hook(body, field, metadata, errors)

        if destiny == TITLE_PATH:
            body, title_error = _apply_title_rule(body, field, is_published)
            if title_error:
                errors[destiny] = title_error

        value = get_value_by_path(body, destiny)

        if value in (None, "", [], {}):
            if field.get("required") and is_published and destiny != "accessRights":
                errors.setdefault(
                    destiny, _("The field {label} is required", label=_label(field))
                )
        elif field_type == "repeater":
            body, row_errors = _validate_repeater(body, field, value, is_published)
            errors.update(row_errors)
        else:
            validator = VALIDATORS.get(field_type)
            if validator is not None:
                new_value, error = validator(value, field)
                if error:
                    errors.setdefault(destiny, error)
                elif new_value is not value:
                    body = set_value_by_path(body, destiny, new_value)

        if not _condition_is_met(field, fields, body) and field_type in CLEARED_WHEN_HIDDEN:
            body = set_value_by_path(body, destiny, CLEARED_WHEN_HIDDEN[field_type])

    body, access_error = validate_access_rights(body)
    if access_error:
        errors["accessRights"] = access_error

    return body, errors


def _apply_title_rule(body: dict, field: dict, is_published: bool) -> tuple[dict, str | None]:
    """Every resource needs a label, even an unfinished one.

    A published resource must have a real title if the form says so; anything
    else gets a placeholder, because a resource with no title at all is
    unfindable in every listing and tree that renders it.
    """
    value = get_value_by_path(body, TITLE_PATH)
    if value:
        return body, None

    if field.get("required") and is_published:
        return body, _("The field {label} is required", label=_label(field))

    return set_value_by_path(body, TITLE_PATH, UNTITLED), None


def _validate_repeater(
    body: dict, field: dict, rows, is_published: bool
) -> tuple[dict, dict[str, str]]:
    """A repeatable group of simple subfields.

    Errors are keyed ``<destiny>.<row>.<subfield>`` rather than by the bare
    subfield name. The original keyed them by subfield alone, so two rows with
    the same problem collapsed into one message and the user could not tell
    which row to fix - and two *different* repeaters sharing a subfield name
    overwrote each other.
    """
    errors: dict[str, str] = {}
    destiny = field.get("destiny")

    if not isinstance(rows, list):
        return body, {destiny: _("The field {label} must be of type array", label=_label(field))}

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors[f"{destiny}.{index}"] = _(
                "The field {label} must be of type array", label=_label(field)
            )
            continue

        for subfield in field.get("subfields") or []:
            sub_destiny = subfield.get("destiny")
            validator = REPEATER_VALIDATORS.get(subfield.get("type"))
            if not sub_destiny or validator is None:
                continue

            # Subfields carry `name` where top-level fields carry `label`;
            # `_label` already falls back, so no mutation of the form is needed.
            # (The original assigned subfield['label'] = subfield['name'],
            # writing into the shared, cached form definition.)
            key = f"{destiny}.{index}.{sub_destiny}"
            value = row.get(sub_destiny)

            if value in (None, "", [], {}):
                if subfield.get("required") and is_published:
                    errors[key] = _("The field {label} is required", label=_label(subfield))
                continue

            new_value, error = validator(value, subfield)
            if error:
                errors[key] = error
            else:
                row[sub_destiny] = new_value

    return body, errors


def validate_access_rights(body: dict) -> tuple[dict, str | None]:
    """Access rights must name a configured right, or be absent.

    ``'public'`` and absence both mean "no restriction" and are stored as
    ``None`` - which is what :mod:`archihub.api.resources.access` reads. An
    empty string is rejected rather than silently treated as public: it is what
    a half-filled form sends, and quietly publishing on that basis is the wrong
    default for an archive.
    """
    from archihub.core.roles import get_access_rights

    if "accessRights" not in body:
        body["accessRights"] = None
        return body, None

    value = body["accessRights"]

    if value is None:
        return body, None
    if value == "":
        return body, _("The resource must have valid access rights")
    if value == "public":
        body["accessRights"] = None
        return body, None

    known = {option.get("id") for option in get_access_rights().get("options", [])}
    if value not in known:
        return body, _("The resource must have valid access rights")

    return body, None


def _call_validate_field_hook(body: dict, field: dict, metadata: dict, errors: dict) -> dict:
    """Give plugins a chance to validate or rewrite a field.

    Kept because plugins genuinely register here. A failing hook must not take
    the whole save down: it is logged and the field is validated normally, which
    is strictly safer than the original's behaviour of letting the exception
    surface as that field's error message.
    """
    from archihub.core.hooks import get_hook_handler

    try:
        result = get_hook_handler().call("validate_field", body, field, metadata, errors)
    except Exception:
        logger.exception("validate_field hook failed for %s", field.get("destiny"))
        return body

    return result if isinstance(result, dict) else body


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def validate_files(files, metadata: dict) -> dict[str, str]:
    """Enforce each file field's ``maxFiles`` ceiling.

    Files are tagged with the field they belong to (``tag``, or ``filetag`` on
    older records); untagged files count as the generic ``file`` bucket.
    """
    file_fields = [f for f in ((metadata or {}).get("fields") or []) if f.get("type") == "file"]
    if not file_fields:
        return {}

    known_tags = {f.get("filetag") for f in file_fields}
    counts: dict[str, int] = {}

    for entry in files or []:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("tag") or entry.get("filetag") or "file"
        if tag in known_tags:
            counts[tag] = counts.get(tag, 0) + 1

    errors: dict[str, str] = {}
    for field in file_fields:
        tag = field.get("filetag")
        max_files = field.get("maxFiles")
        # '' and 0 both mean "no ceiling" in stored form definitions.
        if not isinstance(max_files, int) or max_files <= 0:
            continue
        if counts.get(tag, 0) > max_files:
            errors[tag] = _(
                "The field {label} must have a maximum of {maxFiles} files",
                label=_label(field),
                maxFiles=max_files,
            )

    return errors
