"""Editing the OCR block layout of a page.

The block editor lets a cataloguer correct what OCR produced: move a block's
bounding box, retype its text, add a block the recogniser missed, delete one it
invented. Reading blocks lives in ``viewers.py``; this is the write half.

**A global role was the only check the originals made.** All three routes gated
on ``admin`` or ``editor`` and then loaded the record by id and wrote to it -
so any editor could rewrite the OCR of any record in the archive, including one
filed under a series whose access rights they do not hold and which they cannot
open in the interface. The record's own visibility rule is applied here, before
any write, and it is the same rule the read path uses: ``may_edit`` composes
``records.access.may_view_record`` with the role gate rather than restating
either. Recorded as BACKEND_FINDINGS S19.

**Writes are addressed, not wholesale.** Each of the originals read the entire
``processing`` block, mutated one entry in Python and wrote the whole thing
back, so a plugin finishing a *different* processing on the same record in the
window between read and write had its result silently discarded. The updates
below ``$set`` the one page's block list by dotted path.
"""

from __future__ import annotations

import datetime
import logging

from archihub.api.records import access
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

COLLECTION = "records"

#: The only block collection implemented. The originals branched on
#: ``type_block == 'blocks'`` and, for anything else, fell through to write an
#: unchanged ``processing`` block - bumping ``updatedAt`` and firing the reindex
#: hook while reporting "Block updated". Anything else is refused here.
BLOCK_TYPES = ("blocks",)

#: Keys a client may set on a block. ``data`` was previously splatted into the
#: block whole, so a caller could write ``words`` (the word-level geometry the
#: read path strips), or any key at all, into stored plugin output.
BLOCK_FIELDS = ("bbox", "text", "type", "label", "labels", "order", "confidence")


class BlockError(Exception):
    """The request does not address a block that exists."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def may_edit(user: str, record: dict, is_admin: bool) -> bool:
    """Whether this caller may rewrite this record's block layout.

    Administrators may. An editor may, but only for a record they could open in
    the first place - nobody edits what they cannot read.
    """
    from archihub.api.users.services import has_role

    if is_admin:
        return True
    if not access.may_view_record(user, record, is_admin):
        return False
    return has_role(user, "editor")


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


def _load(record_id: str) -> dict:
    from bson.objectid import ObjectId

    try:
        object_id = ObjectId(record_id)
    except Exception:
        raise BlockError(_("Record does not exist"), 404) from None

    record = _mongo().get_record(
        COLLECTION, {"_id": object_id}, fields={"processing": 1, "accessRights": 1, "parent": 1}
    )
    if not record:
        raise BlockError(_("Record does not exist"), 404)
    return record


def _page_blocks(record: dict, slug: str, page) -> tuple[str, list]:
    """``(dotted path to the page's block list, the current list)``.

    ``page`` is 1-indexed on the wire, matching the viewer.
    """
    processing = record.get("processing")
    entry = processing.get(slug) if isinstance(processing, dict) else None
    if not isinstance(entry, dict):
        raise BlockError(_("Record has not been processed with {slug}", slug=slug), 404)

    if (entry.get("result_storage") or {}).get("type") == "chunked":
        # Chunked results live in their own collection, one document per chunk
        # of pages. Editing one in place needs a different write than a dotted
        # $set on the record, and no caller does it today - refused explicitly
        # rather than silently writing to an inline `result` that is not the
        # one being read back.
        raise BlockError(_("This processing result cannot be edited"), 400)

    result = entry.get("result")
    if not isinstance(result, list):
        raise BlockError(_("Record does not have blocks"), 400)

    index = _index(page) - 1
    if index < 0 or index >= len(result):
        raise BlockError(_("Record does not have that many pages"), 404)

    blocks = (result[index] or {}).get("blocks")
    if not isinstance(blocks, list):
        blocks = []

    return f"processing.{slug}.result.{index}.blocks", blocks


def _index(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise BlockError(_("Invalid page number"), 400) from None
    return value


def _block_type(body: dict) -> str:
    kind = body.get("type_block")
    if kind not in BLOCK_TYPES:
        raise BlockError(_("Unsupported block type"), 400)
    return kind


def _permitted(data) -> dict:
    """The subset of a client's block payload that may be stored."""
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in BLOCK_FIELDS}


def _write(record_id: str, path: str, blocks: list, user: str | None) -> None:
    from bson.objectid import ObjectId

    _mongo().update_record(
        COLLECTION,
        {"_id": ObjectId(record_id)},
        {path: blocks, "updatedBy": user or "system", "updatedAt": _now()},
    )
    _call_hook("record_update", {"_id": record_id, "updatedBy": user or "system"})


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def add(user: str, body: dict) -> tuple[dict, int]:
    """Append a block the recogniser missed."""
    try:
        record, slug = _prepare(user, body)
        _block_type(body)
        path, blocks = _page_blocks(record, slug, body.get("page"))
    except BlockError as exc:
        return {"msg": str(exc)}, exc.status_code

    if "bbox" not in body:
        return {"msg": _("bbox is missing")}, 400

    blocks.append({**_permitted(body.get("data")), "bbox": body["bbox"]})
    _write(body["id_doc"], path, blocks, user)
    return {"msg": _("Block updated")}, 200


def update(user: str, body: dict) -> tuple[dict, int]:
    """Correct an existing block's geometry or content."""
    try:
        record, slug = _prepare(user, body)
        _block_type(body)
        path, blocks = _page_blocks(record, slug, body.get("page"))
        index = _block_index(body.get("index"), len(blocks))
    except BlockError as exc:
        return {"msg": str(exc)}, exc.status_code

    if "bbox" not in body:
        return {"msg": _("bbox is missing")}, 400

    blocks[index] = {**blocks[index], **_permitted(body.get("data")), "bbox": body["bbox"]}
    _write(body["id_doc"], path, blocks, user)
    return {"msg": _("Block updated")}, 200


def delete(user: str, body: dict) -> tuple[dict, int]:
    """Remove a block the recogniser invented."""
    try:
        record, slug = _prepare(user, body)
        _block_type(body)
        path, blocks = _page_blocks(record, slug, body.get("page"))
        index = _block_index(body.get("index"), len(blocks))
    except BlockError as exc:
        return {"msg": str(exc)}, exc.status_code

    blocks.pop(index)
    _write(body["id_doc"], path, blocks, user)
    return {"msg": _("Block deleted")}, 200


def _prepare(user: str, body: dict) -> tuple[dict, str]:
    """Load the record and confirm the caller may rewrite it.

    ``is_admin`` is derived here rather than taken from the route. The route
    holds a ``CurrentUser``, which carries the caller's name and token claims -
    not their roles, which live in Mongo and are read through ``has_role``.
    """
    from archihub.api.users.services import has_role

    record_id = body.get("id_doc")
    slug = body.get("slug")
    if not record_id:
        raise BlockError(_("id_doc is missing"), 400)
    if not slug:
        raise BlockError(_("slug is missing"), 400)

    record = _load(record_id)
    if not may_edit(user, record, has_role(user, "admin")):
        from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

        logger.info("Denied %s block editing on record %s", user, record_id)
        raise BlockError(
            _("You don't have the required authorization"), LEGACY_ROLE_FAILURE_STATUS
        )

    return record, slug


def _block_index(value, count: int) -> int:
    """A block index, checked against the real block count.

    Negative indices are refused rather than wrapping to the end of the page,
    which is what ``list.pop`` does - the original accepted ``-1`` and deleted
    the last block on the page.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise BlockError(_("Invalid block index"), 400) from None
    if value < 0 or value >= count:
        raise BlockError(_("Invalid block index"), 400)
    return value


def _call_hook(name: str, payload: dict) -> None:
    from archihub.core.hooks import get_hook_handler

    try:
        get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("%s hook failed", name)
