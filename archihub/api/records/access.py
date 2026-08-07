"""Who may see a record.

A record is a file, and a file is visible through the resources it belongs to.
Two things gate it: the record's own ``accessRights``, and the *effective*
access right of each resource it hangs off - which is itself inherited from that
resource's ancestors (see ``archihub/api/resources/access.py``).

ONE CORRECTION, AND IT IS THE REASON THIS IS A MODULE. The original wrote:

    if not has_right(current_user, record['accessRights']) and not has_right(current_user, 'admin')

``has_right`` looks up **access rights**, not roles. Administrators are an
``admin`` *role*, and no instance defines an access right by that name - so the
second clause is always false and the intended administrator bypass never
existed. The parent check a few lines below has no bypass clause at all.

The effect is that administrators are refused access to restricted records,
which fails closed and so is a usability defect rather than a hole - but it
means "an administrator can always read it" was never true here, and any
deployment appearing to rely on it was relying on records that had no access
rights set. Corrected to a role check, and recorded as BACKEND_FINDINGS F27.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def user_access_rights(username: str | None) -> list:
    from archihub.api.resources.access import user_access_rights as rights_of

    return rights_of(username)


def holds(username: str, required) -> bool:
    """Whether the caller holds a required access right.

    ``required`` is a single id on records, but list-valued documents exist -
    the same shape question as on resources - so both are handled.
    """
    if not required:
        return True

    held = set(user_access_rights(username))
    if isinstance(required, list):
        return bool(set(required) & held)
    return required in held


def may_view_record(username: str, record: dict, is_admin: bool) -> bool:
    """Whether this caller may see a record and its derivatives.

    Administrators may. Everyone else must hold the record's own access right,
    if it declares one, **and** the effective right of every resource it is
    filed under - a file reachable from a reserved series is reserved, however
    it was reached.
    """
    if is_admin:
        return True

    if not holds(username, record.get("accessRights")):
        return False

    return all(_parent_permits(username, parent) for parent in record.get("parent") or [])


def _parent_permits(username: str, parent) -> bool:
    from archihub.api.resources.access import effective_access_right

    if not isinstance(parent, dict) or not parent.get("id"):
        return True

    resource = _load_resource(parent["id"])
    if not resource:
        # A dangling parent reference. It cannot grant access, and it must not
        # deny it either - the record's other parents decide.
        return True

    return holds(username, effective_access_right(resource))


def _load_resource(resource_id: str):
    from bson.objectid import ObjectId

    try:
        object_id = ObjectId(resource_id)
    except Exception:
        return None

    return _mongo().get_record(
        "resources", {"_id": object_id}, fields={"accessRights": 1, "parents": 1}
    )
