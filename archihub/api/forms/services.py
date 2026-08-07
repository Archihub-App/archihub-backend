"""Metadata-form business logic.

Port of ``app/api/forms/services.py``.

A form defines the fields a content type catalogues. Each field declares a
``destiny`` - the dotted path it writes into on a resource - and every form on
the instance contributes to one combined schema. Two forms may therefore not
declare the same destiny with conflicting types, which is what
:func:`update_main_schema` enforces before any write.
"""

from __future__ import annotations

import json
import logging

from bson import json_util

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "forms"

ACCESS_RIGHTS_FIELD = {
    "name": "accessRights",
    "label": "Derechos de acceso",
    "required": True,
    "destiny": "accessRights",
    "type": "select",
}

TITLE_DESTINY = "metadata.firstLevel.title"

# Field types that may coexist on the same destiny across different forms -
# both resolve to the same stored shape.
INTERCHANGEABLE_TYPES = {"select", "select-multiple2"}

# Types exempt from the "destiny must start with metadata" rule: they either
# write nowhere (separator) or to their own area (file).
DESTINY_EXEMPT_TYPES = {"separator", "file"}

# Upper bound on the -1, -2, ... suffix search when deriving a unique slug.
MAX_SLUG_ATTEMPTS = 1000


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    return json.loads(json_util.dumps(result))


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------


def _base_field_types() -> list[dict]:
    """The built-in field-type catalogue, plus any a plugin contributes.

    Copies are returned because the labels are translated in place by the
    caller; handing out the module-level definitions would let one request's
    translation leak into the next.
    """
    from archihub.api.forms.field_types import FIELD_TYPES

    fields = [dict(field_type) for field_type in FIELD_TYPES]

    # Plugins extend the catalogue through this hook. It returns the (possibly
    # replaced) list; a falsy return means "no contribution", not "empty list".
    contributed = _call_hook_with_result("get_fields_types", fields)
    return contributed or fields


def get_all_fields_types() -> tuple[list | dict, int]:
    """Every available field type, with labels translated per request.

    Labels are translated on each call rather than cached, because the instance
    locale is a setting that can change without a restart.
    """
    try:
        fields = _base_field_types()
        for field in fields:
            if field.get("label"):
                field["label"] = _(field["label"])
        return fields, 200
    except Exception as exc:
        logger.exception("Could not list field types")
        return {"msg": str(exc)}, 500


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_all() -> tuple[list | dict, int]:
    try:
        records = _mongo().get_all_records(COLLECTION, {}, sort=[("name", 1)])
        forms = [
            {
                "name": record.get("name"),
                "description": record.get("description"),
                "slug": record.get("slug"),
            }
            for record in records
        ]
        return forms, 200
    except Exception as exc:
        logger.exception("Could not list forms")
        return {"msg": str(exc)}, 500


def exists(slug: str) -> bool:
    """Cheap existence check.

    The legacy code answered this by calling the full ``get_by_slug``, which
    resolves the access-rights list, prepends a synthetic field and serialises
    the whole document - all discarded. It is called in a loop while deriving a
    unique slug.
    """
    return _mongo().get_record(COLLECTION, {"slug": slug}, {"slug": 1}) is not None


def get_by_slug(slug: str) -> tuple[dict, int]:
    """One form, with the synthetic ``accessRights`` field prepended.

    That field is not stored - every form gets it at read time, because access
    rights are configured per resource but presented as part of the form.
    """
    try:
        form = _mongo().get_record(COLLECTION, {"slug": slug})
        if not form:
            # Legacy returned an untranslated hardcoded Spanish string here while
            # its siblings used the translated message.
            return {"msg": _("Form not found")}, 404

        field = dict(ACCESS_RIGHTS_FIELD)
        field["list"] = _get_access_rights_id()
        form.setdefault("fields", []).insert(0, field)

        form.pop("_id", None)
        return parse_result(form), 200
    except Exception as exc:
        logger.exception("Could not load form %s", slug)
        return {"msg": str(exc)}, 500


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Identical rules to the types domain - see ``api/types/services.py``."""
    slug = name.lower().replace(" ", "-")
    slug = "".join(char for char in slug if char.isalnum() or char == "-")
    slug = slug.strip("-")
    return slug.replace("--", "-")


def make_unique_slug(desired: str) -> str:
    """Return ``desired`` (or a slugified name) with -1, -2, ... until free.

    The legacy loop reassigned the suffixed value back into the variable it was
    suffixing::

        while status == 200:
            body['slug'] = body['slug'] + '-' + str(index)

    so a third form named "Test" became ``test-1-2`` and a fourth
    ``test-1-2-3``. The types domain got this right by keeping the base
    separate; forms did not. Aligned here. This only affects newly generated
    slugs, so no existing URL changes.
    """
    base = desired or ""
    candidate = base
    index = 1
    while candidate and exists(candidate):
        if index > MAX_SLUG_ATTEMPTS:
            # Bounded on purpose. This loop is driven entirely by what the
            # database reports, so an existence query that always answers "yes"
            # spins a request thread forever. The legacy version had no bound.
            raise RuntimeError(
                _("Could not generate a unique slug for {name}", name=base)
            )
        candidate = f"{base}-{index}"
        index += 1
    return candidate


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_form(form: dict) -> None:
    """Validate a form definition, raising on the first problem.

    NOTE: this mutates ``form`` - fields without a truthy ``setCondition`` have
    their now-meaningless condition keys stripped. Preserved, because the write
    that follows depends on the normalised shape.
    """
    has_title = False

    for field in form.get("fields") or []:
        if "label" not in field:
            raise ValueError(_("Error: the field must have a label"))
        if field["label"] == "":
            raise ValueError(_("Error: the field label cannot be empty"))

        field_type = field.get("type")

        if "destiny" in field:
            destiny = field["destiny"]
            if destiny == "ident":
                raise ValueError(_("Error: the field cannot have destiny equal to ident"))
            if not destiny.startswith("metadata") and field_type not in DESTINY_EXEMPT_TYPES:
                raise ValueError(_("Error: the field destiny must start with metadata"))
            if destiny == TITLE_DESTINY:
                has_title = True
                if field_type != "text":
                    raise ValueError(
                        _(
                            "Error: the field with destiny equal to "
                            "metadata.firstLevel.title must be of type text"
                        )
                    )

        if field_type == "file" and "filetag" not in field:
            raise ValueError(_("Error: the field with type file must have the filetag attribute"))

        if field_type == "repeater":
            subfields = field.get("subfields")
            if subfields is None:
                raise ValueError(
                    _("Error: the field with type repeater must have the subfields attribute")
                )
            if not isinstance(subfields, list):
                raise ValueError(_("Error: the subfields attribute must be a list"))
            if not subfields:
                raise ValueError(_("Error: the subfields attribute cannot be empty"))
            for subfield in subfields:
                for required in ("destiny", "name", "type"):
                    if required not in subfield:
                        raise ValueError(_("Error: the subfield must have a {key}", key=required))

        # Normalise conditional fields: without a condition, the condition
        # parameters are meaningless and must not be persisted.
        if not field.get("setCondition"):
            field["setCondition"] = False
            for key in ("conditionField", "conditionType", "conditionValueText"):
                field.pop(key, None)

        if field.get("accessRights"):
            valid = {option.get("id") for option in _get_access_rights().get("options", [])}
            for right in field["accessRights"]:
                if right not in valid:
                    raise ValueError(_("The value of the accessRights field is not valid"))

        _call_hook("validate_form_field", field)

    if not has_title:
        raise ValueError(
            _(
                "Error: the form must have a field with destiny equal to "
                "metadata.firstLevel.title"
            )
        )


def update_main_schema(new_form: dict | None = None, updated_form: dict | None = None) -> dict:
    """Verify the combined field schema across every form stays consistent.

    Two forms may both write to ``metadata.firstLevel.title``, but not with
    conflicting types - the destination is one field on the resource. Raises when
    a proposed form would introduce such a conflict.

    When checking an update, the form being replaced is excluded so it does not
    conflict with its own previous definition.
    """
    schema: dict[str, str] = {}

    filters: dict = {}
    if updated_form and updated_form.get("slug"):
        filters["slug"] = {"$ne": updated_form["slug"]}

    def _absorb(fields: list[dict]) -> None:
        for field in fields or []:
            field_type = field.get("type")
            if "destiny" not in field or field_type in DESTINY_EXEMPT_TYPES:
                continue

            destiny = field["destiny"]
            existing = schema.get(destiny)
            if existing is None:
                schema[destiny] = field_type
            elif existing != field_type and not (
                field_type in INTERCHANGEABLE_TYPES and existing in INTERCHANGEABLE_TYPES
            ):
                raise ValueError(
                    _("Error: the field {field} has two different types", field=destiny)
                )

    for form in _mongo().get_all_records(COLLECTION, filters, sort=[("name", 1)]):
        _absorb(form.get("fields"))

    for candidate in (new_form, updated_form):
        if candidate:
            _absorb(candidate.get("fields"))

    return schema


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create(body: dict, user: str) -> tuple[dict, int]:
    try:
        desired = body.get("slug") or slugify(body.get("name", ""))
        body["slug"] = make_unique_slug(desired)

        validate_form(body)
        update_main_schema(new_form=body)

        from archihub.api.forms.schemas import FormCreate

        form = FormCreate(**body)
        _mongo().insert_record(COLLECTION, form.model_dump(exclude_none=True))

        _register_log(user, "form_create", {"form": {"name": form.name, "slug": form.slug}})
        _clear_cache()
        return {"msg": _("Form created successfully")}, 201
    except Exception as exc:
        logger.exception("Could not create form")
        return {"msg": str(exc)}, 500


def update_by_slug(slug: str, body: dict, user: str) -> tuple[dict, int]:
    try:
        # Existence is checked FIRST. The legacy version ran validate_form and
        # update_main_schema before looking the form up, so an update aimed at a
        # form that does not exist did all that work (and could raise a
        # validation error) before reporting the real problem: 404.
        form = _mongo().get_record(COLLECTION, {"slug": slug}, {"slug": 1})
        if not form:
            return {"msg": _("Form not found")}, 404

        body.setdefault("slug", slug)
        validate_form(body)
        update_main_schema(updated_form=body)

        from archihub.api.forms.schemas import FormUpdate

        update = FormUpdate(**body)
        _mongo().update_record(COLLECTION, {"slug": slug}, update.model_dump(exclude_unset=True))

        _register_log(user, "form_update", {"form": body})
        _clear_cache()
        return {
            "msg": _(
                "Form updated successfully. If new fields were added or the type of any "
                "existing fields was changed, it is important to regenerate the index from "
                "the option in the system settings"
            )
        }, 200
    except Exception as exc:
        logger.exception("Could not update form %s", slug)
        return {"msg": str(exc)}, 500


def delete_by_slug(slug: str, user: str) -> tuple[dict, int]:
    """Delete a form, unless a content type still uses it."""
    try:
        mongo = _mongo()
        form = mongo.get_record(COLLECTION, {"slug": slug})
        if not form:
            return {"msg": _("Form not found")}, 404

        in_use = mongo.count("post_types", {"metadata": slug})
        if in_use > 0:
            return {"msg": _("The form is being used by a post type")}, 400

        mongo.delete_record(COLLECTION, {"slug": slug})
        _register_log(
            user, "form_delete", {"form": {"name": form.get("name"), "slug": form.get("slug")}}
        )
        _clear_cache()
        # 204, matching FormService.deleteForm which requires exactly that. The
        # router sends it without a body: HTTP forbids content on a 204, and the
        # client never reads one.
        return {"msg": _("Form deleted successfully")}, 204
    except Exception as exc:
        logger.exception("Could not delete form %s", slug)
        return {"msg": str(exc)}, 500


def duplicate_by_slug(slug: str, user: str) -> tuple[dict, int]:
    """Copy a form under a new, unique slug."""
    try:
        form = _mongo().get_record(COLLECTION, {"slug": slug})
        if not form:
            return {"msg": _("Form not found")}, 404

        form.pop("_id", None)
        form["name"] = f"{form.get('name', '')} (copia)"
        # Cleared so create() derives a fresh unique slug from the new name.
        form["slug"] = ""
        return create(form, user)
    except Exception as exc:
        logger.exception("Could not duplicate form %s", slug)
        return {"msg": str(exc)}, 500


# ---------------------------------------------------------------------------
# Wiring to not-yet-ported domains
# ---------------------------------------------------------------------------


def _get_access_rights():
    try:
        from archihub.core.roles import get_access_rights

        return get_access_rights()
    except Exception:
        logger.debug("Could not read access rights", exc_info=True)
        return {"options": []}


def _get_access_rights_id():
    try:
        from archihub.core.roles import get_access_rights_id

        return get_access_rights_id()
    except Exception:
        logger.debug("Could not read access rights id", exc_info=True)
        return None


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)


def _call_hook(name: str, payload) -> None:
    try:
        from archihub.core.hooks import get_hook_handler

        get_hook_handler().call(name, payload)
    except Exception:
        logger.warning("Hook %s failed", name, exc_info=True)


def _call_hook_with_result(name: str, payload):
    """Fire a hook whose synchronous return value is the point of calling it."""
    try:
        from archihub.core.hooks import get_hook_handler

        return get_hook_handler().call(name, payload)
    except Exception:
        logger.warning("Hook %s failed", name, exc_info=True)
        return None


def _clear_cache() -> None:
    logger.debug("forms cache invalidation requested (caching not yet enabled)")
