"""Content-type business logic.

Port of ``app/api/types/services.py``.

SCOPE: the five CRUD operations behind ``GET/POST /types`` and
``GET/PUT/DELETE /types/{slug}``, plus the cross-domain helpers other modules
import (``get_metadata``, ``get_icon``, ``is_hierarchical``, ``add_resource``,
``get_count``). The two aggregation endpoints - ``POST /types/moreinfo`` and the
public ``POST /types/info`` - are NOT ported yet; they pull in the resources
aggregation pipeline and land with that domain.

RETURN CONVENTION: ``(payload, status_code)`` tuples, matching the legacy
services so the router can pass them straight through and the diff harness sees
byte-identical bodies. Once a route's parity is confirmed, these become
exceptions from ``archihub.core.errors``.

CACHING is not re-enabled here (see the note in ``api/users/services.py``): the
legacy functions carry ``@cacheHandler.cache.cache()`` and correctness depends on
``update_cache()`` invalidating eight of them plus the global cache. That is
restored once the invalidation sites it depends on are ported.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from bson import json_util

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "post_types"

# Upper bound on the -1, -2, ... suffix search when deriving a unique slug.
# The loop is driven by what the database reports, so an existence query that
# always answers "yes" would otherwise spin a request thread forever.
MAX_SLUG_ATTEMPTS = 1000


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    """BSON -> JSON-safe Python.

    Kept identical to the legacy helper for now. It round-trips through a JSON
    string, which is measurably wasteful on large payloads - noted as a
    performance item rather than changed here, because doing so would alter
    serialisation edge cases across every domain at once.
    """
    return json.loads(json_util.dumps(result))


def slugify(name: str) -> str:
    """Derive a URL slug from a display name.

    Extracted from the legacy route handler, where it sat inline. Same rules:
    lowercase, spaces to hyphens, drop anything not alphanumeric or a hyphen,
    trim and collapse hyphens.
    """
    slug = name.lower().replace(" ", "-")
    slug = "".join(char for char in slug if char.isalnum() or char == "-")
    slug = slug.strip("-")
    return slug.replace("--", "-")


def slug_exists(slug: str) -> bool:
    return _mongo().get_record(COLLECTION, {"slug": slug}, {"slug": 1}) is not None


def make_unique_slug(name: str) -> str:
    """Derive a slug from ``name``, suffixing -1, -2, ... until it is free."""
    base = slugify(name)
    candidate = base
    index = 1
    while candidate and slug_exists(candidate):
        if index > MAX_SLUG_ATTEMPTS:
            raise RuntimeError(_("Could not generate a unique slug for {name}", name=base))
        candidate = f"{base}-{index}"
        index += 1
    return candidate


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_all() -> tuple[list | dict, int]:
    """All content types, alphabetically, projected to name/description/slug."""
    try:
        records = _mongo().get_all_records(COLLECTION, {}, sort=[("name", 1)])
        post_types = [
            {
                "name": post_type.get("name"),
                "description": post_type.get("description"),
                "slug": post_type.get("slug"),
            }
            for post_type in records
        ]
        return post_types, 200
    except Exception as exc:
        logger.exception("Could not list content types")
        return {"msg": str(exc)}, 500


def create(body: dict, user: str) -> tuple[dict, int]:
    try:
        if not body.get("name") or not body.get("slug"):
            return {"msg": _("Name and slug are required")}, 400

        from archihub.api.types.schemas import PostTypeCreate

        post_type = PostTypeCreate(**body)
        # model_dump(exclude_unset=True) is what the legacy insert relied on to
        # drop the inert `id` default - see schemas.py. No `id` is declared here
        # at all, so MongoDB assigns the ObjectId.
        _mongo().insert_record(COLLECTION, post_type.model_dump(exclude_none=True))

        _register_log(user, "type_create", {"post_type": {"name": post_type.name, "slug": post_type.slug}})
        invalidate_cache()
        return {"msg": _("Post type created successfully")}, 201
    except Exception as exc:
        logger.exception("Could not create content type")
        return {"msg": str(exc)}, 500


def get_by_slug(slug: str):
    """One content type, with its resolved parent chain and metadata form.

    Returns the document itself on success (NOT a tuple) - matching the legacy
    contract, which the router depends on to distinguish success from error.
    """
    try:
        post_type = _mongo().get_record(COLLECTION, {"slug": slug})
        if not post_type:
            return {"msg": _("Post type not found")}, 404

        post_type.pop("_id", None)
        post_type = parse_result(post_type)

        parents = get_parents(post_type)
        if post_type.get("hierarchical"):
            # A hierarchical type can also parent itself one level down, so it
            # heads its own parent list.
            parents = [
                {
                    "name": post_type["name"],
                    "slug": post_type["slug"],
                    "icon": post_type.get("icon"),
                    "direct": True,
                }
            ] + parents
        post_type["parentsTypes"] = parents

        metadata = post_type.get("metadata")
        if isinstance(metadata, str) and metadata != "":
            post_type["form"] = metadata
            form_result = get_form_by_slug(metadata)
            if isinstance(form_result, tuple):
                payload, _status = form_result
                raise RuntimeError(payload.get("msg", _("Form not found")))
            post_type["metadata"] = {
                "name": form_result["name"],
                "fields": form_result["fields"],
                "slug": form_result["slug"],
            }
        else:
            post_type["metadata"] = None

        return post_type
    except Exception as exc:
        logger.exception("Could not load content type %s", slug)
        return {"msg": str(exc)}, 500


def update_by_slug(slug: str, body: dict, user: str) -> tuple[dict, int]:
    post_type = _mongo().get_record(COLLECTION, {"slug": slug})
    if not post_type:
        return {"msg": _("Post type not found")}, 404

    try:
        from archihub.api.types.schemas import PostTypeUpdate
        from archihub.core.roles import verify_roles_exist

        if "editRoles" in body:
            body["editRoles"] = verify_roles_exist(body["editRoles"])
        if "viewRoles" in body:
            body["viewRoles"] = verify_roles_exist(body["viewRoles"])

        # A type must not be its own parent - that would make the recursive
        # parent walk loop. `.get` rather than `[...]`: the legacy version
        # raised KeyError when parentType was absent from a partial update.
        parent_types = body.get("parentType") or []
        if slug in [parent.get("id") for parent in parent_types]:
            body["parentType"] = [parent for parent in parent_types if parent.get("id") != slug]

        update = PostTypeUpdate(**body)
        _mongo().update_record(COLLECTION, {"slug": slug}, update.model_dump(exclude_unset=True))

        _register_log(user, "type_update", {"post_type": body})
        invalidate_cache()
        return {"msg": _("Post type updated successfully")}, 200
    except Exception as exc:
        logger.exception("Could not update content type %s", slug)
        return {"msg": str(exc)}, 500


def delete_by_slug(slug: str, user: str) -> tuple[dict, int]:
    """Delete a type and soft-delete every resource that used it."""
    mongo = _mongo()
    post_type = mongo.get_record(COLLECTION, {"slug": slug})
    if not post_type:
        return {"msg": _("Post type not found")}, 404

    mongo.delete_record(COLLECTION, {"slug": slug})
    mongo.update_records(
        "resources",
        {"post_type": slug},
        {"status": "deleted", "updatedAt": datetime.now(), "updatedBy": user or "system"},
    )

    _register_log(user, "type_delete", {"post_type": {"name": post_type.get("name"), "slug": post_type.get("slug")}})

    # NOTE: the legacy code fires 'resources_update_by_filters' (plural) here,
    # but the only registration anywhere is 'resources_update_by_filter'
    # (singular) - so this hook has never fired. Reproduced as a no-op rather
    # than silently "fixed": whatever the singular hook does has never run on
    # type deletion, and enabling it now would be a behaviour change to make
    # deliberately, not as a side effect of a rename. See BACKEND_FINDINGS F2.
    _call_hook("resources_update_by_filters", {"slug": slug})

    invalidate_cache()
    return {"msg": _("Post type deleted successfully")}, 200


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def get_parents(post_type: dict, first: bool = True, fields: tuple[str, ...] = ("name", "slug", "icon"), seen: frozenset[str] = frozenset()) -> list[dict]:
    """Walk a type's parent chain, breadth-first, without repeating a type.

    Two corrections to the legacy version:

    * It guarded with ``if not parent and not parent['hierarchical']`` where
      ``parent`` is a list. When the list was empty the first operand was true,
      so Python evaluated the second and raised ``TypeError: list indices must
      be integers``. That is reachable simply by deleting a parent type and then
      opening a child - the declared parent id no longer resolves, the list comes
      back empty, and the request 500s.
    * Mutable default arguments (``fields=[]``, ``post_types=[]``) replaced with
      immutable ones.
    """
    parent_refs = post_type.get("parentType") or []
    if not parent_refs:
        return []

    ids = [ref.get("id") for ref in parent_refs if ref.get("id")]
    # A hierarchical type lists itself as a parent; don't recurse into it.
    if post_type.get("slug") in ids:
        ids.remove(post_type["slug"])
    if not ids:
        return []

    parents = list(_mongo().get_all_records(COLLECTION, {"slug": {"$in": ids}}))
    if not parents:
        # Declared parents that no longer exist (deleted type). Legacy crashed
        # here; an empty chain is the correct answer.
        return []

    parents = [parent for parent in parents if first or parent.get("slug") not in seen]

    resp: list[dict] = []
    next_seen = seen | {parent.get("slug") for parent in parents}

    for parent in parents:
        entry = {"direct": first}
        for field in fields:
            entry[field] = parent.get(field)
        resp.append(entry)

        for ancestor in get_parents(parent, False, fields, next_seen):
            if ancestor.get("slug") not in [existing.get("slug") for existing in resp]:
                resp.append(ancestor)

    return resp


def get_children(post_type: dict, first: bool = True, fields: tuple[str, ...] = ("name", "slug", "icon"), seen: frozenset[str] = frozenset()) -> list[dict]:
    """Walk a type's child chain. Same shape as :func:`get_parents`."""
    children = list(_mongo().get_all_records(COLLECTION, {"parentType.id": post_type.get("slug")}))
    if not children:
        return []

    resp: list[dict] = []
    for child in children:
        if not first and child.get("slug") in seen:
            continue
        entry = {"direct": first}
        for field in fields:
            entry[field] = child.get(field)
        resp.append(entry)

    next_seen = seen | {entry.get("slug") for entry in resp}
    for entry in list(resp):
        descendants = get_children(entry, False, fields, next_seen)
        if not descendants:
            entry["is_last"] = True
        for descendant in descendants:
            if descendant.get("slug") not in [existing.get("slug") for existing in resp]:
                resp.append(descendant)

    return resp


# ---------------------------------------------------------------------------
# Cross-domain helpers
# ---------------------------------------------------------------------------


def get_form_by_slug(slug: str):
    """Resolve a metadata form, annotated with plugin field types.

    Returns the form dict, or ``({'msg': ...}, status)`` on failure - the legacy
    contract, which callers probe with ``isinstance(result, tuple)``.
    """
    try:
        form = _mongo().get_record("forms", {"slug": slug})
        if not form:
            return {"msg": _("Form not found")}, 404

        # Field types come from the forms domain, which is not ported yet.
        # Degrade to an unannotated form rather than failing: the annotation only
        # adds a `plugin` marker used for rendering.
        fields_types: list = []
        try:
            from archihub.api.forms.services import get_all_fields_types

            result = get_all_fields_types()
            if isinstance(result, tuple):
                result = result[0]
            if isinstance(result, list):
                fields_types = result
        except ImportError:
            logger.debug("forms domain not ported yet; skipping field-type annotation")

        for field in form.get("fields", []):
            for field_type in fields_types:
                if field.get("type") == field_type.get("id") and "plugin" in field_type:
                    field["plugin"] = field_type["plugin"]

        form.setdefault("fields", []).insert(
            0,
            {
                "name": "accessRights",
                "label": "Derechos de acceso",
                "required": True,
                "destiny": "accessRights",
                "list": _get_access_rights_id(),
                "type": "select",
            },
        )
        form.pop("_id", None)
        return parse_result(form)
    except Exception as exc:
        logger.exception("Could not load form %s", slug)
        return {"msg": str(exc)}, 500


def get_metadata(post_type_slug: str):
    """The resolved metadata form for a type. Raises when the type is unknown."""
    post_type = _mongo().get_record(COLLECTION, {"slug": post_type_slug})
    if not post_type:
        raise RuntimeError(_("Post type not found"))

    metadata = post_type.get("metadata")
    if isinstance(metadata, str) and metadata != "":
        form_result = get_form_by_slug(metadata)
        if isinstance(form_result, tuple):
            payload, _status = form_result
            raise RuntimeError(payload.get("msg", _("Form not found")))
        return form_result
    return None


def get_icon(post_type_slug: str):
    post_type = _mongo().get_record(COLLECTION, {"slug": post_type_slug}, {"icon": 1})
    if not post_type:
        return {"msg": _("Post type not found")}, 404
    return post_type.get("icon")


def is_hierarchical(post_type_slug: str):
    """``(hierarchical, has_parents)`` for a type."""
    post_type = _mongo().get_record(COLLECTION, {"slug": post_type_slug})
    if not post_type:
        return {"msg": _("Post type not found")}, 404
    return post_type.get("hierarchical"), len(post_type.get("parentType") or []) > 0


def get_count(post_type_slug: str) -> int:
    return _mongo().count("resources", {"post_type": post_type_slug, "status": "published"})


def add_resource(post_type_slug: str, increment: int = 1):
    post_type = _mongo().get_record(COLLECTION, {"slug": post_type_slug}, {"slug": 1})
    if not post_type:
        return {"msg": _("Post type not found")}, 404
    _mongo().increment_record(COLLECTION, {"slug": post_type_slug}, "resourcesCount", increment)


# ---------------------------------------------------------------------------
# Wiring to not-yet-ported domains
# ---------------------------------------------------------------------------
# These keep the types domain independently portable. Each degrades to a no-op
# with a log line rather than failing, and is replaced by a direct import as its
# domain lands.


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)


def _call_hook(name: str, payload: dict) -> None:
    try:
        from archihub.core.hooks import get_hook_handler

        get_hook_handler().call(name, payload)
    except Exception:
        logger.warning("Hook %s failed", name, exc_info=True)


def _get_access_rights_id():
    try:
        from archihub.api.system.services import get_access_rights_id

        return get_access_rights_id()
    except ImportError:
        logger.debug("system domain not ported yet; access rights list unavailable")
        return None


def invalidate_cache() -> None:
    """Placeholder for the cache invalidation the legacy ``update_cache`` did.

    Caching of these lookups is re-enabled with the rest of the invalidation
    sites; until then there is nothing to invalidate and this is a no-op that
    keeps the call sites in place so they are not forgotten.
    """
    logger.debug("types cache invalidation requested (caching not yet enabled)")
