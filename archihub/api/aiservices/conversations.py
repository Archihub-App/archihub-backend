"""Stored conversations.

Ordinary CRUD, with one rule that is not ordinary and is the reason this is its
own module: **a conversation belongs to the person who had it.** Every read and
every write goes through ``load_own``. The legacy service checked ownership in
some paths and not others — ``get_conversation`` compared ``user`` while
``delete_conversation`` filtered by id alone — so a known id was enough to
delete somebody else's chat, including whatever archive material it quoted.
"""

from __future__ import annotations

import datetime
import logging

from bson.objectid import ObjectId

from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "conversations"
PAGE_SIZE = 20

#: Kinds of conversation the interface distinguishes.
CONVERSATION_TYPES = ("chat", "processing", "transcription", "document")

#: Roles a stored message may carry.
MESSAGE_ROLES = ("system", "user", "assistant", "tool")

MSG_NOT_FOUND = "Conversation not found"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def parse_result(result):
    import json

    from bson import json_util

    return json.loads(json_util.dumps(result))


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def load_own(conversation_id: str, user: str) -> tuple[dict | None, tuple[dict, int] | None]:
    """``(conversation, error)``. Every route starts here."""
    object_id = _object_id(conversation_id)
    if object_id is None:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    conversation = _mongo().get_record(COLLECTION, {"_id": object_id})
    if not conversation:
        return None, ({"msg": _(MSG_NOT_FOUND)}, 404)

    if conversation.get("user") != user:
        logger.info("Denied %s access to conversation %s", user, conversation_id)
        return None, (
            {"msg": _("You don't have the required authorization")},
            LEGACY_ROLE_FAILURE_STATUS,
        )

    return conversation, None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_messages(messages) -> str | None:
    """Messages must be a list of role/content pairs.

    Checked because they are replayed to a provider later: a malformed entry
    surfaces as an opaque provider error at chat time rather than at the moment
    it was stored.
    """
    if not isinstance(messages, list):
        return _("messages must be a list")

    for message in messages:
        if not isinstance(message, dict):
            return _("messages must be a list")
        if message.get("role") not in MESSAGE_ROLES:
            return _('Unknown message role "{role}"', role=str(message.get("role"))[:30])
        content = message.get("content")
        if not isinstance(content, (str, list)):
            return _("Message content must be text or a list of parts")
    return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


# `save()` deliberately does not exist. `POST /aiservices/conversation` is the
# ASK endpoint (see `assistant.py`); an earlier revision of this port gave that
# path to a create-or-append handler, which is what made every chat turn answer
# 404. Turns are written by `assistant.store_turn`, which is the only writer, so
# there is one place that decides what a stored conversation looks like.


def get(conversation_id: str, user: str) -> tuple[dict, int]:
    conversation, error = load_own(conversation_id, user)
    if error is not None:
        return error

    conversation["id"] = str(conversation.pop("_id"))
    return parse_result(conversation), 200


def delete(conversation_id: str, user: str) -> tuple[dict, int]:
    """Delete one of your own conversations.

    The original filtered on the id alone, so a known id deleted anyone's.
    """
    conversation, error = load_own(conversation_id, user)
    if error is not None:
        return error

    _mongo().delete_record(COLLECTION, {"_id": conversation["_id"]})
    return {"msg": _("Conversation deleted")}, 200


def history(body: dict, user: str) -> tuple[dict, int]:
    """The caller's conversations, newest first.

    Message bodies are projected out: a history list shows titles and dates, and
    a hundred conversations' worth of transcripts is a large response to build
    for a sidebar.
    """
    filters: dict = {"user": user}

    kind = body.get("type")
    if kind:
        if kind not in CONVERSATION_TYPES:
            return {"msg": _('Unknown conversation type "{type}"', type=str(kind)[:30])}, 400
        filters["type"] = kind

    for field in ("record_id", "resource_id", "processing_slug"):
        value = body.get(field)
        if value is not None:
            if not isinstance(value, str):
                return {"msg": _('"{field}" must be a text value', field=field)}, 400
            filters[field] = value

    page = body.get("page") or 0
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        page = 0

    mongo = _mongo()
    rows = list(
        mongo.get_all_records(
            COLLECTION,
            filters,
            fields={"messages": 0},
            # STORED SNAKE_CASE. Legacy wrote `created_at`/`updated_at` and the
            # conversations already in the database carry those names, so
            # sorting on a camelCase key silently ordered by nothing at all.
            sort=[("updated_at", -1)],
            limit=PAGE_SIZE,
            skip=page * PAGE_SIZE,
        )
    )
    for row in rows:
        row["id"] = str(row.pop("_id"))

    return parse_result({"results": rows, "total": mongo.count(COLLECTION, filters)}), 200
