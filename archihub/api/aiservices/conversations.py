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
    """One of the caller's own conversations, in full.

    ``_id`` IS KEPT, in its extended-JSON form. `AImessaging.tsx` reads
    ``response._id.$oid`` to know which conversation it is now continuing, so
    renaming it to ``id`` loads the messages and then sends the next turn as a
    NEW conversation - the thread silently forks on the first reply.
    """
    conversation, error = load_own(conversation_id, user)
    if error is not None:
        return error

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


#: Conversation kinds that hang off a RECORD, and are filtered by its id.
RECORD_KINDS = ("record", "transcription", "document")

#: Returned for every row. `messages` is trimmed to the first afterwards - it is
#: what labels the row in the sidebar, so it cannot be projected out, and the
#: rest of a transcript has no business in a list response.
HISTORY_FIELDS = {
    "_id": 1,
    "created_at": 1,
    "updated_at": 1,
    "messages": 1,
    "type": 1,
    "processing_slug": 1,
}


def history(body: dict, user: str) -> tuple[list, int]:
    """The caller's conversations about one record, newest first.

    **Returns a bare ARRAY**, and every part of that sentence is contract:

    * `AImessaging.tsx` does ``Array.isArray(response) ? response : []``, so an
      envelope like ``{"results": [...]}`` is silently read as *no results* -
      a 200 with a populated body rendering as "No results".
    * A row keeps ``_id`` in its extended-JSON form, because the component
      opens and deletes a conversation by ``item._id.$oid``. Renaming it to
      ``id`` leaves the list rendering and every click throwing.
    * A row keeps its first message, because the sidebar labels each entry with
      ``item.messages[0].content``. Projecting messages out renders blank rows
      at best.
    * The record is named by ``id`` in the request, not ``record_id``. That is
      what the component sends.

    An earlier revision of this port got all four wrong at once, which is a
    single 200 that shows an empty panel to a user who has conversations.

    Deliberately unpaginated, as the legacy route is: the panel has no paging
    control, so a limit would silently hide older conversations with nothing to
    reach them by. Message bodies are already trimmed to one.
    """
    kind = body.get("type")
    target_id = body.get("id")
    slug = body.get("processing_slug") or body.get("slug")

    filters: dict = {"user": user}

    if kind in RECORD_KINDS:
        if not isinstance(target_id, str) or not target_id:
            return [], 200

        # The conversations are the caller's own, but they quote a record, and
        # the record may have been reserved since the conversation was had.
        from archihub.api.records import services as record_services

        _record, error = record_services.load_visible(target_id, user)
        if error is not None:
            payload, status = error
            return payload, status

        filters["record_id"] = target_id
        if kind != "record":
            filters["type"] = kind
            if slug:
                filters["processing_slug"] = slug

    elif kind == "image_gallery":
        if not isinstance(target_id, str) or not target_id:
            return [], 200
        filters["resource_id"] = target_id
        filters["type"] = "image_gallery"

    elif kind == "atlas":
        filters["type"] = "atlas"
        if slug:
            filters["processing_slug"] = slug

    else:
        # Unknown kind: an empty list, exactly as the legacy route returns.
        # Not a 400 - the panel asks on open, and refusing would replace an
        # empty history with an error dialog.
        return [], 200

    rows = list(
        _mongo().get_all_records(
            COLLECTION,
            filters,
            fields=HISTORY_FIELDS,
            # STORED SNAKE_CASE. Legacy wrote `created_at`/`updated_at` and the
            # conversations already in the database carry those names, so
            # sorting on a camelCase key ordered by nothing at all.
            sort=[("updated_at", -1)],
        )
    )

    for row in rows:
        messages = row.get("messages") or []
        row["messages"] = messages[:1]

    return parse_result(rows), 200
