"""The record assistant: a question about a record, answered by a model.

This is the layer between `chat.py` (which takes an assembled message list and
talks to a provider) and the user (who has a video open and types "give me a
summary of the interview"). It decides what the model is shown, applies the
access rule, and records the turn.

**It exists because `POST /aiservices/conversation` means "ask" and not "save".**
In the legacy backend that path is `set_conversation`, dispatching on
``body['type']`` to one of four builders. The rewrite gave the same path to
conversation-record CRUD and put raw chat on a new one
(`/providers/{id}/chat`), so every AI entry point in the product answered 404
"Conversation not found" — the frontend's ``id`` is the *record* being discussed,
and it was being looked up as a conversation id (F64).

WHAT EACH TYPE SHOWS THE MODEL
------------------------------

``transcription``  the flat transcript text of a processing, as a user turn
                   followed by a short assistant acknowledgement. The
                   acknowledgement is not decoration: several providers reject
                   two consecutive turns with the same role, and resuming a
                   conversation appends more user turns after this one.

``document``       the OCR blocks of ONE page, ordered top to bottom and
                   overlap-filtered, combined with the question into a single
                   user turn for the same reason.

``image_gallery`` and ``atlas`` are not implemented and say so with a 501 —
distinguishable from "this route does not exist", which is what the caller got
before.

ACCESS IS THE RECORD'S, NOT THE CONVERSATION'S
----------------------------------------------

Everything here starts at ``records.services.load_visible``, so a caller is shown
a transcript exactly when they could open the record it belongs to. The legacy
builders called ``get_by_id`` and raised a bare Spanish exception on anything but
200, which the route reported as a 500 — a permission failure indistinguishable
from a broken backend.

Resuming a conversation additionally requires owning it (``conversations.load_own``).
Both checks are needed and neither implies the other: a conversation is personal,
and the record it quotes may have been reserved since it was had.

THE STREAM'S SHAPE IS THE FRONTEND'S CONTRACT, and it is not the one
`streaming.event_stream` emits. `AIservice.tsx` branches on a ``type`` field:
``{"type": "response", "delta": ...}`` while text arrives, then
``{"type": "done", "done": true, "conversation_id": ...}``. A payload carrying
neither ``type`` nor ``response`` falls through its parser to the *done* branch —
so emitting the OpenAI-shaped ``{"delta": ...}`` frames used by
`/providers/{id}/chat` would end the answer at its first token. Hence a separate
renderer here rather than reuse.

**The turn is persisted before ``done`` is sent**, so the ``conversation_id`` the
client stores always names a conversation that exists. A failure mid-stream is
delivered as an ``error`` event, because by then the status line is long gone.
"""

from __future__ import annotations

import datetime
import json
import logging

from archihub.api.aiservices import chat, conversations, prompts, providers
from archihub.api.aiservices import errors as ai_errors
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

#: Types this backend can answer, and the ones it deliberately cannot yet.
IMPLEMENTED_TYPES = ("transcription", "document")
KNOWN_TYPES = ("transcription", "document", "image_gallery", "atlas")

#: Legacy stores these snake_case. They are not new fields - 36 conversations on
#: this instance already carry them - so the port must write the same names or
#: history sorts on a key none of the existing rows have.
CREATED_AT = "created_at"
UPDATED_AT = "updated_at"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _now() -> datetime.datetime:
    return datetime.datetime.now()


class AssistantError(Exception):
    """A refusal with the status the caller should see."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Reading the request
# ---------------------------------------------------------------------------


def wants_stream(body: dict) -> bool:
    """Whether the caller asked for server-sent events.

    The legacy helper also accepted ``strem`` and ``sstream`` in two places
    each. Those are typos that were tolerated rather than fixed; the frontend
    sends ``stream`` at the top level and has always done so, so only the
    correct spelling and the ``opts`` nesting are honoured here.
    """
    opts = body.get("opts")
    if isinstance(opts, dict) and "stream" in opts:
        return bool(opts["stream"])
    return bool(body.get("stream"))


def _page(body: dict) -> int:
    opts = body.get("opts")
    raw = opts.get("page", 1) if isinstance(opts, dict) else 1
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


# ---------------------------------------------------------------------------
# Ordering OCR blocks
# ---------------------------------------------------------------------------

#: A block overlapping another by more than this share of the average block area
#: is treated as a duplicate detection and the smaller one is dropped.
OVERLAP_SHARE = 0.35


def order_and_filter_blocks(blocks: list) -> list:
    """Blocks top to bottom, with overlapping duplicates removed.

    Layout detection emits overlapping regions for the same text - a paragraph
    inside a detected column inside a detected block - and feeding all of them to
    a model repeats the passage several times, which it then treats as emphasis.
    """
    usable = [b for b in blocks or [] if isinstance(b, dict) and isinstance(b.get("bbox"), dict)]
    ordered = sorted(usable, key=lambda block: block["bbox"].get("y") or 0)

    def area(bbox: dict) -> float:
        return (bbox.get("width") or 0) * (bbox.get("height") or 0)

    areas = [area(block["bbox"]) for block in ordered]
    average = sum(areas) / len(areas) if areas else 0

    dropped: set[int] = set()
    for i in range(len(ordered)):
        if i in dropped:
            continue
        for j in range(i + 1, len(ordered)):
            if j in dropped:
                continue
            a, b = ordered[i]["bbox"], ordered[j]["bbox"]
            x = max(0, min((a.get("x") or 0) + (a.get("width") or 0),
                           (b.get("x") or 0) + (b.get("width") or 0))
                    - max(a.get("x") or 0, b.get("x") or 0))
            y = max(0, min((a.get("y") or 0) + (a.get("height") or 0),
                           (b.get("y") or 0) + (b.get("height") or 0))
                    - max(a.get("y") or 0, b.get("y") or 0))
            if x * y > OVERLAP_SHARE * average:
                if areas[i] < areas[j]:
                    dropped.add(i)
                    break
                dropped.add(j)

    return [block for index, block in enumerate(ordered) if index not in dropped]


def blocks_to_text(blocks: list) -> str:
    """Ordered blocks as plain text, titles marked so structure survives."""
    lines = []
    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"# {text}" if block.get("type") == "Title" else text)
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Building the context for one record
# ---------------------------------------------------------------------------


def _transcription_context(record: dict, slug: str, message: str) -> list[dict]:
    from archihub.api.records import transcription

    result = transcription.result_of(record, slug)
    text = str(result.get("text") or "").strip()
    if not text:
        raise AssistantError(_("Record has not been processed"), 404)

    return [
        {"role": "system", "content": prompts.TRANSCRIPTION},
        {"role": "user", "content": "Transcription:\n\n" + text},
        {"role": "assistant", "content": "I have read the transcription. How can I help?"},
        {"role": "user", "content": message},
    ]


def _document_context(record: dict, slug: str, message: str, page: int) -> list[dict]:
    from archihub.api.records import blocks as record_blocks

    _path, raw = record_blocks._page_blocks(record, slug, page)
    text = blocks_to_text(order_and_filter_blocks(raw))
    if not text:
        raise AssistantError(_("Record does not have blocks"), 404)

    context = f"---\nPAGE: {page}\n---\n{text}"
    return [
        {"role": "system", "content": prompts.DOCUMENT},
        # One turn, not two: providers that reject consecutive same-role
        # messages would refuse the pair, and resuming appends more after it.
        {"role": "user", "content": f"Document content:\n\n{context}\n\n---\n\n{message}"},
    ]


def build_messages(body: dict, user: str) -> tuple[list[dict], dict]:
    """``(messages, conversation)`` for this request.

    ``conversation`` is the stored document being resumed, or ``{}``.
    """
    from archihub.api.records import blocks as record_blocks
    from archihub.api.records import services as record_services
    from archihub.api.records import transcription

    kind = body.get("type")
    if kind not in KNOWN_TYPES:
        raise AssistantError(_('Unknown conversation type "{type}"', type=str(kind)[:30]), 400)
    if kind not in IMPLEMENTED_TYPES:
        raise AssistantError(
            _("The {type} assistant is not available on this backend yet", type=str(kind)), 501
        )

    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise AssistantError(_("You must specify a message"), 400)

    record_id = body.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise AssistantError(_("You must specify a record"), 400)

    slug = body.get("slug") or body.get("processing_slug")
    if not isinstance(slug, str) or not slug:
        raise AssistantError(_("You must specify a processing"), 400)

    record, error = record_services.load_visible(record_id, user)
    if error is not None:
        payload, status = error
        raise AssistantError(payload.get("msg") or _("Record not found"), status)

    # Resume before building, so a conversation the caller does not own is
    # refused without reading the record's processing at all.
    conversation: dict = {}
    conversation_id = body.get("conversation_id")
    if conversation_id:
        loaded, conv_error = conversations.load_own(conversation_id, user)
        if conv_error is not None:
            payload, status = conv_error
            raise AssistantError(payload.get("msg") or _("Conversation not found"), status)
        conversation = loaded

    try:
        if kind == "transcription":
            messages = _transcription_context(record, slug, message.strip())
        else:
            messages = _document_context(record, slug, message.strip(), _page(body))
    except (transcription.TranscriptionError, record_blocks.BlockError) as exc:
        # These already carry the right status; re-raising them as 500 is how
        # the legacy builders reported "this record has no transcript".
        raise AssistantError(str(exc), getattr(exc, "status_code", 404)) from exc

    if conversation:
        prior = [
            {"role": entry.get("role"), "content": entry.get("content")}
            for entry in conversation.get("messages") or []
            if isinstance(entry, dict) and entry.get("role")
        ]
        # After the context and before the new question, so the model reads the
        # source material first and the question last.
        messages = messages[:-1] + prior + messages[-1:]

    return messages, conversation


# ---------------------------------------------------------------------------
# Recording the turn
# ---------------------------------------------------------------------------


def store_turn(body: dict, user: str, conversation: dict, question: str, answer: str) -> str:
    """Append the exchange, creating the conversation if there is not one yet.

    Returns the conversation id. Failing to store must not lose the answer the
    user is already reading, so a write failure is logged and the id comes back
    empty rather than raising into the stream.
    """
    turns = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]

    try:
        if conversation:
            existing = list(conversation.get("messages") or [])
            _mongo().update_record(
                conversations.COLLECTION,
                {"_id": conversation["_id"]},
                {"messages": existing + turns, UPDATED_AT: _now()},
            )
            return str(conversation["_id"])

        now = _now()
        document = {
            "user": user,
            "messages": turns,
            "type": body.get("type"),
            "processing_slug": body.get("slug") or body.get("processing_slug"),
            "record_id": body.get("id"),
            "applied_skills": body.get("applied_skills") or [],
            CREATED_AT: now,
            UPDATED_AT: now,
        }
        inserted = _mongo().insert_record(conversations.COLLECTION, document)
        return str(inserted.inserted_id)
    except Exception:
        logger.exception("Could not store an assistant turn for %s", user)
        return ""


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------


def _provider_and_model(body: dict) -> tuple[dict, str]:
    provider_ref = body.get("provider")
    provider_id = provider_ref.get("id") if isinstance(provider_ref, dict) else provider_ref
    if not provider_id:
        raise AssistantError(_("You must specify a provider"), 400)

    provider = providers.load(str(provider_id))
    if provider is None:
        raise AssistantError(_("Provider not found"), 404)

    model_ref = body.get("model")
    model = model_ref.get("id") if isinstance(model_ref, dict) else model_ref
    if not model:
        raise AssistantError(_("You must specify a model"), 400)

    return provider, str(model)


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def answer(body: dict, user: str) -> tuple[dict, int]:
    """One complete answer, stored, for a caller that did not ask to stream."""
    messages, conversation = build_messages(body, user)
    provider, model = _provider_and_model(body)
    question = body["message"].strip()

    result = chat.complete(provider, messages, model=model)
    text = _answer_text(result)
    conversation_id = store_turn(body, user, conversation, question, text)

    return {"response": text, "conversation_id": conversation_id}, 200


def _answer_text(result: dict) -> str:
    choices = result.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


def stream(body: dict, user: str):
    """The answer as SSE, in the shape `AIservice.tsx` parses.

    Everything that can fail with a status has already failed by the time this
    generator is iterated - `build_messages` and `_provider_and_model` run in
    `respond()` while the response line can still be chosen.
    """
    messages, conversation = build_messages(body, user)
    provider, model = _provider_and_model(body)
    question = body["message"].strip()

    def generate():
        parts: list[str] = []
        try:
            for chunk in chat.stream(provider, messages, model=model):
                delta = getattr(chunk, "delta", "")
                if delta:
                    parts.append(delta)
                    yield _frame({"type": "response", "delta": delta})
        except ai_errors.ProviderError as exc:
            logger.info("Assistant stream failed for %s: %s", user, exc)
            yield _frame({"type": "error", "error": str(exc), "done": True})
            return
        except Exception:
            logger.exception("Unexpected failure in the assistant stream for %s", user)
            yield _frame(
                {"type": "error", "error": _("An unexpected error occurred"), "done": True}
            )
            return

        # Stored BEFORE `done`, so the id the client keeps always resolves.
        conversation_id = store_turn(body, user, conversation, question, "".join(parts))
        yield _frame({"type": "done", "done": True, "conversation_id": conversation_id})

    return generate()
