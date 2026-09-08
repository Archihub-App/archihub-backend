"""manuscriptAIAnalysis — the decisions that decide whether a transcription
lands on the right handwriting.

Nothing here calls a model. What is tested is everything around the call, which
is where this plugin can be wrong without looking wrong: which page image a
block's coordinates are measured against, which region of it gets cut out, and
which entry on the record the answer is written back into. A transcription
stored against the wrong page is not a visible failure - it is a document that
now says something it does not say.
"""

from __future__ import annotations

import pytest

plugin = pytest.importorskip(
    "archihub.plugins.manuscriptAIAnalysis",
    reason="manuscriptAIAnalysis is not installed in this checkout",
)


# ---------------------------------------------------------------------------
# Finding the segmentation result
# ---------------------------------------------------------------------------


def test_an_entry_is_chosen_by_the_type_it_declares_not_by_its_key():
    """A record segmented by any producer must be covered - the key names in
    the bulk query are a narrowing, not the selection rule."""
    processing = {
        "fileProcessing": {"type": "document"},
        "ocrProcessingHF": {"type": "lt_extraction", "result": []},
        "somethingElse": {"type": "lt_extraction", "result": []},
        "transcribeWhisperX": {"type": "av_transcribe"},
    }

    keys = [key for key, _entry in plugin.extraction_entries(processing)]

    assert sorted(keys) == ["ocrProcessingHF", "somethingElse"]


@pytest.mark.parametrize("processing", [None, {}, "text", [], {"x": "not a dict"}, {"x": {}}])
def test_a_record_with_nothing_segmented_yields_no_entries(processing):
    assert plugin.extraction_entries(processing) == []


def test_a_result_held_in_chunk_documents_is_recognised():
    assert plugin.is_chunked({"result_storage": {"type": "chunked"}}) is True


@pytest.mark.parametrize("entry", [{}, {"result_storage": {}}, {"result_storage": {"type": "inline"}}])
def test_an_inline_result_is_not_reported_as_chunked(entry):
    assert plugin.is_chunked(entry) is False


# ---------------------------------------------------------------------------
# Which page a single-page run means
# ---------------------------------------------------------------------------


def _page(number, *blocks):
    return {"page": number, "blocks": list(blocks)}


def _manuscript(text=""):
    return {"type": "Manuscript", "text": text, "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1}}


def test_a_full_run_covers_every_page():
    result = [_page(1), _page(2), _page(3)]
    assert plugin.selected_pages(result, None) == result


def test_a_page_is_found_by_its_own_number_not_its_position():
    """A partial segmentation run stores fewer pages than the document has, so
    position N-1 is some other page - and its blocks would be transcribed and
    the answers written back under this page's number."""
    result = [_page(2), _page(5), _page(9)]

    assert plugin.selected_pages(result, 5) == [_page(5)]
    assert plugin.selected_pages(result, 2) == [_page(2)]


def test_asking_for_a_page_that_was_never_segmented_selects_nothing():
    assert plugin.selected_pages([_page(1), _page(2)], 7) == []


def test_a_malformed_page_entry_is_dropped_rather_than_indexed():
    assert plugin.selected_pages(["not a page", None, _page(1)], None) == [_page(1)]


@pytest.mark.parametrize("body,expected", [
    ({}, None),
    ({"page_only": False, "opts": {"page": 4}}, None),
    ({"page_only": True, "opts": {"page": 4}}, 4),
    ({"page_only": True}, 1),
    ({"page_only": True, "opts": None}, 1),
    ({"page_only": True, "opts": {}}, 1),
    ({"page_only": True, "opts": {"page": "no"}}, 1),
])
def test_which_page_a_run_asks_for(body, expected):
    assert plugin.requested_page(body) == expected


# ---------------------------------------------------------------------------
# Which blocks are this plugin's to fill in
# ---------------------------------------------------------------------------


def test_only_manuscript_blocks_with_a_region_are_selected():
    page = _page(
        1,
        {"type": "Manuscript", "bbox": {"x": 0, "y": 0, "width": 1, "height": 1}},
        {"type": "Text", "bbox": {"x": 0, "y": 0, "width": 1, "height": 1}},
        {"type": "Manuscript"},
        "not a block",
    )

    blocks = plugin.manuscript_blocks(page)

    assert len(blocks) == 1
    assert blocks[0]["type"] == "Manuscript"


def test_a_page_with_no_blocks_at_all_is_not_an_error():
    assert plugin.manuscript_blocks({"page": 1}) == []


# ---------------------------------------------------------------------------
# The crop, and where the outline lands inside it
# ---------------------------------------------------------------------------


def test_a_block_in_open_space_is_padded_on_every_side():
    bbox = {"x": 0.4, "y": 0.4, "width": 0.2, "height": 0.2}

    crop, outline = plugin.crop_geometry(bbox, 1000, 1000, padding=0.5)

    # Block is 400..600; padding is half of 200 = 100 each way.
    assert crop == (300.0, 300.0, 700.0, 700.0)
    # And inside that crop the block sits at 100..300.
    assert outline == (100.0, 100.0, 300.0, 300.0)


def test_a_block_against_the_edge_clamps_the_crop_to_the_page():
    bbox = {"x": 0.0, "y": 0.0, "width": 0.2, "height": 0.2}

    crop, _outline = plugin.crop_geometry(bbox, 1000, 1000, padding=0.5)

    assert crop == (0.0, 0.0, 300.0, 300.0)


def test_the_outline_follows_the_clamped_crop_rather_than_the_padding():
    """The whole point of returning both: when the crop is clamped the block is
    no longer `padding` pixels in from the corner, and an outline drawn at the
    padding offset marks the wrong region for the model to read."""
    bbox = {"x": 0.0, "y": 0.0, "width": 0.2, "height": 0.2}

    _crop, outline = plugin.crop_geometry(bbox, 1000, 1000, padding=0.5)

    assert outline == (0.0, 0.0, 200.0, 200.0)


def test_a_block_filling_the_page_produces_the_whole_page():
    crop, outline = plugin.crop_geometry(
        {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}, 800, 600, padding=0.8
    )
    assert crop == (0.0, 0.0, 800.0, 600.0)
    assert outline == (0.0, 0.0, 800.0, 600.0)


def test_a_block_with_no_geometry_has_no_area_to_send():
    crop, _outline = plugin.crop_geometry({}, 1000, 1000)
    assert plugin.has_area(crop) is False


def test_a_real_region_has_area():
    crop, _outline = plugin.crop_geometry(
        {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}, 1000, 1000
    )
    assert plugin.has_area(crop) is True


# ---------------------------------------------------------------------------
# Selecting records
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, resources=(), records=()):
        self.resources = list(resources)
        self.records = list(records)
        self.queries = []

    def get_all_records(self, collection, filters, fields=None, sort=None):
        self.queries.append((collection, filters))
        return list(self.resources if collection == "resources" else self.records)


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo(resources=[{"_id": "r1"}, {"_id": "r2"}])
    monkeypatch.setattr(plugin, "_mongo", lambda: fake)
    return fake


def test_an_explicit_record_list_is_used_as_given(mongo):
    filters = plugin.record_filters({"records": ["6a88b6e74bb8b789b188db90"]})
    assert list(filters) == ["_id"]
    assert mongo.queries == []


def test_a_selection_with_no_content_type_is_refused(mongo):
    with pytest.raises(ValueError):
        plugin.record_filters({})


def test_a_selection_covers_only_segmented_documents(mongo):
    filters = plugin.record_filters({"post_type": "expediente"})

    assert filters[f"processing.{plugin.SOURCE_KEY}.type"] == plugin.SOURCE_TYPE
    assert filters["parent.id"] == {"$in": ["r1", "r2"]}
    assert filters["$or"] == [
        {f"processing.{producer}.type": "lt_extraction"}
        for producer in plugin.EXTRACTION_PRODUCERS
    ]


def test_a_parent_without_a_resource_list_is_not_an_error(mongo):
    plugin.record_filters({"post_type": "expediente", "parent": "6a88b6e74bb8b789b188db90"})
    _collection, resource_filters = mongo.queries[0]
    assert "$or" in resource_filters


# ---------------------------------------------------------------------------
# Reading a record end to end, against real page images
# ---------------------------------------------------------------------------


@pytest.fixture
def pages(tmp_path):
    """Three page images, ordered only by how their names sort.

    THE SUFFIX IS ONE DIGIT HERE ON PURPOSE. The renderer pads page numbers to
    the width the page count needs, so a three-page document and a
    three-hundred-page one are named differently. Code that rebuilds a page's
    filename has to guess that width; code that indexes the sorted listing -
    which is how `records/viewers.py` decides which image is page N - does not
    care, and this fixture is what holds it to that.
    """
    from PIL import Image

    directory = tmp_path / "web" / "big"
    directory.mkdir(parents=True)
    for index, colour in enumerate(("red", "green", "blue"), start=1):
        Image.new("RGB", (400, 400), colour).save(directory / f"page_0001-{index}.jpg")
    return directory


@pytest.fixture
def record_environment(monkeypatch, pages):
    """A record whose page images exist and whose model answers predictably."""
    stored = {}
    sent = []

    monkeypatch.setattr(plugin, "_page_directory", lambda record: pages)
    monkeypatch.setattr(
        plugin,
        "call_model",
        lambda provider, model, messages, options: (
            sent.append(messages) or f"read-{len(sent)}"
        ),
    )
    monkeypatch.setattr(
        plugin.plugin_data,
        "store_processing_result",
        lambda record_id, key, entry: stored.__setitem__((record_id, key), entry),
    )
    return stored, sent


def _record(result, key="ocrProcessingHF"):
    return {
        "_id": "6a88b6e74bb8b789b188db90",
        "processing": {
            "fileProcessing": {"type": "document", "path": "2026/01/abc"},
            key: {"type": "lt_extraction", "model": "rtdetr", "result": result},
        },
    }


def test_every_manuscript_block_is_transcribed_and_written_back(record_environment):
    stored, _sent = record_environment
    record = _record([_page(1, _manuscript()), _page(2, _manuscript())])

    written = plugin._process_record(record, "openai", "gpt-4o", {}, None, "alice")

    assert written == 2
    entry = stored[("6a88b6e74bb8b789b188db90", "ocrProcessingHF")]
    assert [p["blocks"][0]["text"] for p in entry["result"]] == ["read-1", "read-2"]


def test_the_rest_of_the_entry_survives_the_write(record_environment):
    """Only `result` changes - the type and the producer's own model name are
    what the readers dispatch on."""
    stored, _sent = record_environment

    plugin._process_record(_record([_page(1, _manuscript())]), "openai", "gpt-4o", {}, None, "a")

    entry = stored[("6a88b6e74bb8b789b188db90", "ocrProcessingHF")]
    assert entry["type"] == "lt_extraction"
    assert entry["model"] == "rtdetr"


def test_a_single_page_run_only_transcribes_that_page(record_environment):
    stored, _sent = record_environment
    record = _record([_page(1, _manuscript()), _page(2, _manuscript())])

    written = plugin._process_record(record, "openai", "gpt-4o", {}, 2, "alice")

    assert written == 1
    entry = stored[("6a88b6e74bb8b789b188db90", "ocrProcessingHF")]
    assert entry["result"][0]["blocks"][0]["text"] == ""
    assert entry["result"][1]["blocks"][0]["text"] == "read-1"


def test_a_page_is_the_nth_image_in_the_sorted_listing_whatever_it_is_called(record_environment, pages):
    """Renaming the images - keeping their order - must change nothing. This is
    what makes the block coordinates line up with the page the reader shows."""
    stored, sent = record_environment
    for index, image in enumerate(sorted(pages.iterdir()), start=1):
        image.rename(pages / f"scan_{index}.png")

    written = plugin._process_record(_record([_page(2, _manuscript())]), "o", "m", {}, None, "a")

    assert written == 1
    entry = stored[("6a88b6e74bb8b789b188db90", "ocrProcessingHF")]
    assert entry["result"][0]["blocks"][0]["text"] == "read-1"


def test_a_page_with_no_matching_image_is_skipped_rather_than_guessed(record_environment):
    """Three images exist; page 9 has no image, so its coordinates cannot be
    measured against anything."""
    stored, _sent = record_environment

    written = plugin._process_record(_record([_page(9, _manuscript())]), "o", "m", {}, None, "a")

    assert written == 0
    assert stored == {}


def test_nothing_is_written_when_there_is_no_manuscript_to_read(record_environment):
    stored, _sent = record_environment
    record = _record([_page(1, {"type": "Text", "text": "printed", "bbox": {"x": 0, "y": 0, "width": 1, "height": 1}})])

    written = plugin._process_record(record, "o", "m", {}, None, "a")

    assert written == 0
    assert stored == {}


def test_a_chunked_result_is_skipped_rather_than_written_to_the_wrong_place(record_environment):
    stored, _sent = record_environment
    record = _record([_page(1, _manuscript())])
    record["processing"]["ocrProcessingHF"]["result_storage"] = {"type": "chunked"}

    written = plugin._process_record(record, "o", "m", {}, None, "a")

    assert written == 0
    assert stored == {}


def test_each_segmentation_entry_is_written_under_its_own_key(record_environment):
    """Writing one dotted path per producer is what stops a second producer's
    result being replaced by this record as it was last read."""
    stored, _sent = record_environment
    record = _record([_page(1, _manuscript())])
    record["processing"]["ocrProcessing"] = {
        "type": "lt_extraction",
        "result": [_page(1, _manuscript())],
    }

    plugin._process_record(record, "o", "m", {}, None, "a")

    assert set(stored) == {
        ("6a88b6e74bb8b789b188db90", "ocrProcessingHF"),
        ("6a88b6e74bb8b789b188db90", "ocrProcessing"),
    }


def test_the_model_is_sent_an_image_and_the_instruction(record_environment):
    _stored, sent = record_environment

    plugin._process_record(_record([_page(1, _manuscript())]), "o", "m", {}, None, "a")

    system, user = sent[0]
    assert system["role"] == "system"
    assert user["content"][0]["type"] == "image_url"
    assert user["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert user["content"][1]["text"] == plugin.USER_PROMPT


def test_the_crop_is_built_in_memory_and_leaves_no_file_behind(record_environment, pages):
    """A crop written out puts a record id into a filename and stays there
    whenever the cleanup does not run."""
    before = set(pages.parent.parent.rglob("*"))

    plugin._process_record(_record([_page(1, _manuscript())]), "o", "m", {}, None, "a")

    assert set(pages.parent.parent.rglob("*")) == before


# ---------------------------------------------------------------------------
# The task, and the name it is queued under
# ---------------------------------------------------------------------------


def test_the_registered_task_name_is_the_one_a_queued_message_resolves_by():
    assert plugin.process.name == "manuscriptAIAnalysis.processing"


def test_both_routes_queue_the_same_task_under_different_labels():
    """The two labels are what the task list shows; the work is identical."""
    assert plugin.TASK_PROCESSING == "manuscriptAIAnalysis.processing"
    assert plugin.TASK_BULK == "manuscriptAIAnalysis.bulk"


def test_a_run_naming_no_model_is_refused_before_anything_is_sent(mongo):
    with pytest.raises(ValueError):
        plugin.process({"records": ["6a88b6e74bb8b789b188db90"], "provider": "openai"}, "alice")


def test_one_failing_record_does_not_end_the_run(monkeypatch, mongo):
    """A selection run over a paid API is exactly where stopping at the first
    unreadable record wastes the rest."""
    ids = ["6a88b6e74bb8b789b188db9%d" % n for n in (1, 2, 3)]
    mongo.records = [{"_id": value} for value in ids]
    seen = []

    def flaky(record, *args):
        seen.append(record["_id"])
        if record["_id"] == ids[1]:
            raise RuntimeError("no page images")
        return 1

    monkeypatch.setattr(plugin, "_process_record", flaky)

    message = plugin.process(
        {"records": ids, "provider": "openai", "model": "gpt-4o"}, "alice"
    )

    # Every record was attempted, and the failure is reported rather than
    # folded into the success count.
    assert seen == ids
    assert "2" in message and "1" in message


# ---------------------------------------------------------------------------
# The routes, through the real routing stack
# ---------------------------------------------------------------------------


def test_no_route_of_this_plugin_answers_an_anonymous_caller():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from archihub.core.errors import register_exception_handlers
    from archihub.plugins.framework.mounting import build_plugin

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(build_plugin(plugin.SLUG).build())
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/manuscriptAIAnalysis/bulk", json={}).status_code == 401
    assert client.post("/manuscriptAIAnalysis/blockProcessing", json={}).status_code == 401
    assert client.get("/manuscriptAIAnalysis/settings/bulk").status_code == 401
    assert client.post("/manuscriptAIAnalysis/settings", data={"data": "{}"}).status_code == 401
