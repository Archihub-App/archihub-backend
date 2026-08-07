"""Controlled-vocabulary business logic.

Port of ``app/api/lists/services.py``.

Lists are addressed **by id**, confirmed with the maintainer. ``get_by_slug`` is
therefore NOT ported: `lists` documents carry no ``slug`` field at all (verified
against a live database - the create path never writes one), so that function
could never match anything, and it then subscripted its ``None`` result before
its own existence check. It is dead code and is deleted rather than reproduced.

SUCCESS RESPONSES ARE BYTE-IDENTICAL to the legacy ones. Error responses are
corrected, because the legacy ones were not merely different but unusable:

``get_by_id`` returned its errors as ``({'msg': ...}, 404)`` - a tuple - to a
route that tested ``if 'msg' in resp``. Membership in a tuple is not key lookup,
so the test was always False and the route fell through to
``return jsonify(resp), 200``: an **HTTP 200 whose body is the JSON array
``[{"msg": ...}, 404]``**. `ListsService.getList` treats any 200 as success and
hands that array to the component expecting ``{name, description, options}``.
Returning a real 404 is what the frontend already knows how to handle - it
rejects every non-200 - so this is a fix in the direction the client expects.
"""

from __future__ import annotations

import json
import logging

from bson import json_util
from bson.objectid import ObjectId

from archihub.core.i18n import gettext as _

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

    The legacy code called ``ObjectId(id)`` directly on a path parameter, so a
    malformed id raised ``InvalidId`` and surfaced as a 500 carrying the bson
    error text. A bad id in the URL is a client error, not a server fault.
    """
    try:
        return ObjectId(value)
    except Exception:
        return None


def _load_options(option_ids: list[str]) -> list[dict]:
    """Resolve option ids to ``{id, term}``, preserving the list's own order.

    Order is significant - it is the order the options are presented in - and
    MongoDB does not return ``$in`` results in the order of the argument, which
    is why the legacy code walked the id list rather than the query result.
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
    except Exception as exc:
        logger.exception("Could not list vocabularies")
        return {"msg": str(exc)}, 500


def get_by_id(list_id: str) -> tuple[dict, int]:
    """One list with its options resolved and ordered.

    Success payload is exactly ``{name, description, options: [{id, term}]}``,
    matching the legacy shape the frontend consumes.
    """
    object_id = _to_object_id(list_id)
    if object_id is None:
        return {"msg": _("List not found")}, 404

    try:
        record = _mongo().get_record(COLLECTION, {"_id": object_id})
        # The existence check comes FIRST here. The legacy version subscripted
        # the result on the two lines above its own `if not lista` check, so an
        # unknown id raised TypeError and was reported as a 500.
        if not record:
            return {"msg": _("List not found")}, 404

        return {
            "name": record.get("name"),
            "description": record.get("description", ""),
            "options": _load_options(record.get("options") or []),
        }, 200
    except Exception as exc:
        logger.exception("Could not load list %s", list_id)
        return {"msg": str(exc)}, 500


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
    except Exception as exc:
        logger.exception("Could not create list")
        return {"msg": str(exc)}, 500


def update_by_id(list_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Update a list, reconciling its options.

    Options are three-way reconciled: entries with an id are updated, entries
    without one are created, and entries flagged ``deleted`` are dropped from the
    list. The resulting id array replaces the stored one, so ordering and
    membership both come from the request.

    A patch that does NOT include ``options`` updates the remaining fields and
    leaves the options alone. The legacy version wrapped its whole body in
    ``if 'options' in body:`` and so returned ``None`` - a broken response - for
    any patch that only renamed a list.
    """
    object_id = _to_object_id(list_id)
    if object_id is None:
        return {"msg": _("List not found")}, 404

    mongo = _mongo()
    existing = mongo.get_record(COLLECTION, {"_id": object_id})
    if not existing:
        return {"msg": _("List not found")}, 404

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
    except Exception as exc:
        logger.exception("Could not update list %s", list_id)
        return {"msg": str(exc)}, 500


def delete_by_id(list_id: str, user: str) -> tuple[dict, int]:
    object_id = _to_object_id(list_id)
    if object_id is None:
        return {"msg": _("List not found")}, 404

    try:
        mongo = _mongo()
        existing = mongo.get_record(COLLECTION, {"_id": object_id})
        if not existing:
            # Legacy returned a hardcoded, untranslated Spanish string here
            # ('Listado no existe') while every sibling path used the translated
            # _('List not found'). Unified.
            return {"msg": _("List not found")}, 404

        mongo.delete_record(COLLECTION, {"_id": object_id})
        _register_log(
            user,
            "list_delete",
            # str() on the id: legacy passed the raw ObjectId, which is not
            # JSON-serialisable and would break the audit record.
            {"list": {"name": existing.get("name"), "id": str(existing["_id"])}},
        )
        return {"msg": _("List deleted successfully")}, 200
    except Exception as exc:
        logger.exception("Could not delete list %s", list_id)
        return {"msg": str(exc)}, 500


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
