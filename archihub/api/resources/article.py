"""The article editor.

Port of the three ``articleBody`` services in
``app/api/resources/services.py`` (``get_article_body``,
``update_article_body``, ``add_article_block_comment``).

``articleBody`` is a list of blocks - the long-form narrative some content types
carry alongside their catalogue metadata. Blocks embed structured references
(favourites, snaps, locations, uploaded records) and can carry reviewer
comments. The frontend renders them through DOMPurify; see the sanitisation
rule in the ``upgrade_front`` section of CLAUDE.md.

AUTHORISATION IS TIGHTENED HERE. See :func:`may_edit` - the original's write
path had a hole this closes, and closing it is a real behaviour change for
instances relying on the default. It is written up as BACKEND_FINDINGS S17.
"""

from __future__ import annotations

import datetime
import logging
import numbers

from bson.objectid import ObjectId

from archihub.api.resources import access, hierarchy
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "resources"

MSG_UNAUTHORIZED = "You don't have the required authorization"
MSG_NOT_FOUND = "Resource does not exist"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _to_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _now() -> datetime.datetime:
    """``utcnow()`` is deprecated and returns a naive datetime."""
    return datetime.datetime.now(datetime.timezone.utc)


def serialise(value):
    """Make a block tree JSON-safe.

    Blocks are free-form and may contain datetimes at any depth (a comment's
    ``createdAt``, a location's captured timestamp), which are not serialisable.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat() + "Z"
    if isinstance(value, list):
        return [serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: serialise(item) for key, item in value.items()}
    return value


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def may_edit(user: str, resource: dict, is_admin: bool) -> bool:
    """Whether this caller may change a resource's article.

    THE RULE, and why it is not the original's (BACKEND_FINDINGS S17):

    * Administrators always may.
    * Nobody may edit what they cannot read. The original checked access rights
      on the *read* route and not on the write one, so a resource whose contents
      were reserved could still have its narrative rewritten by anyone.
    * If the content type declares ``editRoles``, holding one of them is
      required and sufficient - exactly the original's rule.
    * If it declares none, the original required **nothing at all**: any
      authenticated user could overwrite any resource's article. That is not a
      sensible default for an archive, so it falls back to the rule the ordinary
      resource-update route already applies - the resource's creator, or a
      ``super_editor``.

    THIS IS NARROWER THAN THE ORIGINAL in that last case, which is the common
    one: most content types declare no ``editRoles``. An instance where people
    routinely edit articles on resources they did not create will notice. The
    remedies are both ordinary configuration - declare ``editRoles`` on the
    content type, or grant ``super_editor`` - and either is preferable to the
    alternative of leaving article content world-writable.
    """
    if is_admin:
        return True

    if not access.may_view_resource(user, resource, is_admin):
        return False

    edit_roles = hierarchy.type_roles(resource.get("post_type") or "")["editRoles"]
    if edit_roles:
        return access.holds_edit_role(user, resource.get("post_type"), is_admin)

    return access.owns_or_supervises(user, resource, is_admin)


def _load(resource_id: str, fields: dict | None = None):
    object_id = _to_object_id(resource_id)
    if object_id is None:
        return None
    return _mongo().get_record(COLLECTION, {"_id": object_id}, fields=fields)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_article_body(resource_id: str, user: str) -> tuple[dict, int]:
    """The article of a resource the caller may read."""
    from archihub.api.users.services import has_role

    resource = _load(resource_id, {"articleBody": 1, "accessRights": 1, "parents": 1, "post_type": 1, "status": 1})
    if not resource:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    is_admin = has_role(user, "admin")
    if not access.may_view_resource(user, resource, is_admin):
        logger.info("Denied %s the article of resource %s", user, resource_id)
        return {"msg": _(MSG_UNAUTHORIZED)}, LEGACY_ROLE_FAILURE_STATUS

    return {"articleBody": serialise(resource.get("articleBody"))}, 200


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def update_article_body(resource_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Replace a resource's article.

    ONLY ``articleBody`` IS WRITTEN. The original built its update from the
    whole request body - ``ResourceUpdate(**{**body, ...})`` - and
    ``ResourceUpdate`` declares ``status``, ``accessRights``, ``post_type``,
    ``parent``, ``parents``, ``metadata``, ``ident`` and ``favCount``. Since the
    database layer writes exactly the fields that were set, a caller could
    publish a draft, clear its access restrictions and re-file it in the tree by
    adding those keys to an article save - bypassing every check the real update
    route performs. See BACKEND_FINDINGS S17.
    """
    from archihub.api.users.services import has_role

    resource = _load(
        resource_id, {"post_type": 1, "accessRights": 1, "parents": 1, "createdBy": 1}
    )
    # Checked before anything is read off it. The original took `post_type` from
    # the record several lines above its own existence check, so a stale id
    # produced a 500 where 404 was documented.
    if not resource:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    article_body = body.get("articleBody")
    if article_body is None:
        return {"msg": _("Article body is required")}, 400
    if not isinstance(article_body, list):
        return {"msg": _("Article body has an invalid format")}, 400

    if not may_edit(user, resource, has_role(user, "admin")):
        logger.info("Denied %s an article edit on resource %s", user, resource_id)
        return {"msg": _(MSG_UNAUTHORIZED)}, LEGACY_ROLE_FAILURE_STATUS

    _write(resource_id, serialise(article_body), user)
    _audit(user, {"resource": resource_id, "articleBody": article_body})

    return {"msg": _("Article body updated")}, 200


def add_block_comment(resource_id: str, body: dict, user: str) -> tuple[dict, int]:
    """Attach a reviewer comment to one block of the article.

    The block is addressed either by ``blockId`` or by ``blockIndex``. An id is
    preferred: an index is only meaningful against the version of the article
    the client was looking at, and blocks move.
    """
    from archihub.api.users.services import has_role

    resource = _load(
        resource_id,
        {"post_type": 1, "articleBody": 1, "accessRights": 1, "parents": 1, "createdBy": 1},
    )
    if not resource:
        return {"msg": _(MSG_NOT_FOUND)}, 404

    if not may_edit(user, resource, has_role(user, "admin")):
        logger.info("Denied %s a block comment on resource %s", user, resource_id)
        return {"msg": _(MSG_UNAUTHORIZED)}, LEGACY_ROLE_FAILURE_STATUS

    article_body = resource.get("articleBody") or []
    if not isinstance(article_body, list):
        return {"msg": _("Article body has an invalid format")}, 400

    comment_text = body.get("comment")
    if not isinstance(comment_text, str) or not comment_text.strip():
        return {"msg": _("Comment is required")}, 400

    index, error = _resolve_block(article_body, body)
    if error is not None:
        return error

    block = article_body[index]
    comments = block.get("comments")
    if comments is None:
        comments = []
    elif not isinstance(comments, list):
        return {"msg": _("Block comments have an invalid format")}, 400

    created_at = _now()
    comment = {
        "comment": comment_text.strip(),
        "user": user,
        "createdAt": created_at.isoformat(),
    }
    comments.append(comment)
    block["comments"] = comments

    _write(resource_id, serialise(article_body), user, updated_at=created_at)
    _audit(
        user,
        {
            "resource": resource_id,
            "blockIndex": index,
            "blockId": block.get("id"),
            "comment": comment["comment"],
        },
    )

    return {
        "msg": _("Block comment added"),
        "blockIndex": index,
        "blockId": block.get("id"),
        "comment": comment,
    }, 200


def _resolve_block(article_body: list, body: dict) -> tuple[int, tuple[dict, int] | None]:
    """Locate the addressed block, or return the response that explains why not."""
    block_id = body.get("blockId")
    block_index = body.get("blockIndex")

    if block_index is None and not block_id:
        return -1, ({"msg": _("You must specify a block")}, 400)

    if block_id:
        index = next(
            (
                i
                for i, block in enumerate(article_body)
                if isinstance(block, dict) and block.get("id") == block_id
            ),
            None,
        )
        if index is None:
            return -1, ({"msg": _("Block does not exist")}, 404)
        return index, None

    # `True` is an int in Python, and indexing by it would silently address
    # block 1.
    if isinstance(block_index, bool) or not isinstance(block_index, numbers.Integral):
        return -1, ({"msg": _("Block index must be an integer")}, 400)

    index = int(block_index)
    if not 0 <= index < len(article_body):
        return -1, ({"msg": _("Block does not exist")}, 404)

    if not isinstance(article_body[index], dict):
        return -1, ({"msg": _("Block has an invalid format")}, 400)

    return index, None


def _write(resource_id: str, article_body, user: str, updated_at=None) -> None:
    """The only write in this module, and it touches three fields."""
    _mongo().update_record(
        COLLECTION,
        {"_id": _to_object_id(resource_id)},
        {
            "articleBody": article_body,
            "updatedAt": updated_at or _now(),
            "updatedBy": user,
        },
    )


def _audit(user: str, details: dict) -> None:
    from archihub.api.logs.services import register_log

    register_log(user, "resource_article_update", details)
