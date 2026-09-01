"""The record assistant — the layer that answers a question about a record.

`POST /aiservices/conversation` is the ASK endpoint. The rewrite gave that path
to a create-or-append handler for conversation *records*, so every chat turn in
the product answered **404 "Conversation not found"**: the frontend sends `id`
meaning the record being discussed, and it was looked up as a conversation id.
The first test here is that regression, stated as a property rather than a
status.

The stream shape is tested for the same reason it exists. `AIservice.tsx`
branches on a `type` field, and a payload carrying neither `type` nor `response`
falls through its parser to the *done* branch — so emitting the OpenAI-shaped
`{"delta": ...}` frames that `/providers/{id}/chat` uses would end the answer at
its first token, with no error anywhere.
"""

from __future__ import annotations

import json

import pytest

from archihub.api.aiservices import assistant

RECORD_ID = "6a4be338fc7ab7b3737e8220"
OTHER_ID = "6a4be338fc7ab7b3737e8221"
SLUG = "transcribeWhisperX"


class FakeMongo:
    def __init__(self):
        self.inserted: list[tuple[str, dict]] = []
        self.updates: list[tuple[dict, dict]] = []

    def insert_record(self, collection, document):
        self.inserted.append((collection, document))

        class Result:
            inserted_id = OTHER_ID

        return Result()

    def update_record(self, collection, filters, update):
        self.updates.append((filters, update))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake


def _record(text="the interview transcript"):
    return {
        "_id": RECORD_ID,
        # `av_transcribe` is the stored processing type, not "transcription" -
        # that is the CONVERSATION type. They are different vocabularies.
        "processing": {SLUG: {"type": "av_transcribe", "result": {"text": text}}},
    }


@pytest.fixture
def visible(monkeypatch):
    """The record is readable by whoever asks."""
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible",
        lambda record_id, user: (_record(), None),
    )


def _body(**overrides):
    body = {
        "type": "transcription",
        "message": "give me a summary of the interview",
        "id": RECORD_ID,
        "slug": SLUG,
        "provider": {"id": "6a84fde25da14d8f86f41466"},
        "model": {"id": "deepseek/deepseek-v4"},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


def test_the_record_id_is_not_looked_up_as_a_conversation(visible, monkeypatch):
    """`id` names the RECORD. Treating it as a conversation is what broke this.

    Asserted by watching where the id goes: `load_own` must not be consulted at
    all when no `conversation_id` was sent.
    """
    consulted = []
    monkeypatch.setattr(
        "archihub.api.aiservices.conversations.load_own",
        lambda cid, user: consulted.append(cid) or (None, ({"msg": "nope"}, 404)),
    )

    messages, conversation, _applied = assistant.build_messages(_body(), "someone@test.com")

    assert consulted == [], f"the record id was looked up as a conversation: {consulted}"
    assert conversation == {}
    assert messages[-1]["content"] == "give me a summary of the interview"


def test_the_transcript_reaches_the_model(visible):
    messages, _conversation, _applied = assistant.build_messages(_body(), "someone@test.com")

    assert messages[0]["role"] == "system"
    assert "the interview transcript" in messages[1]["content"]
    # A provider that rejects two consecutive user turns must still work, and
    # resuming appends more user turns after this one.
    assert messages[2]["role"] == "assistant"


def test_prior_turns_go_between_the_source_material_and_the_question(monkeypatch, visible):
    monkeypatch.setattr(
        "archihub.api.aiservices.conversations.load_own",
        lambda cid, user: ({"_id": cid, "messages": [
            {"role": "user", "content": "who is speaking?"},
            {"role": "assistant", "content": "two people"},
        ]}, None),
    )

    messages, conversation, _applied = assistant.build_messages(
        _body(conversation_id="abc"), "someone@test.com"
    )

    assert conversation["_id"] == "abc"
    contents = [m["content"] for m in messages]
    assert contents.index("who is speaking?") > contents.index(
        "Transcription:\n\nthe interview transcript"
    )
    assert messages[-1]["content"] == "give me a summary of the interview"


# ---------------------------------------------------------------------------
# Refusals carry the right status
# ---------------------------------------------------------------------------


def test_an_unimplemented_type_says_so_rather_than_404ing(visible):
    """`atlas` is the one that remains, and its refusal names it.

    Not 404: a caller must be able to tell "this backend does not do that" from
    "there is no such route", which is what they got before.
    """
    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_body(type="atlas"), "someone@test.com")

    assert caught.value.status_code == 501
    assert "atlas" in str(caught.value)


def test_an_unknown_type_is_a_400(visible):
    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_body(type="nonsense"), "someone@test.com")

    assert caught.value.status_code == 400


def test_a_record_the_caller_cannot_see_keeps_the_records_own_status(monkeypatch):
    """Not a 500. The legacy builders raised a bare exception for any non-200."""
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible",
        lambda record_id, user: (None, ({"msg": "You don't have the required authorization"}, 403)),
    )

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_body(), "someone@test.com")

    assert caught.value.status_code == 403


def test_someone_elses_conversation_is_refused_before_the_record_is_read(monkeypatch, visible):
    monkeypatch.setattr(
        "archihub.api.aiservices.conversations.load_own",
        lambda cid, user: (None, ({"msg": "You don't have the required authorization"}, 403)),
    )

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_body(conversation_id="abc"), "someone@test.com")

    assert caught.value.status_code == 403


def test_an_unprocessed_record_is_a_404_not_a_500(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible",
        lambda record_id, user: ({"_id": RECORD_ID, "processing": {}}, None),
    )

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_body(), "someone@test.com")

    assert caught.value.status_code == 404


@pytest.mark.parametrize("missing", ["message", "id", "slug"])
def test_a_missing_required_field_is_a_400(visible, missing):
    body = _body()
    body.pop(missing)

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(body, "someone@test.com")

    assert caught.value.status_code == 400


# ---------------------------------------------------------------------------
# The stream's shape is the frontend's contract
# ---------------------------------------------------------------------------


class FakeChunk:
    def __init__(self, delta):
        self.delta = delta


def _frames(monkeypatch, chunks):
    monkeypatch.setattr(
        "archihub.api.aiservices.chat.stream",
        lambda provider, messages, **options: iter(chunks),
    )
    monkeypatch.setattr(
        "archihub.api.aiservices.providers.load", lambda provider_id: {"_id": provider_id}
    )
    return list(assistant.stream(_body(), "someone@test.com"))


def _payloads(frames):
    return [json.loads(f.removeprefix("data: ").strip()) for f in frames]


def test_every_frame_carries_a_type(monkeypatch, visible, mongo):
    """Without `type`, AIservice.tsx treats the payload as *done*."""
    payloads = _payloads(_frames(monkeypatch, [FakeChunk("Hola"), FakeChunk(" mundo")]))

    assert all("type" in payload for payload in payloads), payloads
    assert [p["type"] for p in payloads] == ["response", "response", "done"]
    assert [p["delta"] for p in payloads[:2]] == ["Hola", " mundo"]


def test_frames_are_separated_by_real_blank_lines(monkeypatch, visible, mongo):
    """The legacy framing emitted a literal backslash-n and no client could read it."""
    frames = _frames(monkeypatch, [FakeChunk("hi")])

    assert all(frame.startswith("data: ") and frame.endswith("\n\n") for frame in frames)
    assert "\\n" not in frames[0]


def test_the_turn_is_stored_before_done_is_sent(monkeypatch, visible, mongo):
    """The id the client keeps must always name a conversation that exists."""
    payloads = _payloads(_frames(monkeypatch, [FakeChunk("answer")]))

    assert mongo.inserted, "nothing was stored"
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["conversation_id"] == OTHER_ID


def test_a_failure_mid_stream_arrives_as_an_event(monkeypatch, visible, mongo):
    """The status line is long gone; a dropped socket looks like a finished answer."""
    from archihub.api.aiservices import errors

    def explode():
        yield FakeChunk("partial")
        raise errors.ProviderError(errors.Reason.UNAVAILABLE, "provider is down")

    payloads = _payloads(_frames(monkeypatch, explode()))

    assert payloads[-1]["type"] == "error"
    assert payloads[-1]["done"] is True


def test_a_failed_stream_does_not_store_a_half_answer(monkeypatch, visible, mongo):
    from archihub.api.aiservices import errors

    def explode():
        raise errors.ProviderError(errors.Reason.UNAVAILABLE, "down")
        yield  # pragma: no cover

    _frames(monkeypatch, explode())

    assert mongo.inserted == []


# ---------------------------------------------------------------------------
# Persistence uses the names already in the database
# ---------------------------------------------------------------------------


def test_a_new_conversation_is_stored_with_the_legacy_date_fields(mongo):
    """36 conversations on this instance carry `created_at`/`updated_at`.

    Writing camelCase would make them invisible to a history sorted the other
    way, and vice versa.
    """
    assistant.store_turn(_body(), "someone@test.com", {}, "q", "a")

    _collection, document = mongo.inserted[0]
    assert "created_at" in document and "updated_at" in document
    assert "createdAt" not in document and "updatedAt" not in document
    assert document["user"] == "someone@test.com"
    assert document["record_id"] == RECORD_ID


def test_resuming_appends_rather_than_replacing(mongo):
    conversation = {"_id": "abc", "messages": [{"role": "user", "content": "earlier"}]}

    assistant.store_turn(_body(), "someone@test.com", conversation, "q", "a")

    _filters, update = mongo.updates[0]
    assert [m["content"] for m in update["messages"]] == ["earlier", "q", "a"]
    assert mongo.inserted == []


def test_a_storage_failure_does_not_lose_the_answer(monkeypatch, visible):
    """The user is already reading it; raising here would replace it with an error."""

    class Broken:
        def insert_record(self, *a, **k):
            raise RuntimeError("mongo is down")

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: Broken())

    assert assistant.store_turn(_body(), "someone@test.com", {}, "q", "a") == ""


def test_history_sorts_on_the_field_the_documents_actually_have():
    import inspect

    from archihub.api.aiservices import conversations

    source = inspect.getsource(conversations.history)
    assert '("updated_at", -1)' in source
    assert "updatedAt" not in source


# ---------------------------------------------------------------------------
# OCR block ordering
# ---------------------------------------------------------------------------


def _block(x, y, w, h, text, kind="Text"):
    return {"bbox": {"x": x, "y": y, "width": w, "height": h}, "text": text, "type": kind}


def test_blocks_are_ordered_top_to_bottom():
    ordered = assistant.order_and_filter_blocks(
        [_block(0, 90, 10, 10, "last"), _block(0, 10, 10, 10, "first")]
    )

    assert [b["text"] for b in ordered] == ["first", "last"]


def test_an_overlapping_duplicate_detection_is_dropped():
    """Layout detection nests regions; feeding both repeats the passage."""
    ordered = assistant.order_and_filter_blocks(
        [_block(0, 0, 100, 100, "the paragraph"), _block(5, 5, 90, 90, "the paragraph again")]
    )

    assert len(ordered) == 1
    assert ordered[0]["text"] == "the paragraph"


def test_titles_keep_their_structure_in_the_text():
    text = assistant.blocks_to_text(
        [_block(0, 0, 10, 10, "Chapter One", "Title"), _block(0, 20, 10, 10, "body")]
    )

    assert text == "# Chapter One\n\nbody"


def test_a_page_with_no_bboxes_does_not_explode():
    assert assistant.order_and_filter_blocks([{"text": "no bbox"}]) == []
    assert assistant.order_and_filter_blocks([]) == []


# ---------------------------------------------------------------------------
# Reading the request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, False),
        ({"stream": True}, True),
        ({"opts": {"stream": True}}, True),
        ({"opts": {"stream": False}, "stream": True}, False),
    ],
)
def test_stream_is_read_from_either_place(body, expected):
    assert assistant.wants_stream(body) is expected


@pytest.mark.parametrize(
    ("opts", "expected"),
    [(None, 1), ({"page": 3}, 3), ({"page": "4"}, 4), ({"page": 0}, 1), ({"page": "x"}, 1)],
)
def test_the_page_is_at_least_one(opts, expected):
    assert assistant._page({"opts": opts} if opts is not None else {}) == expected


# ---------------------------------------------------------------------------
# The document viewer's "Input mode" radio
# ---------------------------------------------------------------------------


@pytest.fixture
def document_record(monkeypatch, tmp_path):
    """A processed document whose page images and original PDF exist on disk."""
    # A real stored path, not "": an empty one is refused, and rightly so -
    # it would resolve to the web root and list whatever is there.
    pages = tmp_path / "web" / "2026" / "08" / "doc" / "web" / "big"
    pages.mkdir(parents=True)
    for index in (1, 2, 3):
        (pages / f"{index:04d}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([index]) * 40)

    originals = tmp_path / "original" / "2026" / "08"
    originals.mkdir(parents=True)
    (originals / "doc.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 64)

    record = {
        "_id": RECORD_ID,
        "filepath": "2026/08/doc.pdf",
        "processing": {
            "fileProcessing": {"type": "document", "path": "2026/08/doc"},
            "ocr": {"type": "lt_extraction", "result": [
                {"blocks": [{"bbox": {"x": 0, "y": 0, "width": 9, "height": 9}, "text": "page one"}]},
            ]},
        },
    }
    monkeypatch.setattr(
        "archihub.core.settings.get_settings",
        lambda: type("S", (), {
            "web_files_path": str(tmp_path / "web"),
            "original_files_path": str(tmp_path / "original"),
        })(),
    )
    monkeypatch.setattr(
        "archihub.api.records.services.load_visible", lambda rid, user: (record, None)
    )
    return record


def _doc_body(**overrides):
    body = _body(type="document", slug="ocr")
    body.update(overrides)
    return body


def test_image_mode_sends_the_page_image_not_the_ocr_text(document_record):
    """The radio is real. Ignoring it answers confidently from the wrong source."""
    messages, _conversation, _applied = assistant.build_messages(
        _doc_body(opt="image", opts={"page": 2}), "someone@test.com"
    )

    parts = messages[-1]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "page one" not in json.dumps(messages)


def test_ocr_mode_sends_the_text(document_record):
    messages, _conversation, _applied = assistant.build_messages(
        _doc_body(opt="document_ocr", opts={"page": 1}), "someone@test.com"
    )

    assert isinstance(messages[-1]["content"], str)
    assert "page one" in messages[-1]["content"]


def test_the_default_is_the_ocr_text(document_record):
    messages, _conversation, _applied = assistant.build_messages(_doc_body(), "someone@test.com")

    assert isinstance(messages[-1]["content"], str)


def test_each_page_sends_a_different_image(document_record):
    first = assistant.page_image_part(document_record, 1)
    second = assistant.page_image_part(document_record, 2)

    assert first["image_url"]["url"] != second["image_url"]["url"]


@pytest.mark.parametrize("page", [0, 4, 99])
def test_a_page_outside_the_document_is_refused(document_record, page):
    """`page` is client-supplied; it indexes a listing, never a path segment."""
    with pytest.raises(assistant.AssistantError) as caught:
        assistant.page_image_part(document_record, page)

    assert caught.value.status_code == 404


def test_an_oversized_page_image_is_refused_before_it_is_sent(monkeypatch, document_record):
    monkeypatch.setattr(assistant, "MAX_IMAGE_BYTES", 4)

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.page_image_part(document_record, 1)

    assert caught.value.status_code == 413


def test_a_stored_path_cannot_escape_the_web_root(monkeypatch, document_record):
    document_record["processing"]["fileProcessing"]["path"] = "../../../etc"

    with pytest.raises(Exception) as caught:
        assistant.page_image_part(document_record, 1)

    assert "etc" not in str(caught.value)


def test_a_record_that_is_not_a_document_has_no_page_image(document_record):
    document_record["processing"]["fileProcessing"]["type"] = "image"

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.page_image_part(document_record, 1)

    assert caught.value.status_code == 404


# ---------------------------------------------------------------------------
# The whole-document ("full_pdf") input mode
# ---------------------------------------------------------------------------

ANTHROPIC_PROVIDER = {"dialect": "anthropic"}
OPENAI_PROVIDER = {"dialect": "openai-compatible"}
OLLAMA_PROVIDER = {"dialect": "ollama"}


def test_document_part_sends_the_original_file_whole(document_record):
    part = assistant.document_part(document_record)

    assert part["type"] == "document_url"
    assert part["document_url"]["url"].startswith("data:application/pdf;base64,")
    assert part["document_url"]["name"] == "doc.pdf"


def test_document_part_refuses_a_record_with_no_original(document_record):
    document_record["filepath"] = None

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.document_part(document_record)

    assert caught.value.status_code == 404


def test_an_oversized_document_is_refused_before_it_is_sent(monkeypatch, document_record):
    monkeypatch.setattr(assistant, "MAX_DOCUMENT_BYTES", 4)

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.document_part(document_record)

    assert caught.value.status_code == 413


def test_full_pdf_mode_sends_the_whole_document_for_a_capable_dialect(document_record):
    messages, _conversation, _applied = assistant.build_messages(
        _doc_body(opt="full_pdf", opts={"page": 1}), "someone@test.com", ANTHROPIC_PROVIDER
    )

    parts = messages[-1]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "document_url"
    assert "page one" not in json.dumps(messages)


def test_full_pdf_mode_works_for_openai_compatible_too(document_record):
    """OpenAI itself speaks this dialect and has a native `file` part - the
    dialect fronts other servers too, but excluding OpenAI's own to be safe
    about the rest would refuse the one provider this was built for."""
    messages, _conversation, _applied = assistant.build_messages(
        _doc_body(opt="full_pdf", opts={"page": 1}), "someone@test.com", OPENAI_PROVIDER
    )

    assert messages[-1]["content"][0]["type"] == "document_url"


def test_full_pdf_mode_is_refused_for_a_dialect_with_no_document_part(document_record):
    """A silent fallback to OCR text would answer from a source the caller did
    not choose, with nothing on the response to say so."""
    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(
            _doc_body(opt="full_pdf"), "someone@test.com", OLLAMA_PROVIDER
        )

    assert caught.value.status_code == 422


def test_full_pdf_mode_does_not_need_a_processing_slug(document_record):
    body = _doc_body(opt="full_pdf")
    body.pop("slug")

    messages, _conversation, _applied = assistant.build_messages(
        body, "someone@test.com", ANTHROPIC_PROVIDER
    )

    assert messages[-1]["content"][0]["type"] == "document_url"


# ---------------------------------------------------------------------------
# The image gallery
# ---------------------------------------------------------------------------


@pytest.fixture
def gallery(monkeypatch, tmp_path):
    """A resource whose gallery has three processed images."""
    images = tmp_path / "2026" / "08"
    images.mkdir(parents=True)
    for name in ("a", "b", "c"):
        (images / f"{name}_large.jpg").write_bytes(b"\xff\xd8\xff" + name.encode() * 30)

    records = [
        {"_id": f"rec{n}", "processing": {"fileProcessing": {"path": f"2026/08/{n}"}}}
        for n in ("a", "b", "c")
    ]

    monkeypatch.setattr(
        "archihub.core.settings.get_settings",
        lambda: type("S", (), {"web_files_path": str(tmp_path)})(),
    )
    # The resource's own access rule, and then the RAW records - which is the
    # pair the real path uses, and the reason it does not go through
    # `records.get_by_gallery_index` (presentation strips `filepath`).
    monkeypatch.setattr(
        "archihub.api.resources.services.load_visible",
        lambda resource_id, user, fields=None: ({"_id": resource_id, "filesObj": []}, None),
    )
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda user, role: False)
    monkeypatch.setattr(
        "archihub.api.records.viewers.gallery_records", lambda resource, **kwargs: records
    )
    return records


def _gallery_body(**overrides):
    body = _body(type="image_gallery", id="resource-1")
    body.pop("slug", None)
    body.update(overrides)
    return body


def test_a_gallery_conversation_sends_the_image_at_that_position(gallery):
    messages, _conv, applied = assistant.build_messages(
        _gallery_body(opts={"page": 1}), "someone@test.com"
    )

    parts = messages[-1]["content"]
    assert parts[0]["type"] == "image_url"
    assert applied["image_path"] == "2026/08/b_large.jpg"
    assert applied["page"] == 1


def test_a_gallery_conversation_needs_no_processing_slug(gallery):
    """It is about a resource and a position, not a record's processing."""
    messages, _conv, _applied = assistant.build_messages(_gallery_body(), "someone@test.com")

    assert messages[0]["content"] == assistant.prompts.IMAGE_GALLERY


def test_a_position_past_the_end_is_a_404(gallery):
    """Legacy indexed the list directly, so this raised IndexError as a 500."""
    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_gallery_body(opts={"page": 99}), "someone@test.com")

    assert caught.value.status_code == 404


def test_a_resource_the_caller_cannot_see_keeps_its_status(monkeypatch, gallery):
    """A gallery conversation is about a RESOURCE, so that is the rule applied."""
    monkeypatch.setattr(
        "archihub.api.resources.services.load_visible",
        lambda resource_id, user, fields=None: (None, ({"msg": "nope"}, 403)),
    )

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_gallery_body(), "someone@test.com")

    assert caught.value.status_code == 403


def test_a_gallery_conversation_asks_for_the_callers_own_visible_images(monkeypatch, gallery):
    """The resource rule alone is not enough - each image is its own record.

    ``gallery_image`` must forward the caller's identity and admin status into
    ``gallery_records`` rather than reading every attached image unfiltered.
    """
    captured = {}

    def fake_gallery_records(resource, **kwargs):
        captured.update(kwargs)
        return gallery

    monkeypatch.setattr("archihub.api.records.viewers.gallery_records", fake_gallery_records)
    monkeypatch.setattr(
        "archihub.api.users.services.has_role", lambda user, role: role == "admin"
    )

    assistant.build_messages(_gallery_body(opts={"page": 0}), "curator@test.com")

    assert captured == {"user": "curator@test.com", "is_admin": True}


def test_the_stored_turn_holds_a_path_not_the_bytes(gallery, mongo):
    """A base64 data URL per turn reaches MongoDB's 16 MB limit in a few turns."""
    _messages, _conv, applied = assistant.build_messages(
        _gallery_body(opts={"page": 0}), "someone@test.com"
    )
    assistant.store_turn(_gallery_body(), "someone@test.com", {}, "q", "a", applied)

    _collection, document = mongo.inserted[0]
    part = document["messages"][0]["content"][0]
    assert part == {"type": "image_path", "path": "2026/08/a_large.jpg"}
    assert "base64" not in json.dumps(document, default=str)


def test_a_gallery_conversation_hangs_off_the_resource(gallery, mongo):
    assistant.store_turn(
        _gallery_body(), "someone@test.com", {}, "q", "a", {"image_path": "p", "page": 2}
    )

    _collection, document = mongo.inserted[0]
    assert document["resource_id"] == "resource-1"
    assert document["page"] == 2
    assert "record_id" not in document


def test_only_the_newest_stored_image_is_re_sent(gallery):
    """Legacy replayed every image, so a long conversation resent them all."""
    conversation = {
        "_id": "abc",
        "messages": [
            {"role": "user", "content": [
                {"type": "image_path", "path": "2026/08/a_large.jpg"},
                {"type": "text", "text": "what is this?"},
            ]},
            {"role": "assistant", "content": "a photograph"},
            {"role": "user", "content": [
                {"type": "image_path", "path": "2026/08/b_large.jpg"},
                {"type": "text", "text": "and this?"},
            ]},
            {"role": "assistant", "content": "a map"},
        ],
    }

    replayed = assistant._replay([], conversation)

    images = [p for turn in replayed if isinstance(turn["content"], list)
              for p in turn["content"] if p.get("type") == "image_url"]
    markers = [p for turn in replayed if isinstance(turn["content"], list)
               for p in turn["content"] if p.get("type") == "text"
               and p["text"].startswith("[Previous image:")]

    assert len(images) == 1
    assert len(markers) == 1
    assert "a_large.jpg" in markers[0]["text"]


def test_a_missing_derivative_becomes_a_marker_not_a_dropped_turn(gallery):
    """Better a named absence than a model answering about an unseen image."""
    conversation = {
        "_id": "abc",
        "messages": [
            {"role": "user", "content": [
                {"type": "image_path", "path": "2026/08/gone_large.jpg"},
                {"type": "text", "text": "what is this?"},
            ]},
        ],
    }

    replayed = assistant._replay([], conversation)

    texts = [p["text"] for p in replayed[0]["content"] if p.get("type") == "text"]
    assert any("gone_large.jpg" in t for t in texts)
    assert "what is this?" in texts


def test_an_oversized_gallery_image_is_refused(monkeypatch, gallery):
    monkeypatch.setattr(assistant, "MAX_IMAGE_BYTES", 4)

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(_gallery_body(), "someone@test.com")

    assert caught.value.status_code == 413


# ---------------------------------------------------------------------------
# Thinking steps reach the stream and the stored turn
# ---------------------------------------------------------------------------


class ReasoningChunk:
    def __init__(self, delta="", reasoning=""):
        self.delta = delta
        self.reasoning = reasoning


def test_thinking_steps_are_streamed_and_stored(monkeypatch, visible, mongo):
    payloads = _payloads(
        _frames(
            monkeypatch,
            [
                ReasoningChunk(reasoning="Reading the transcript: looking\n"),
                ReasoningChunk(delta="The interview"),
                ReasoningChunk(delta=" covers three topics."),
            ],
        )
    )

    kinds = [p["type"] for p in payloads]
    assert "thinking_step" in kinds
    # The step opens before the text produced under it.
    assert kinds.index("thinking_step") < kinds.index("response")
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["thinking_steps"][0]["title"] == "Reading the transcript"

    _collection, document = mongo.inserted[0]
    assert document["messages"][1]["thinking_steps"][0]["title"] == "Reading the transcript"


def test_reasoning_is_never_part_of_the_answer(monkeypatch, visible, mongo):
    """It is prose in the same voice; mixed in, the answer starts mid-thought."""
    _frames(monkeypatch, [ReasoningChunk(reasoning="Hmm: let me think\n"),
                          ReasoningChunk(delta="The answer.")])

    _collection, document = mongo.inserted[0]
    assert document["messages"][1]["content"] == "The answer."


# ---------------------------------------------------------------------------
# Skills reach the prompt through the assistant, not only in isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def one_skill(monkeypatch):
    """A single resolvable skill, whatever it is looked up by."""
    skill = {
        "path": "research/summarise.md",
        "command": "research/summarise",
        "name": "summarise",
        "title": "Summarise a source",
        "content": "Produce five bullet points.",
    }
    monkeypatch.setattr("archihub.api.aiservices.skill_context.lookup", lambda ident: skill)
    return skill


def test_a_selected_skill_shapes_the_prompt(visible, one_skill):
    """The unit is tested next door; this asserts the assistant WIRES it."""
    messages, _conv, applied = assistant.build_messages(
        _body(applied_skills=["research/summarise"]), "someone@test.com"
    )

    assert "Produce five bullet points." in messages[-1]["content"]
    assert applied["skills"][0]["path"] == "research/summarise.md"


def test_the_inline_token_the_frontend_prepends_is_consumed(visible, one_skill):
    r"""`AImessaging.tsx` sends `\path` in the text AND in `applied_skills`.

    Ignoring the field leaves the token in the message, where the model sees an
    unexplained `\research/summarise` with no instruction attached.
    """
    messages, _conv, _applied = assistant.build_messages(
        _body(message=r"\research/summarise give me a summary"), "someone@test.com"
    )

    question = messages[-1]["content"].split("User request:", 1)[1]
    assert question.strip() == "give me a summary"


def test_the_skills_are_stored_with_the_turn(visible, one_skill, mongo):
    """`AImessaging.tsx` renders `msg.applied_skills` when reopening a thread."""
    _messages, _conv, applied = assistant.build_messages(
        _body(applied_skills=["research/summarise"]), "someone@test.com"
    )
    assistant.store_turn(_body(), "someone@test.com", {}, "q", "a", applied)

    _collection, document = mongo.inserted[0]
    assert document["messages"][0]["applied_skills"][0]["title"] == "Summarise a source"


def test_no_skills_leaves_the_question_alone(visible):
    messages, _conv, applied = assistant.build_messages(_body(), "someone@test.com")

    assert messages[-1]["content"] == "give me a summary of the interview"
    assert "skills" not in applied


# ---------------------------------------------------------------------------
# Which requests actually need a processing slug
# ---------------------------------------------------------------------------


def test_image_mode_does_not_need_a_processing_slug(document_record):
    """The assistant opens on a plain PDF with no processing view selected.

    `view` is undefined in the frontend then, so no slug is sent - and none is
    needed, because a page image comes from `fileProcessing`. Demanding one
    refused the request outright; the legacy route subscripted `body['slug']`
    and 500'd on the KeyError.
    """
    body = _doc_body(opt="image", opts={"page": 1})
    body.pop("slug")

    messages, _conversation, _applied = assistant.build_messages(body, "someone@test.com")

    assert messages[-1]["content"][0]["type"] == "image_url"


def test_ocr_mode_still_needs_one(document_record):
    """It names the processing the blocks are read FROM; there is no default."""
    body = _doc_body(opt="document_ocr")
    body.pop("slug")

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(body, "someone@test.com")

    assert caught.value.status_code == 400


def test_transcription_still_needs_one(visible):
    body = _body()
    body.pop("slug")

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.build_messages(body, "someone@test.com")

    assert caught.value.status_code == 400


def test_a_gallery_conversation_never_needed_one(gallery):
    body = _gallery_body()
    assert "slug" not in body

    messages, _conversation, _applied = assistant.build_messages(body, "someone@test.com")

    assert messages[0]["content"] == assistant.prompts.IMAGE_GALLERY


# ---------------------------------------------------------------------------
# Conversation types an active plugin provides
# ---------------------------------------------------------------------------


def test_a_plugin_conversation_type_is_501_when_no_plugin_provides_it():
    """Recognised, but not available here - which is what 501 means.

    The type stays in KNOWN_TYPES so the refusal names it, rather than reading
    as a client sending nonsense.
    """
    from archihub.api.aiservices import assistant
    from archihub.plugins.framework import interop

    interop.reset()

    assert assistant.plugin_handler("atlas") is None


def test_a_plugin_conversation_type_resolves_once_the_plugin_provides_it():
    """The dependency follows ACTIVATION, not the presence of a directory: a
    plugin registers its entry point when it is built."""
    from archihub.api.aiservices import assistant
    from archihub.plugins.framework import interop

    interop.reset()
    interop.provide("atlas_conversation", "atlas", lambda body, provider, user: "answered")

    handler = assistant.plugin_handler("atlas")

    assert handler is not None
    assert handler({}, {}, "alice") == "answered"

    interop.reset()


def test_a_record_conversation_type_is_never_treated_as_a_plugin_type():
    """The record assistant must keep answering the types it implements even
    while a plugin is registered - the dispatch is per type, not a mode."""
    from archihub.api.aiservices import assistant
    from archihub.plugins.framework import interop

    interop.reset()
    interop.provide("atlas_conversation", "atlas", lambda *a: "atlas")

    for kind in assistant.IMPLEMENTED_TYPES:
        assert assistant.plugin_handler(kind) is None, f"{kind} was diverted to a plugin"

    interop.reset()


def test_every_plugin_type_is_a_known_type():
    """A type that dispatches to a plugin but is not in KNOWN_TYPES would be
    refused as unknown before the dispatch could ever run."""
    from archihub.api.aiservices import assistant

    for kind in assistant.PLUGIN_TYPES:
        assert kind in assistant.KNOWN_TYPES
        assert kind not in assistant.IMPLEMENTED_TYPES, (
            f"{kind} is claimed by both the record assistant and a plugin"
        )


# ---------------------------------------------------------------------------
# A disabled provider must not be usable through the record assistant
# ---------------------------------------------------------------------------


def test_provider_and_model_refuses_a_disabled_provider(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.aiservices.providers.load",
        lambda provider_id: {"_id": provider_id, "enabled": False},
    )

    with pytest.raises(assistant.AssistantError) as caught:
        assistant.provider_and_model({"provider": {"id": "p1"}, "model": {"id": "m1"}})

    assert caught.value.status_code == 403


def test_provider_and_model_allows_an_enabled_provider(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.aiservices.providers.load",
        lambda provider_id: {"_id": provider_id, "enabled": True},
    )

    provider, model = assistant.provider_and_model({"provider": {"id": "p1"}, "model": {"id": "m1"}})

    assert provider["_id"] == "p1"
    assert model == "m1"


def test_provider_and_model_defaults_a_missing_enabled_field_to_true(monkeypatch):
    """A provider created before the switch existed must not become unusable."""
    monkeypatch.setattr(
        "archihub.api.aiservices.providers.load", lambda provider_id: {"_id": provider_id}
    )

    provider, _model = assistant.provider_and_model({"provider": {"id": "p1"}, "model": {"id": "m1"}})

    assert provider["_id"] == "p1"
