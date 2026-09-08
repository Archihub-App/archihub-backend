"""ocrProcessingHF — the decisions that decide whether a run is correct.

Nothing here loads a detection model or touches a database. What is under test
is the part that goes wrong silently: which model a request is allowed to name,
which pages a partial re-run replaces, and which detections survive filtering.
A segmentation run that quietly stores the wrong pages looks exactly like one
that worked, right up until someone opens the reader.
"""

from __future__ import annotations

import pytest

#: SKIPPED WHEN THE PLUGIN IS NOT INSTALLED - `archihub/plugins/*` is gitignored
#: apart from the ones that ship with the backend, so a bare import here would
#: turn a checkout without it into a collection error.
plugin = pytest.importorskip(
    "archihub.plugins.ocrProcessingHF",
    reason="ocrProcessingHF is not installed in this checkout",
)


# ---------------------------------------------------------------------------
# The model name reaches a directory
# ---------------------------------------------------------------------------


@pytest.fixture
def one_model(monkeypatch):
    monkeypatch.setattr(plugin, "available_models", lambda: ["rtdetr_v2"])


def test_a_model_that_exists_is_accepted(one_model):
    assert plugin.resolve_model("rtdetr_v2") == "rtdetr_v2"


def test_both_reserved_names_are_accepted_without_a_directory(one_model):
    assert plugin.resolve_model("*") == "*"
    assert plugin.resolve_model("existing") == "existing"


@pytest.mark.parametrize(
    "requested",
    ["../../etc", "rtdetr_v2/../..", "/etc", "unknown_model", "", None, 7, ["rtdetr_v2"]],
)
def test_anything_else_is_refused_before_it_becomes_a_path(one_model, requested):
    """The value arrives in a request body and is joined onto MODEL_ROOT."""
    with pytest.raises(ValueError):
        plugin.resolve_model(requested)


def test_a_missing_models_directory_is_no_models_rather_than_an_error(monkeypatch, tmp_path):
    """The directory is gitignored, so a checkout has none until one is copied in."""
    monkeypatch.setattr(plugin, "MODEL_ROOT", tmp_path / "absent")
    assert plugin.available_models() == []


def test_pycache_is_not_offered_as_a_model(monkeypatch, tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "rtdetr_v2").mkdir()
    (tmp_path / "notes.txt").write_text("not a model")
    monkeypatch.setattr(plugin, "MODEL_ROOT", tmp_path)

    assert plugin.available_models() == ["rtdetr_v2"]


# ---------------------------------------------------------------------------
# A partial re-run replaces the pages it produced, and only those
# ---------------------------------------------------------------------------


def _page(number, text):
    return {"page": number, "blocks": [{"text": text}]}


def test_a_single_page_rerun_replaces_that_page_and_keeps_the_rest():
    stored = [_page(1, "one"), _page(2, "two"), _page(3, "three")]

    merged = plugin.merge_page_results(stored, [_page(2, "redone")])

    assert [entry["page"] for entry in merged] == [1, 2, 3]
    assert merged[1]["blocks"][0]["text"] == "redone"
    assert merged[0]["blocks"][0]["text"] == "one"
    assert merged[2]["blocks"][0]["text"] == "three"


def test_a_rerun_of_the_last_page_does_not_drop_an_earlier_one():
    """The failure this replaces: pages were dropped by a counter left over from
    the read loop, which names whichever page the loop stopped on rather than
    the page that was redone."""
    stored = [_page(1, "one"), _page(2, "two"), _page(3, "three")]

    merged = plugin.merge_page_results(stored, [_page(1, "redone")])

    assert [entry["page"] for entry in merged] == [1, 2, 3]
    assert merged[0]["blocks"][0]["text"] == "redone"
    assert merged[1]["blocks"][0]["text"] == "two"


def test_a_page_produced_for_the_first_time_is_added_in_order():
    merged = plugin.merge_page_results([_page(1, "one"), _page(3, "three")], [_page(2, "new")])
    assert [entry["page"] for entry in merged] == [1, 2, 3]


def test_merging_into_nothing_yields_what_was_produced():
    assert plugin.merge_page_results(None, [_page(1, "one")]) == [_page(1, "one")]


def test_a_malformed_stored_page_is_dropped_rather_than_raising():
    merged = plugin.merge_page_results(["not a page", None, _page(1, "one")], [_page(2, "two")])
    assert [entry["page"] for entry in merged] == [1, 2]


# ---------------------------------------------------------------------------
# Which detections survive
# ---------------------------------------------------------------------------


def test_a_box_almost_entirely_inside_another_is_dropped():
    outer = [0, 0, 100, 100]
    inner = [1, 1, 99, 99]
    assert plugin.is_contained(inner, [outer, inner]) is True


def test_a_box_only_partly_overlapping_another_is_kept():
    assert plugin.is_contained([0, 0, 100, 100], [[50, 50, 150, 150]]) is False


def test_a_box_is_not_contained_by_itself():
    box = [0, 0, 10, 10]
    assert plugin.is_contained(box, [box]) is False


def test_a_zero_area_box_is_not_reported_as_contained():
    """Division by the box's own area is what decides containment."""
    assert plugin.is_contained([5, 5, 5, 5], [[0, 0, 10, 10]]) is False


# ---------------------------------------------------------------------------
# Matching PDF words to a region
# ---------------------------------------------------------------------------


def _word(text, x0, x1, top, bottom):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}


def test_only_the_words_inside_the_region_are_returned():
    words = [
        _word("inside", 10, 40, 10, 20),
        _word("right", 600, 650, 10, 20),
        _word("below", 10, 40, 600, 650),
    ]
    bbox = {"x_1": 0.0, "y_1": 0.0, "x_2": 0.5, "y_2": 0.5}

    matched = plugin.words_within(words, bbox, 1000, 1000)

    assert [word["text"] for word in matched] == ["inside"]


def test_a_page_with_no_dimensions_matches_nothing_rather_than_dividing_by_zero():
    assert plugin.words_within([_word("x", 1, 2, 1, 2)], {"x_1": 0, "y_1": 0, "x_2": 1, "y_2": 1}, 0, 0) == []


# ---------------------------------------------------------------------------
# Region geometry round-trips, which is what `existing` depends on
# ---------------------------------------------------------------------------


def test_a_stored_region_converts_back_to_the_pixels_it_came_from():
    box = [100.0, 200.0, 300.0, 500.0]

    stored = plugin.normalised_bbox(box, 1000, 2000)
    assert plugin.pixel_box(stored, 1000, 2000) == pytest.approx(box)


def test_a_stored_region_with_no_geometry_reads_as_the_origin():
    assert plugin.pixel_box({}, 100, 100) == [0.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# Which page a single-page run means
# ---------------------------------------------------------------------------


def test_a_full_run_asks_for_no_particular_page():
    assert plugin.requested_page({}) is None
    assert plugin.requested_page({"page_only": False, "opts": {"page": 4}}) is None


def test_a_single_page_run_reads_the_page_out_of_opts():
    assert plugin.requested_page({"page_only": True, "opts": {"page": 4}}) == 4


@pytest.mark.parametrize("body", [
    {"page_only": True},
    {"page_only": True, "opts": None},
    {"page_only": True, "opts": {}},
    {"page_only": True, "opts": {"page": "not a number"}},
])
def test_a_single_page_run_with_no_usable_page_falls_back_to_the_first(body):
    """The record action sends `page_only` with no `opts` at all."""
    assert plugin.requested_page(body) == 1


# ---------------------------------------------------------------------------
# Selecting records
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, resources=()):
        self.resources = list(resources)
        self.queries = []

    def get_all_records(self, collection, filters, fields=None, sort=None):
        self.queries.append((collection, filters))
        return list(self.resources)


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


def test_a_selection_covers_only_already_rendered_documents(mongo):
    filters = plugin.record_filters({"post_type": "expediente"})

    assert filters[f"processing.{plugin.SOURCE_KEY}"] == {"$exists": True}
    assert filters[f"processing.{plugin.SOURCE_KEY}.type"] == plugin.SOURCE_TYPE
    assert filters["parent.id"] == {"$in": ["r1", "r2"]}


def test_without_overwrite_an_already_processed_record_is_excluded(mongo):
    filters = plugin.record_filters({"post_type": "expediente"})
    assert filters[f"processing.{plugin.PROCESSING_KEY}"] == {"$exists": False}


def test_with_overwrite_every_matching_record_is_included(mongo):
    filters = plugin.record_filters({"post_type": "expediente", "overwrite": True})
    assert f"processing.{plugin.PROCESSING_KEY}" not in filters


def test_a_parent_without_a_resource_list_is_not_an_error(mongo):
    """`parent` present and `resources` absent is what the tree selection sends."""
    plugin.record_filters({"post_type": "expediente", "parent": "6a88b6e74bb8b789b188db90"})

    _collection, resource_filters = mongo.queries[0]
    assert "$or" in resource_filters


# ---------------------------------------------------------------------------
# What the result is called, checked against its consumer
# ---------------------------------------------------------------------------


def test_the_result_is_stored_as_the_extraction_type_the_readers_render():
    from archihub.core.log_actions import log_actions

    assert plugin.PROCESSING_TYPE == "lt_extraction"
    # The readers dispatch on this string, and the log screen names it.
    assert plugin.PROCESSING_TYPE in log_actions


def test_the_processing_key_is_the_slug():
    """It is also the directory name, the route prefix and the task stem, and
    the bulk filter tests its absence to find unprocessed records."""
    assert plugin.PROCESSING_KEY == plugin.SLUG == "ocrProcessingHF"


def test_the_task_names_are_the_ones_queued_messages_resolve_by():
    assert plugin.bulk.name == "ocrProcessingHF.bulk"
    assert plugin.TASK_BLOCK_PROCESSING == "ocrProcessingHF.blockProcessing"


# ---------------------------------------------------------------------------
# The routes, through the real routing stack
# ---------------------------------------------------------------------------


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from archihub.core.errors import register_exception_handlers
    from archihub.plugins.framework.mounting import build_plugin

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(build_plugin(plugin.SLUG).build())
    return TestClient(app, raise_server_exceptions=False)


def test_no_route_of_this_plugin_answers_an_anonymous_caller():
    client = _client()

    assert client.post("/ocrProcessingHF/bulk", json={}).status_code == 401
    assert client.post("/ocrProcessingHF/blockProcessing", json={}).status_code == 401
    assert client.get("/ocrProcessingHF/settings/bulk").status_code == 401
    assert client.post("/ocrProcessingHF/settings", data={"data": "{}"}).status_code == 401
