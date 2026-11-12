"""Roles and access rights.

Role helpers. They live in ``core`` rather than in a domain package because both
the types domain and the auth layer need them, and an authorisation helper with
no obvious home is how a second, divergent copy comes to exist.

HOW THE VOCABULARY IS ASSEMBLED - not obvious, and easy to get wrong:

The ``access_rights`` settings document does NOT contain the roles. It holds the
**ids of two controlled vocabularies** (``user_roles_list`` and
``access_rights_list``), each pointing at a document in the ``lists``
collection. Those lists hold whatever custom terms an administrator has defined.

On top of that, :data:`BUILTIN_ROLES` is always present. These are the roles the
application itself checks for in code (``admin``, ``editor``, ``processing``,
...); they are not configurable, and they exist whether or not an administrator
has created a roles list at all. Reading only the configured list therefore
returns an empty vocabulary on a fresh instance - and since role assignment is
validated against this, every role would be rejected, including the ones
onboarding assigns to the first administrator.
"""

from __future__ import annotations

import logging

from archihub.core.i18n import gettext as _
from archihub.infra.cache import cached

logger = logging.getLogger(__name__)

ACCESS_RIGHTS_SETTING = "access_rights"

# Entry ids within the access_rights settings document, each holding the id of a
# document in the `lists` collection. The positional fallbacks serve documents
# predating the ids.
ROLES_LIST_ENTRY = "user_roles_list"
ROLES_LIST_INDEX = 1
ACCESS_RIGHTS_LIST_ENTRY = "access_rights_list"
ACCESS_RIGHTS_LIST_INDEX = 0

# Roles the application checks for in code. Always available, regardless of what
# any administrator has configured, because authorisation logic references them
# by name. Removing one here silently disables every check that uses it.
BUILTIN_ROLES: tuple[str, ...] = (
    "user",
    "admin",
    "super_editor",   # may edit anything
    "editor",
    "publisher",
    "visualizer",
    "processing",
    "team_lead",      # used to assign tasks to a team
    "transcriber",    # may transcribe audio and video
    "llm",            # may use language models
)


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _settings_entry(entry_id: str, fallback_index: int) -> dict | None:
    record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
    if not record:
        logger.error("The access_rights settings document does not exist")
        return None

    data = record.get("data") or []
    entry = next((item for item in data if item.get("id") == entry_id), None)
    if entry is None and len(data) > fallback_index:
        entry = data[fallback_index]
    return entry


def _list_options(list_id) -> list[dict]:
    """Resolve a controlled vocabulary to its ``{id, term}`` options."""
    if not list_id:
        return []

    from archihub.api.lists.services import get_by_id

    payload, status = get_by_id(str(list_id))
    if status != 200:
        logger.warning("Could not resolve vocabulary %s", list_id)
        return []
    return payload.get("options") or []


def get_roles_id():
    return (_settings_entry(ROLES_LIST_ENTRY, ROLES_LIST_INDEX) or {}).get("value")


def get_access_rights_id():
    return (_settings_entry(ACCESS_RIGHTS_LIST_ENTRY, ACCESS_RIGHTS_LIST_INDEX) or {}).get("value")


@cached("system")
def get_roles() -> dict:
    """Every assignable role: the configured vocabulary in system plus the built-ins."""
    record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
    if record and "roles" not in record and (_settings_entry(ROLES_LIST_ENTRY, ROLES_LIST_INDEX) or _mongo().get_record("lists", {"name": "Roles"})):
        try:
            from archihub.api.system.services import migrate_authorisation_vocabularies

            migrate_authorisation_vocabularies()
            record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
        except Exception:
            logger.debug("Auto-migration of roles deferred", exc_info=True)

    custom = (record or {}).get("roles")
    if custom is None:
        roles_doc = _mongo().get_record("system", {"name": "roles"})
        if roles_doc and "options" in roles_doc:
            custom = roles_doc.get("options")
        elif get_roles_id():
            custom = _list_options(get_roles_id())
        else:
            custom = []

    options = []
    for opt in custom or []:
        if isinstance(opt, dict):
            options.append({"id": str(opt.get("id", "")), "term": str(opt.get("term", ""))})
        elif isinstance(opt, str):
            options.append({"id": opt, "term": opt})

    known = {option.get("id") for option in options}
    for role in BUILTIN_ROLES:
        if role not in known:
            options.append({"id": role, "term": role})

    return {"options": options}


@cached("system")
def get_access_rights() -> dict:
    """The configured access-rights vocabulary, stored in the system collection."""
    record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
    if not record:
        return {"options": []}

    options = record.get("options")
    if not options and (_settings_entry(ACCESS_RIGHTS_LIST_ENTRY, ACCESS_RIGHTS_LIST_INDEX) or _mongo().get_record("lists", {"name": "Niveles de acceso"})):
        try:
            from archihub.api.system.services import migrate_authorisation_vocabularies

            migrate_authorisation_vocabularies()
            record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
            options = (record or {}).get("options")
        except Exception:
            logger.debug("Auto-migration of access rights deferred", exc_info=True)

    if options is not None:
        formatted = []
        for opt in options:
            if isinstance(opt, dict):
                formatted.append({"id": str(opt.get("id", "")), "term": str(opt.get("term", ""))})
            elif isinstance(opt, str):
                formatted.append({"id": opt, "term": opt})
        return {"options": formatted}

    list_id = get_access_rights_id()
    if list_id:
        return {"options": _list_options(list_id)}

    return {"options": []}


def access_rights_document() -> dict:
    """The stored access-rights document from the system collection."""
    record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
    if not record:
        return {"name": "access_rights", "options": []}

    options = record.get("options")
    if not options and (_settings_entry(ACCESS_RIGHTS_LIST_ENTRY, ACCESS_RIGHTS_LIST_INDEX) or _mongo().get_record("lists", {"name": "Niveles de acceso"})):
        try:
            from archihub.api.system.services import migrate_authorisation_vocabularies

            migrate_authorisation_vocabularies()
            record = _mongo().get_record("system", {"name": ACCESS_RIGHTS_SETTING})
            options = (record or {}).get("options")
        except Exception:
            logger.debug("Auto-migration of access rights deferred", exc_info=True)

    if options is not None:
        formatted = []
        for opt in options:
            if isinstance(opt, dict):
                formatted.append({"id": str(opt.get("id", "")), "term": str(opt.get("term", ""))})
            elif isinstance(opt, str):
                formatted.append({"id": opt, "term": opt})
        return {
            "name": record.get("name", "access_rights"),
            "label": record.get("label", "Niveles de acceso"),
            "description": record.get("description", ""),
            "options": formatted,
        }

    list_id = get_access_rights_id()
    if list_id:
        from archihub.api.lists.services import get_by_id

        payload, status = get_by_id(str(list_id))
        if status == 200:
            return payload

    return {
        "name": record.get("name", "access_rights"),
        "label": record.get("label", "Niveles de acceso"),
        "description": record.get("description", ""),
        "options": [],
    }


def verify_roles_exist(compare: list) -> list[str]:
    """Validate role references and reduce them to their ids.

    Raises when a role is not defined - the caller is assigning permissions, and
    silently dropping an unrecognised role would grant narrower access than the
    administrator believes they configured.
    """
    known = {role.get("id") for role in get_roles().get("options", [])}
    result: list[str] = []

    for role in compare or []:
        role_id = role.get("id") if isinstance(role, dict) else role
        if role_id not in known:
            raise ValueError(_("The role {role} does not exist", role=role_id))
        result.append(role_id)

    return result


def verify_access_rights_exist(compare: list) -> list[str]:
    """As :func:`verify_roles_exist`, for access rights."""
    known = {right.get("id") for right in get_access_rights().get("options", [])}
    result: list[str] = []

    for right in compare or []:
        right_id = right.get("id") if isinstance(right, dict) else right
        if right_id not in known:
            raise ValueError(_("The access right {right} does not exist", right=right_id))
        result.append(right_id)

    return result
