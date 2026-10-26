"""Controlled-vocabulary business logic.

Lists are addressed **by id**; `lists` documents carry no ``slug``.

Errors are real ``(payload, status)`` responses - a 404 for an unknown list - so
the frontend's ``!response.ok`` check sees them as errors.
"""

from __future__ import annotations

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


def _load_options(option_ids: list[str]) -> list[dict]:
    """Resolve option ids to ``{id, term}``, preserving the list's own order.

    Order is significant - it is the order the options are presented in - and
    MongoDB does not return ``$in`` results in the order of the argument, so the
    result is re-ordered by the id list.
    """
    object_ids = [oid for oid in (_to_object_id(str(i)) for i in option_ids) if oid]
    if not object_ids:
        return []

    records = list(_mongo().get_all_records(OPTIONS_COLLECTION, {"_id": {"$in": object_ids}}))
    by_id = {str(record["_id"]): record for record in records}

    resolved = []
    for option_id in option_ids:
        record = by_id.get(str(option_id))
        if record:
            resolved.append({"id": str(record["_id"]), "term": record.get("term")})
    return resolved


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_all() -> tuple[list | dict, int]:
    try:
        records = _mongo().get_all_records(COLLECTION, {}, sort=[("name", 1)])
        lists = [{"name": record.get("name"), "id": str(record["_id"])} for record in records]
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
        }
        new_list = mongo.insert_record(COLLECTION, payload)

        _register_log(
            user, "list_create", {"list": {"name": payload["name"], "id": str(new_list.inserted_id)}}
        )
        return {"msg": _("List created successfully")}, 201
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
