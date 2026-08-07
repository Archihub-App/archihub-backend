"""Role and access-right definitions.

Port of the role helpers in ``app/utils/functions.py``. They live in ``core``
rather than a domain package because both the types domain and the auth layer
need them, and the legacy placement in a 41,000-line grab-bag module is what
allowed a second, divergent copy of the authorisation helpers to appear
alongside them.

Roles are stored inside the ``access_rights`` settings document rather than in
their own collection.
"""

from __future__ import annotations

import logging

from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

ACCESS_RIGHTS_SETTING = "access_rights"

# Index of the roles entry within the settings document's `data` array. The
# legacy code hard-codes data[1] for roles and data[0] for access rights; the
# lookups below prefer matching by `id` and fall back to the position, so a
# reordered settings document degrades instead of silently returning the wrong
# list. Same defensive shape as the locale lookup in core/i18n.py.
_ROLES_INDEX = 1
_ACCESS_RIGHTS_INDEX = 0


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


def get_roles() -> dict:
    """The roles definition, as ``{'options': [{'id': ..., 'term': ...}, ...]}``."""
    entry = _settings_entry("roles", _ROLES_INDEX)
    value = (entry or {}).get("value") or {}
    if isinstance(value, dict):
        return value
    return {"options": value if isinstance(value, list) else []}


def get_roles_id() -> list | None:
    entry = _settings_entry("roles", _ROLES_INDEX)
    return (entry or {}).get("value")


def get_access_rights() -> dict:
    entry = _settings_entry("access_rights", _ACCESS_RIGHTS_INDEX)
    value = (entry or {}).get("value") or {}
    if isinstance(value, dict):
        return value
    return {"options": value if isinstance(value, list) else []}


def verify_roles_exist(compare: list[dict]) -> list[str]:
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


def verify_access_rights_exist(compare: list[dict]) -> list[str]:
    """As :func:`verify_roles_exist`, for access rights."""
    known = {right.get("id") for right in get_access_rights().get("options", [])}
    result: list[str] = []

    for right in compare or []:
        right_id = right.get("id") if isinstance(right, dict) else right
        if right_id not in known:
            raise ValueError(_("The access right {right} does not exist", right=right_id))
        result.append(right_id)

    return result
