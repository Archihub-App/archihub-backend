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

from archihub.api.aiservices import (
    chat,
    conversations,
    prompts,
    providers,
    skill_context,
    thinking,
)
from archihub.api.aiservices import errors as ai_errors
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

#: Types this backend can answer, and the ones it deliberately cannot yet.
IMPLEMENTED_TYPES = ("transcription", "document", "image_gallery")
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


# ---------------------------------------------------------------------------
# A document page as an image
# ---------------------------------------------------------------------------

#: A page image is base64'd into the request body, which inflates it by a third,
#: and then counted against the model's context. A scan larger than this is
#: refused rather than sent - the failure would otherwise arrive from the
#: provider as an opaque size error, long after the user pressed send.
MAX_IMAGE_BYTES = 12 * 1024 * 1024

#: Which derivative directory a page image is read from. Fixed, because it
#: becomes a path segment; `size` is not taken from the request.
PAGE_IMAGE_SIZE = "big"


def page_image_part(record: dict, page: int) -> dict:
    """One rendered page of a document, as an ``image_url`` content part.

    Legacy passed a private ``{"type": "image_path", "path": ...}`` part and let
    each provider class open the file itself. The dialects here already convert
    the standard ``image_url`` part - all four of them - so the file is read
    once, at the edge, and every provider gets the same thing.

    Every path component is either a fixed constant or comes out of the
    database through ``resolve_within``; ``page`` selects from a sorted listing
    by index and is range-checked, so it never becomes a path segment.
    """
    import base64
    import mimetypes

    from archihub.core import files as filestore
    from archihub.core.settings import get_settings

    processing = (record.get("processing") or {}).get("fileProcessing")
    if not isinstance(processing, dict):
        raise AssistantError(_("Record has not been processed"), 404)
    if processing.get("type") != "document":
        raise AssistantError(_("Record has not been processed"), 404)

    stored = processing.get("path")
    if not stored:
        raise AssistantError(_("Record has not been processed"), 404)

    directory = filestore.resolve_within(
        get_settings().web_files_path, stored, "web", PAGE_IMAGE_SIZE
    )
    if not directory.is_dir():
        raise AssistantError(_("Record has not been processed"), 404)

    pages = sorted(entry for entry in directory.iterdir() if entry.is_file())
    if page < 1 or page > len(pages):
        raise AssistantError(_("Record does not have that many pages"), 404)

    image = pages[page - 1]
    size = image.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise AssistantError(_("The page image is too large to send"), 413)

    media_type = mimetypes.guess_type(image.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}}


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


#: What the viewer's "Input mode" radio can be set to.
DOCUMENT_INPUTS = ("document_ocr", "image")


def _document_input(body: dict) -> str:
    """Which of the two the caller asked for.

    An unrecognised value falls back to the OCR text rather than being refused,
    matching the legacy route: the setting is a display preference and losing
    the answer over it would be worse than answering from the text.
    """
    requested = body.get("opt")
    return requested if requested in DOCUMENT_INPUTS else "document_ocr"


def _document_context(
    record: dict, slug: str, message: str, page: int, mode: str = "document_ocr"
) -> list[dict]:
    """The page as OCR text, or as the rendered image the user chose instead.

    THE MODE IS NOT DECORATION. The viewer has an "Input mode" radio, and an
    earlier revision of this port ignored it entirely - so choosing *Image* sent
    the OCR text anyway. The model answered confidently from the wrong source,
    which is the failure a status code cannot show.
    """
    from archihub.api.records import blocks as record_blocks

    if mode == "image":
        return [
            {"role": "system", "content": prompts.DOCUMENT},
            {
                "role": "user",
                "content": [
                    page_image_part(record, page),
                    {"type": "text", "text": f"Document page: {page}\n\n{message}"},
                ],
            },
        ]

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


# ---------------------------------------------------------------------------
# A gallery image
# ---------------------------------------------------------------------------

#: The derivative a gallery conversation reads. Fixed, like PAGE_IMAGE_SIZE.
GALLERY_IMAGE_SIZE = "large"


def gallery_image(resource_id: str, index: int, user: str) -> tuple[str, dict]:
    """``(stored path, image_url part)`` for the nth image of a gallery.

    Position follows the curator's ``order``, which the legacy path got wrong:
    its order map was keyed by string and looked up by ``ObjectId``, so nothing
    ever matched and every gallery fell back to Mongo's natural order. An index
    past the end is a 404 here; there it raised ``IndexError``, reported as 500.
    """
    from archihub.api.records import viewers
    from archihub.api.resources.services import load_visible as load_resource

    # THE RESOURCE IS THE SUBJECT, so the resource's access rule is the one that
    # applies - the same load the gallery viewer route uses, with the same
    # projection.
    resource, error = load_resource(resource_id, user, fields={"filesObj": 1})
    if error is not None:
        payload, status = error
        raise AssistantError(payload.get("msg") or _("Resource does not exist"), status)

    # `gallery_records` reads the RAW records, which is what this needs. Going
    # through `records.get_by_gallery_index` would return the *presented*
    # record instead, and presentation deliberately strips `filepath` and
    # summarises `processing` - so the path simply would not be there.
    records = viewers.gallery_records(resource)
    if index < 0 or index >= len(records):
        raise AssistantError(_("Record does not exist"), 404)

    try:
        entry = viewers.file_processing_of(records[index])
    except viewers.ViewerError as exc:
        raise AssistantError(str(exc), getattr(exc, "status_code", 404)) from exc

    suffix = viewers.GALLERY_SUFFIXES[GALLERY_IMAGE_SIZE]
    stored = entry["path"] + suffix
    return stored, _image_part(stored)


def _image_part(stored_path: str) -> dict:
    """A stored web-derivative path as an ``image_url`` content part."""
    import base64
    import mimetypes

    from archihub.core import files as filestore
    from archihub.core.settings import get_settings

    path = filestore.resolve_within(get_settings().web_files_path, stored_path)
    if not path.is_file():
        raise AssistantError(_("Record has not been processed"), 404)

    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise AssistantError(_("The page image is too large to send"), 413)

    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}}


def _gallery_context(resource_id: str, message: str, index: int, user: str) -> tuple[list[dict], str]:
    """``(messages, the stored path of the image sent)``."""
    stored, part = gallery_image(resource_id, index, user)
    return (
        [
            {"role": "system", "content": prompts.IMAGE_GALLERY},
            {"role": "user", "content": [part, {"type": "text", "text": message}]},
        ],
        stored,
    )


#: How a stored turn refers to an image. Legacy's shape, and the one
#: `AImessaging.tsx` renders - a PATH, never the bytes. Storing the data URL
#: instead would put megabytes of base64 into the conversation document and walk
#: it straight into MongoDB's 16 MB limit after a handful of turns.
IMAGE_PART = "image_path"


def _replay(messages: list[dict], conversation: dict) -> list[dict]:
    """Prior turns, with stored image references resolved back to images.

    ONLY THE MOST RECENT stored image is re-sent as an image; earlier ones
    become a line naming the file. Legacy replayed every one, so a twenty-turn
    gallery conversation sent twenty images on every request - cost and context
    growing without bound. Collapsing them is also what the system prompt
    already tells the model to expect: the newest image is the primary context
    and earlier ones are referred to by file name.
    """
    prior = []
    stored_images = [
        part
        for entry in conversation.get("messages") or []
        if isinstance(entry, dict) and isinstance(entry.get("content"), list)
        for part in entry["content"]
        if isinstance(part, dict) and part.get("type") == IMAGE_PART
    ]
    newest = stored_images[-1] if stored_images else None

    for entry in conversation.get("messages") or []:
        if not isinstance(entry, dict) or not entry.get("role"):
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            prior.append({"role": entry["role"], "content": content})
            continue

        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != IMAGE_PART:
                parts.append(part)
                continue
            name = str(part.get("path") or "").rsplit("/", 1)[-1]
            if part is newest:
                try:
                    parts.append(_image_part(part["path"]))
                    continue
                except AssistantError:
                    # The derivative is gone. Naming it is still better than
                    # dropping the turn, which would leave the model answering
                    # about an image it was never shown and never told about.
                    logger.info("Gallery image %s is no longer readable", part.get("path"))
            parts.append({"type": "text", "text": f"[Previous image: {name}]"})

        prior.append({"role": entry["role"], "content": parts})

    return prior


def build_messages(body: dict, user: str) -> tuple[list[dict], dict, dict]:
    """``(messages, conversation, applied)`` for this request.

    ``conversation`` is the stored document being resumed, or ``{}``.
    ``applied`` records what shaped the request and has to be stored with the
    turn - the skills that were resolved, and the image a gallery turn refers
    to (by path, never by its bytes).
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

    target_id = body.get("id")
    if not isinstance(target_id, str) or not target_id:
        raise AssistantError(_("You must specify a record"), 400)

    # WHICH REQUESTS NEED A PROCESSING SLUG, AND WHY THE ANSWER IS NOT "all".
    #
    # A slug names one of a record's processings, and it is only needed when the
    # context is READ from that processing - a transcript, or a page's OCR
    # blocks. A document page sent as an IMAGE comes from `fileProcessing`,
    # which every processed document has, and a gallery conversation is about a
    # resource and a position in it.
    #
    # The distinction is not academic: the assistant opens on a plain PDF with
    # no processing view selected, so `view` is undefined in the frontend and no
    # slug is sent. Demanding one refused the request outright - and the legacy
    # route was worse, subscripting `body['slug']` and 500ing on the KeyError.
    slug = body.get("slug") or body.get("processing_slug")
    needs_slug = kind == "transcription" or (
        kind == "document" and _document_input(body) == "document_ocr"
    )
    if needs_slug and (not isinstance(slug, str) or not slug):
        raise AssistantError(_("You must specify a processing"), 400)

    record = None
    if kind != "image_gallery":
        record, error = record_services.load_visible(target_id, user)
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

    applied: dict = {}
    question = message.strip()

    try:
        if kind == "transcription":
            messages = _transcription_context(record, slug, question)
        elif kind == "document":
            messages = _document_context(
                record, slug, question, _page(body), _document_input(body)
            )
        else:
            index = _page(body) - 1 if _gallery_index(body) is None else _gallery_index(body)
            messages, stored = _gallery_context(target_id, question, index, user)
            applied["image_path"] = stored
            applied["page"] = index
    except (transcription.TranscriptionError, record_blocks.BlockError) as exc:
        # These already carry the right status; re-raising them as 500 is how
        # the legacy builders reported "this record has no transcript".
        raise AssistantError(str(exc), getattr(exc, "status_code", 404)) from exc

    if conversation:
        # After the context and before the new question, so the model reads the
        # source material first and the question last.
        messages = messages[:-1] + _replay(messages, conversation) + messages[-1:]

    # LAST, so the skill text lands on the turn actually being asked - after
    # the history has been spliced in, not on a turn that later moves.
    messages, resolved = skill_context.apply_to(messages, question, body.get("applied_skills"))
    if resolved:
        applied["skills"] = [
            {"path": s.get("path"), "title": s.get("title"), "command": s.get("command")}
            for s in resolved
        ]

    return messages, conversation, applied


def _gallery_index(body: dict):
    """The gallery position, if the caller stated one.

    ``opts.page`` is a 0-based INDEX here and a 1-based PAGE for documents -
    the legacy routes differ on this and the frontend sends what each expects,
    so the difference is wire contract rather than an inconsistency to tidy.
    """
    opts = body.get("opts")
    if not isinstance(opts, dict) or "page" not in opts:
        return None
    try:
        value = int(opts["page"])
    except (TypeError, ValueError):
        return 0
    return value if value >= 0 else 0


# ---------------------------------------------------------------------------
# Recording the turn
# ---------------------------------------------------------------------------


def store_turn(
    body: dict,
    user: str,
    conversation: dict,
    question: str,
    answer_text: str,
    applied: dict | None = None,
    steps: list[dict] | None = None,
) -> str:
    """Append the exchange, creating the conversation if there is not one yet.

    Returns the conversation id. Failing to store must not lose the answer the
    user is already reading, so a write failure is logged and the id comes back
    empty rather than raising into the stream.

    WHAT GOES ON EACH TURN. The user's turn carries the skills it was asked with
    and, for a gallery, a reference to the image - as a PATH. The assistant's
    carries the thinking steps. `AImessaging.tsx` reads all three back
    (``msg.applied_skills``, ``msg.thinking_steps``, and ``image_path`` parts),
    so a reopened conversation looks like the one that was had. Legacy stored
    the skills only at conversation level and the steps not at all, so both were
    lost on reload.
    """
    applied = applied or {}

    user_turn: dict = {"role": "user", "content": question}
    image_path = applied.get("image_path")
    if image_path:
        # NEVER the bytes. A base64 data URL here is megabytes per turn and
        # reaches MongoDB's 16 MB document limit in a handful of exchanges.
        user_turn["content"] = [
            {"type": IMAGE_PART, "path": image_path},
            {"type": "text", "text": question},
        ]
    if applied.get("skills"):
        user_turn["applied_skills"] = applied["skills"]

    assistant_turn: dict = {"role": "assistant", "content": answer_text}
    if steps:
        assistant_turn["thinking_steps"] = steps

    turns = [user_turn, assistant_turn]

    try:
        if conversation:
            existing = list(conversation.get("messages") or [])
            update = {"messages": existing + turns, UPDATED_AT: _now()}
            if applied.get("page") is not None:
                update["page"] = applied["page"]
            _mongo().update_record(
                conversations.COLLECTION, {"_id": conversation["_id"]}, update
            )
            return str(conversation["_id"])

        now = _now()
        kind = body.get("type")
        document = {
            "user": user,
            "messages": turns,
            "type": kind,
            "processing_slug": body.get("slug") or body.get("processing_slug"),
            "applied_skills": applied.get("skills") or [],
            CREATED_AT: now,
            UPDATED_AT: now,
        }
        # A gallery conversation hangs off the RESOURCE and remembers which
        # image it was about; the history route filters on exactly these.
        if kind == "image_gallery":
            document["resource_id"] = body.get("id")
            document["page"] = applied.get("page", 0)
        else:
            document["record_id"] = body.get("id")

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
    messages, conversation, applied = build_messages(body, user)
    provider, model = _provider_and_model(body)
    question = body["message"].strip()

    result = chat.complete(provider, messages, model=model)
    text = _answer_text(result)
    conversation_id = store_turn(body, user, conversation, question, text, applied)

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
    messages, conversation, applied = build_messages(body, user)
    provider, model = _provider_and_model(body)
    question = body["message"].strip()

    def generate():
        parts: list[str] = []
        steps = thinking.ThinkingSteps()
        try:
            for chunk in chat.stream(provider, messages, model=model):
                # Reasoning first: a step that opens should appear before the
                # text produced under it, which is the order it happened in.
                reasoning = getattr(chunk, "reasoning", "")
                if reasoning:
                    for event in steps.consume(reasoning):
                        yield _frame(event)

                delta = getattr(chunk, "delta", "")
                if delta:
                    parts.append(delta)
                    yield _frame({"type": "response", "delta": delta})

            for event in steps.finalize():
                yield _frame(event)
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

        summary = steps.summary()
        # Stored BEFORE `done`, so the id the client keeps always resolves.
        conversation_id = store_turn(
            body, user, conversation, question, "".join(parts), applied, summary
        )
        yield _frame(
            {
                "type": "done",
                "done": True,
                "conversation_id": conversation_id,
                "thinking_steps": summary,
            }
        )

    return generate()
