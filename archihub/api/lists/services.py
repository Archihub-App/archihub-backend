"""Controlled-vocabulary business logic.

Lists are addressed **by id**; `lists` documents carry no ``slug``.

Errors are real ``(payload, status)`` responses - a 404 for an unknown list - so
the frontend's ``!response.ok`` check sees them as errors.
"""

from __future__ import annotations

from collections.abc import Iterable
import json
import logging

from bson import json_util
from bson.objectid import ObjectId

from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import ROLE_FAILURE_STATUS
from archihub.infra.cache import cached

logger = logging.getLogger(__name__)

COLLECTION = "lists"
OPTIONS_COLLECTION = "options"
FORMS_COLLECTION = "forms"
PREVIEW_OPTIONS_LIMIT = 3


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    return json.loads(json_util.dumps(result))


def _to_object_id(value: str) -> ObjectId | None:
    """Parse an id, returning None when it is not a valid ObjectId.

    A bad id in the URL is a client error, not a server fault.
    """
    try:
        return ObjectId(value)
    except Exception:
        return None


def _load_options(option_ids: list[str], mongo=None) -> list[dict]:
    """Resolve option ids to ``{id, term}``, preserving the list's own order.

    Order is significant - it is the order the options are presented in - and
    MongoDB does not return ``$in`` results in the order of the argument, so the
    result is re-ordered by the id list.
    """
    object_ids = [oid for oid in (_to_object_id(str(i)) for i in option_ids) if oid]
    if not object_ids:
        return []

    client = mongo or _mongo()
    records = list(client.get_all_records(OPTIONS_COLLECTION, {"_id": {"$in": object_ids}}))
    by_id = {str(record["_id"]): record for record in records}

    resolved = []
    for option_id in option_ids:
        record = by_id.get(str(option_id))
        if record:
            resolved.append({"id": str(record["_id"]), "term": record.get("term")})
    return resolved


def _load_preview_options(records: list[dict], mongo) -> dict[str, list[dict]]:
    """Resolve up to ``PREVIEW_OPTIONS_LIMIT`` ordered options per list in one batch."""
    batch_oids: list[ObjectId] = []
    seen_oids: set[str] = set()
    for record in records:
        for option_id in (record.get("options") or [])[:PREVIEW_OPTIONS_LIMIT]:
            oid = _to_object_id(str(option_id))
            if oid and str(oid) not in seen_oids:
                seen_oids.add(str(oid))
                batch_oids.append(oid)

    by_id: dict[str, dict] = {}
    if batch_oids:
        option_docs = list(
            mongo.get_all_records(
                OPTIONS_COLLECTION,
                {"_id": {"$in": batch_oids}},
                fields={"term": 1},
            )
        )
        by_id = {str(doc["_id"]): doc for doc in option_docs}

    previews: dict[str, list[dict]] = {}
    for record in records:
        list_id = str(record["_id"])
        raw_ids = [str(item) for item in (record.get("options") or [])]
        resolved: list[dict] = []
        for option_id in raw_ids[:PREVIEW_OPTIONS_LIMIT]:
            doc = by_id.get(option_id)
            if doc:
                resolved.append({"id": str(doc["_id"]), "term": doc.get("term")})

        if len(resolved) < PREVIEW_OPTIONS_LIMIT and len(raw_ids) > PREVIEW_OPTIONS_LIMIT:
            extra = _load_options(raw_ids[PREVIEW_OPTIONS_LIMIT:], mongo=mongo)
            resolved.extend(extra[: PREVIEW_OPTIONS_LIMIT - len(resolved)])

        previews[list_id] = resolved
    return previews


def _sort_form_refs(forms: Iterable[dict]) -> list[dict[str, str]]:
    """Normalise, deduplicate by slug, and sort ``{slug, name}`` form references."""
    deduped: dict[str, dict[str, str]] = {}
    for entry in forms:
        if not isinstance(entry, dict) or not entry.get("slug"):
            continue
        slug = str(entry["slug"])
        deduped[slug] = {"slug": slug, "name": str(entry.get("name") or "")}
    return sorted(
        deduped.values(),
        key=lambda item: (item["name"].lower(), item["slug"]),
    )


def extract_form_list_ids(form: dict) -> list[str]:
    """Return deduplicated list ids referenced anywhere in ``form['fields']``."""
    found: list[str] = []
    seen: set[str] = set()

    def _walk(fields) -> None:
        if not isinstance(fields, list):
            return
        for field in fields:
            if not isinstance(field, dict):
                continue
            list_id = field.get("list")
            if list_id is not None and not isinstance(list_id, bool):
                cleaned = str(list_id).strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    found.append(cleaned)
            subfields = field.get("subfields")
            if isinstance(subfields, list):
                _walk(subfields)

    _walk(form.get("fields") if isinstance(form, dict) else None)
    return found


def sync_form_usage(
    form_slug: str,
    form_name: str,
    list_ids: Iterable[str],
    *,
    previous_slug: str | None = None,
    mongo=None,
) -> None:
    """Keep each list's ``forms`` field aligned with a created, updated, or deleted form."""
    if not form_slug and not previous_slug:
        return

    client = mongo or _mongo()
    target_ids = {
        str(item).strip()
        for item in list_ids
        if item is not None and not isinstance(item, bool) and str(item).strip()
    }
    match_slugs = {slug for slug in (form_slug, previous_slug) if slug}

    target_oids = [oid for oid in (_to_object_id(item) for item in target_ids) if oid is not None]
    or_clauses: list[dict] = []
    if target_oids:
        or_clauses.append({"_id": {"$in": target_oids}})
    if match_slugs:
        or_clauses.append({"forms.slug": {"$in": sorted(match_slugs)}})
    if not or_clauses:
        return

    query = or_clauses[0] if len(or_clauses) == 1 else {"$or": or_clauses}
    candidates = list(client.get_all_records(COLLECTION, query, fields={"forms": 1}))

    for doc in candidates:
        doc_id = str(doc["_id"])
        raw_forms = doc.get("forms")
        current_forms = _sort_form_refs(raw_forms) if isinstance(raw_forms, list) else []

        is_target = doc_id in target_ids
        has_match = any(entry["slug"] in match_slugs for entry in current_forms)
        if not is_target and not has_match:
            continue

        updated_forms = [entry for entry in current_forms if entry["slug"] not in match_slugs]
        if is_target and form_slug:
            updated_forms.append({"slug": str(form_slug), "name": str(form_name or "")})
        updated_forms = _sort_form_refs(updated_forms)

        if raw_forms != updated_forms:
            client.update_record(COLLECTION, {"_id": doc["_id"]}, {"forms": updated_forms})


def sync_all_forms_usage(*, mongo=None, list_docs: list[dict] | None = None) -> dict[str, list[dict[str, str]]]:
    """Reconcile every list's ``forms`` field against all stored metadata forms."""
    client = mongo or _mongo()
    forms = list(
        client.get_all_records(FORMS_COLLECTION, {}, fields={"slug": 1, "name": 1, "fields": 1})
    )

    usage_by_list_id: dict[str, dict[str, dict[str, str]]] = {}
    for form in forms:
        slug = form.get("slug")
        if not slug:
            continue
        form_ref = {"slug": str(slug), "name": str(form.get("name") or "")}
        for list_id in extract_form_list_ids(form):
            usage_by_list_id.setdefault(list_id, {})[str(slug)] = form_ref

    records = (
        list_docs
        if list_docs is not None
        else list(client.get_all_records(COLLECTION, {}, fields={"forms": 1}))
    )

    resolved: dict[str, list[dict[str, str]]] = {}
    for doc in records:
        list_id = str(doc["_id"])
        desired_forms = _sort_form_refs(usage_by_list_id.get(list_id, {}).values())
        resolved[list_id] = desired_forms
        if doc.get("forms") != desired_forms:
            client.update_record(COLLECTION, {"_id": doc["_id"]}, {"forms": desired_forms})
    return resolved


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_all() -> tuple[list | dict, int]:
    try:
        mongo = _mongo()
        records = list(mongo.get_all_records(COLLECTION, {}, sort=[("name", 1)]))
        synced_forms: dict[str, list[dict[str, str]]] | None = None
        if any("forms" not in record or not isinstance(record.get("forms"), list) for record in records):
            synced_forms = sync_all_forms_usage(mongo=mongo, list_docs=records)

        previews = _load_preview_options(records, mongo)
        lists = []
        for record in records:
            list_id = str(record["_id"])
            raw_options = record.get("options") or []
            forms = (
                synced_forms[list_id]
                if synced_forms is not None and list_id in synced_forms
                else _sort_form_refs(record.get("forms") or [])
            )
            lists.append(
                {
                    "name": record.get("name"),
                    "id": list_id,
                    "options": previews.get(list_id, []),
                    "optionsCount": len(raw_options),
                    "forms": forms,
                }
            )
        return lists, 200
    except Exception:
        logger.exception("Could not list vocabularies")
        return {"msg": _("Error while processing the request")}, 500


@cached("lists", "options")
def _resolved_list(list_id: str) -> dict | None:
    """A list with its options resolved, or ``None`` if it does not exist.

    Cached: forms render the same list in many fields, and every write to
    either collection invalidates it.
    """
    record = _mongo().get_record(COLLECTION, {"_id": ObjectId(list_id)})
    if not record:
        return None
    return {
        "name": record.get("name"),
        "description": record.get("description", ""),
        "options": _load_options(record.get("options") or []),
    }


def get_by_id(list_id: str) -> tuple[dict, int]:
    """One list with its options resolved and ordered.

    Success payload is exactly ``{name, description, options: [{id, term}]}``,
    the shape the frontend consumes.
    """
    object_id = _to_object_id(list_id)
    if object_id is None:
        return {"msg": _("List not found")}, 404

    try:
        payload = _resolved_list(str(object_id))
        if payload is None:
            return {"msg": _("List not found")}, 404
        return payload, 200
    except Exception:
        logger.exception("Could not load list %s", list_id)
        return {"msg": _("Error while processing the request")}, 500


def get_option_by_id(option_id: str):
    """Resolve a single option. Returns None for an absent/sentinel id."""
    if not option_id or option_id == "none":
        return None

    object_id = _to_object_id(option_id)
    if object_id is None:
        return None

    option = _mongo().get_record(OPTIONS_COLLECTION, {"_id": object_id})
    if not option:
        return None

    return {"_id": option_id, "term": option.get("term")}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create(body: dict, user: str) -> tuple[dict, int]:
    try:
        mongo = _mongo()

        option_ids = []
        for option in body.get("options") or []:
            term = option.get("term") if isinstance(option, dict) else option
            result = mongo.insert_record(OPTIONS_COLLECTION, {"term": term})
            option_ids.append(str(result.inserted_id))

        payload = {
            "name": body.get("name"),
            "description": body.get("description", ""),
            "options": option_ids,
            "forms": [],
        }
        new_list = mongo.insert_record(COLLECTION, payload)

        _register_log(
            user, "list_create", {"list": {"name": payload["name"], "id": str(new_list.inserted_id)}}
        )
        return {"msg": _("List created successfully"), "id": str(new_list.inserted_id)}, 201
    except Exception:
        logger.exception("Could not create list")
        return {"msg": _("Error while processing the request")}, 500


def _relabels_authorisation_vocabulary(options: list, user: str) -> str | None:
    """The first option id in ``options`` whose term change is role administration.

    That is an option the roles or access-rights vocabulary references, decided
    by the option rather than by the list it is edited through, since one option
    may belong to several lists. Changing its term needs a built-in role of
    ``lists.manage``; ``None`` when nothing needs one or ``user`` holds one.
    Keeping the term, or dropping the option from another list, is not a change.
    """
    from archihub.api.users.services import has_builtin_permission
    from archihub.core.roles import get_access_rights_id, get_roles_id

    wanted = {
        str(option["id"]): option.get("term")
        for option in options
        if option.get("id") and not option.get("deleted")
    }
    if not wanted:
        return None

    mongo = _mongo()
    protected: set[str] = set()
    for vocabulary_id in (get_roles_id(), get_access_rights_id()):
        vocabulary_oid = _to_object_id(str(vocabulary_id)) if vocabulary_id else None
        vocabulary = mongo.get_record(COLLECTION, {"_id": vocabulary_oid}) if vocabulary_oid else None
        protected.update(str(i) for i in (vocabulary or {}).get("options") or [])

    shared = [oid for oid in (_to_object_id(i) for i in wanted if i in protected) if oid]
    if not shared:
        return None
    stored = {
        str(record["_id"]): record.get("term")
        for record in mongo.get_all_records(OPTIONS_COLLECTION, {"_id": {"$in": shared}})
    }
    changed = [i for i in map(str, shared) if wanted[i] != stored.get(i)]
    if not changed or has_builtin_permission(user, "lists.manage"):
        return None
    return changed[0]


def update_by_id(list_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Update a list, reconciling its options.

    Options are three-way reconciled: entries with an id are updated, entries
    without one are created, and entries flagged ``deleted`` are dropped from the
    list. The resulting id array replaces the stored one, so ordering and
    membership both come from the request.

    A patch that does NOT include ``options`` updates the remaining fields and
    leaves the options alone.

    AN OPTION ID MUST ALREADY BELONG TO THIS LIST, AND APPEAR ONCE. An update
    changes only this list's own options, each at most once. A request naming
    any other id, or one id twice, is refused whole before anything is written.
    """
    object_id = _to_object_id(list_id)
    if object_id is None:
        return {"msg": _("List not found")}, 404

    mongo = _mongo()
    existing = mongo.get_record(COLLECTION, {"_id": object_id})
    if not existing:
        return {"msg": _("List not found")}, 404

    named = [str(option["id"]) for option in body.get("options") or [] if option.get("id")]
    if len(named) != len(set(named)):
        # One entry per option: what is checked is exactly what is written.
        return {"msg": _("An option may appear only once in a list")}, 400

    owned = {str(option_id) for option_id in existing.get("options") or []}
    for option in body.get("options") or []:
        if option.get("id") and str(option["id"]) not in owned:
            logger.info("Refused %s an edit to option %s outside list %s", user, option["id"], list_id)
            return {"msg": _("The option {option} does not belong to this list", option=option["id"])}, 400

    relabelled = _relabels_authorisation_vocabulary(body.get("options") or [], user)
    if relabelled is not None:
        logger.info("Refused %s a change to authorisation option %s via list %s", user, relabelled, list_id)
        return {"msg": _("You don't have the required authorization")}, ROLE_FAILURE_STATUS

    try:
        update: dict = {}
        for field in ("name", "description"):
            if body.get(field) is not None:
                update[field] = body[field]

        if body.get("options") is not None:
            option_ids: list[str] = []
            for option in body["options"]:
                if option.get("deleted"):
                    continue
                if option.get("id"):
                    existing_id = _to_object_id(option["id"])
                    if existing_id is None:
                        continue
                    mongo.update_record(
                        OPTIONS_COLLECTION, {"_id": existing_id}, {"term": option.get("term")}
                    )
                    option_ids.append(option["id"])
                else:
                    result = mongo.insert_record(OPTIONS_COLLECTION, {"term": option.get("term")})
                    option_ids.append(str(result.inserted_id))
            update["options"] = option_ids

        if not update:
            # Nothing to change; report success rather than writing an empty $set,
            # which MongoDB rejects.
            return {"msg": _("List updated successfully")}, 200

        mongo.update_record(COLLECTION, {"_id": object_id}, update)
        _register_log(user, "list_update", {"list": body})
        _invalidate_role_caches(list_id)

        return {"msg": _("List updated successfully")}, 200
    except Exception:
        logger.exception("Could not update list %s", list_id)
        return {"msg": _("Error while processing the request")}, 500


def delete_by_id(list_id: str, user: str) -> tuple[dict, int]:
    object_id = _to_object_id(list_id)
    if object_id is None:
        return {"msg": _("List not found")}, 404

    try:
        mongo = _mongo()
        existing = mongo.get_record(COLLECTION, {"_id": object_id})
        if not existing:
            return {"msg": _("List not found")}, 404

        mongo.delete_record(COLLECTION, {"_id": object_id})
        _register_log(
            user,
            "list_delete",
            # str() on the id: a raw ObjectId would break the audit record.
            {"list": {"name": existing.get("name"), "id": str(existing["_id"])}},
        )
        return {"msg": _("List deleted successfully")}, 200
    except Exception:
        logger.exception("Could not delete list %s", list_id)
        return {"msg": _("Error while processing the request")}, 500


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _invalidate_role_caches(list_id: str) -> None:
    """Roles and access rights are themselves stored as lists.

    Editing one of those two lists changes the authorisation vocabulary, so any
    cached copy has to go.
    """
    try:
        from archihub.core.roles import get_access_rights_id, get_roles_id

        if list_id in (get_access_rights_id(), get_roles_id()):
            logger.info("Authorisation vocabulary changed (list %s)", list_id)
    except Exception:
        logger.debug("Could not check role cache invalidation", exc_info=True)


def _register_log(user: str, action_key: str, metadata: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action_key, metadata)
    except ImportError:
        logger.debug("logs domain not ported yet; audit entry %s not written", action_key)
