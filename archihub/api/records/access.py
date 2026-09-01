"""Who may see a record.

A record is a file, and a file is visible through the resources it belongs to.
Two things gate it: the record's own ``accessRights``, and the *effective*
access right of each resource it hangs off - which is itself inherited from that
resource's ancestors (see ``archihub/api/resources/access.py``).

THE RULE THIS MODULE EXISTS TO STATE ONCE. Written inline, it comes out as:

    if not has_right(current_user, record['accessRights']) and not has_right(current_user, 'admin')

``has_right`` looks up **access rights**, not roles. Administrators are an
``admin`` *role*, and no instance defines an access right by that name - so the
second clause is always false and the intended administrator bypass never
existed. The parent check a few lines below has no bypass clause at all.

The effect is that administrators are refused access to restricted records,
which fails closed and so is a usability defect rather than a hole - but it
means "an administrator can always read it" was never true here, and any
deployment appearing to rely on it was relying on records that had no access
rights set. It is a role check.
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

    return all(_parent_permits(username, parent) for parent in _containing_parents(record))


def is_public(record: dict) -> bool:
    """Whether an anonymous caller may see this record.

    A record is public when it restricts nothing itself **and** every resource
    it is filed under is public - which carries the published check and the
    inherited access right with it, through ``resources.access.is_public``.

    The legacy public layer checked only that the *record* declared no access
    right and that each parent's own right was absent. It never checked that the
    parent was published, so **a file attached to an unpublished draft was
    served to anonymous callers** through `/records/public/<id>` as soon as
    somebody knew its id. Ids are not secret: they appear in the authenticated
    listing every cataloguer can see.
    """
    if record.get("accessRights"):
        return False

    parents = _containing_parents(record)
    if not parents:
        # A record filed nowhere is reachable through no public resource, so
        # nothing publishes it. The legacy code treated this as public.
        return False

    return all(_parent_is_public(parent) for parent in parents)


def _containing_parents(record: dict) -> list:
    """The entries in ``record["parent"]`` that actually place it in the archive.

    Attaching a file as a saved view's thumbnail reuses the same attach
    machinery resources use, so it appends an entry here too - but a view
    lives in its own collection, is never a member of the resources
    hierarchy, and grants no access right of its own. Left in, it reads to
    both rules above as an ordinary parent that can never be resolved, which
    denies (`is_public`) or is silently permitted (`may_view_record`) for the
    wrong reason - a record used as a view's thumbnail must be judged solely
    by the resources it is genuinely filed under.
    """
    from archihub.api.views.services import VIEW_POST_TYPE

    return [
        parent
        for parent in record.get("parent") or []
        if not (isinstance(parent, dict) and parent.get("post_type") == VIEW_POST_TYPE)
    ]


def _parent_is_public(parent) -> bool:
    from archihub.api.resources.access import is_public as resource_is_public

    if not isinstance(parent, dict) or not parent.get("id"):
        return False

    resource = _load_resource(parent["id"], public=True)
    if not resource:
        return False

    return resource_is_public(resource)


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


def _load_resource(resource_id: str, public: bool = False):
    from bson.objectid import ObjectId

    try:
        object_id = ObjectId(resource_id)
    except Exception:
        return None

    fields = {"accessRights": 1, "parents": 1}
    if public:
        # The public rule additionally needs the publication state and the type,
        # so it can apply the same three gates the authenticated rule does.
        fields.update({"status": 1, "post_type": 1})

    return _mongo().get_record("resources", {"_id": object_id}, fields=fields)
