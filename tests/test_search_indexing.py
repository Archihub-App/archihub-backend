"""What actually reaches the search index.

The legacy indexer was one 160-line loop body inside
``try: ... except Exception: continue``, so the only observable was a number at
the end of a run - a number that counted failures as successes. Nothing here
could have been written against that shape. These tests exist because the port
splits "what does this resource index as" out from "walk the collection".

Covers ``archihub/api/search/documents.py``, ``archihub/api/search/mapping.py``
and the paging/batching in ``archihub/worker/tasks/indexing.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from archihub.api.search import documents, mapping


def _field(kind: str, destiny: str, **extra) -> dict:
    return {"type": kind, "destiny": destiny, **extra}


def _no_centroid(ident, parent, level):
    return None


def build(resource: dict, fields: list[dict], **kwargs) -> dict:
    kwargs.setdefault("is_article", False)
    kwargs.setdefault("records", [])
    kwargs.setdefault("centroid_lookup", _no_centroid)
    return documents.build_resource_document(resource, fields, **kwargs)


# ---------------------------------------------------------------------------
# Paths into and out of a document
# ---------------------------------------------------------------------------


def test_a_nested_destiny_is_read_and_written_at_the_same_place():
    resource = {"status": "published", "metadata": {"firstLevel": {"title": "A fonds"}}}
    fields = [_field("text", "metadata.firstLevel.title")]

    document = build(resource, fields)

    assert document["metadata"]["firstLevel"]["title"] == "A fonds"


def test_a_missing_value_leaves_the_field_out_rather_than_writing_null():
    """A null in the index is a value Elasticsearch has to type; absence is not."""
    resource = {"status": "published", "metadata": {}}

    document = build(resource, [_field("text", "metadata.firstLevel.title")])

    assert "metadata" not in document or "firstLevel" not in document["metadata"]


def test_a_non_dict_step_is_replaced_rather_than_subscripted():
    """Two schema entries, one a prefix of the other. The original did
    `temp = temp[key]` unconditionally, so the second raised TypeError inside
    the swallow-everything handler and the resource vanished from the index."""
    document = documents.set_by_path({"date": "1990"}, "date.from", "1990-01-01")

    assert document["date"] == {"from": "1990-01-01"}


def test_get_by_path_stops_at_a_value_that_is_not_a_dict():
    assert documents.get_by_path({"a": "text"}, "a.b.c") is None


# ---------------------------------------------------------------------------
# Field kinds
# ---------------------------------------------------------------------------


def test_a_date_is_indexed_as_utc_with_milliseconds():
    resource = {"status": "published", "metadata": {"date": datetime(1990, 5, 4, 12, 30, 15)}}

    document = build(resource, [_field("simple-date", "metadata.date")])

    assert document["metadata"]["date"] == "1990-05-04T12:30:15.000Z"


def test_a_tz_aware_date_is_converted_rather_than_relabelled():
    from datetime import timedelta

    value = datetime(1990, 5, 4, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))

    assert documents.to_utc_iso(value) == "1990-05-04T17:00:00.000Z"


def test_a_date_field_holding_a_string_is_left_alone():
    """The schema says the field is a date; a document may disagree."""
    resource = {"status": "published", "metadata": {"date": "circa 1990"}}

    document = build(resource, [_field("simple-date", "metadata.date")])

    assert "date" not in document.get("metadata", {})


def test_a_multi_select_indexes_its_terms_deduplicated_and_ordered():
    """Ordered on purpose: the original built a `set` and sent it straight to
    Elasticsearch, so the same resource produced a different document on every
    run and no diff of the index against itself meant anything."""
    resource = {
        "status": "published",
        "metadata": {
            "subjects": [
                {"term": "Photography", "id": "1"},
                {"term": "Archives", "id": "2"},
                {"term": "Photography", "id": "1"},
            ]
        },
    }

    document = build(resource, [_field("select-multiple2", "metadata.subjects")])

    assert document["metadata"]["subjects"] == ["Archives", "Photography"]


def test_a_multi_select_entry_with_no_term_is_skipped_not_fatal():
    resource = {"status": "published", "metadata": {"subjects": [{"id": "1"}, {"term": "Maps"}]}}

    document = build(resource, [_field("select-multiple2", "metadata.subjects")])

    assert document["metadata"]["subjects"] == ["Maps"]


def test_a_repeater_is_not_indexed():
    """DELIBERATE, and reproduced from the legacy behaviour - see
    _apply_repeater_dates. Recorded as F46 rather than changed, because turning
    it on risks mapping conflicts that would REMOVE resources from the index."""
    resource = {
        "status": "published",
        "metadata": {"authors": [{"name": "A", "born": datetime(1900, 1, 1)}]},
    }
    fields = [
        _field(
            "repeater",
            "metadata.authors",
            subfields=[{"type": "simple-date", "destiny": "born"}],
        )
    ]

    document = build(resource, fields)

    assert "authors" not in document.get("metadata", {})


def test_a_location_with_explicit_coordinates_becomes_a_geojson_point():
    resource = {
        "status": "published",
        "metadata": {"place": [{"coordinates": [-74.0, 4.6]}]},
    }

    document = build(resource, [_field("location", "metadata.place")])

    assert document["metadata"]["place"] == [{"type": "Point", "coordinates": [-74.0, 4.6]}]


def test_a_malformed_location_costs_that_point_not_the_resource():
    """The original raised here, and the raise was caught by the handler that
    skipped the whole resource."""
    resource = {
        "status": "published",
        "ident": "AH-1",
        "metadata": {"place": [{"coordinates": [1.0, 2.0, 3.0]}, {"coordinates": [-74.0, 4.6]}]},
    }

    document = build(resource, [_field("location", "metadata.place")])

    assert document["metadata"]["place"] == [{"type": "Point", "coordinates": [-74.0, 4.6]}]
    assert document["ident"] == "AH-1"


def test_a_location_given_as_administrative_levels_resolves_the_most_specific():
    seen = []

    def centroid(ident, parent, level):
        seen.append((ident, parent, level))
        return [{"type": "Point", "coordinates": [-74.1, 4.7]}] if level == 2 else None

    resource = {
        "status": "published",
        "metadata": {
            "place": [
                {
                    "level_0": {"ident": "CO"},
                    "level_1": {"ident": "CUN"},
                    "level_2": {"ident": "BOG"},
                }
            ]
        },
    }

    document = build(
        resource, [_field("location", "metadata.place")], centroid_lookup=centroid
    )

    # Most specific first, and it stops as soon as one resolves.
    assert seen == [("BOG", "CUN", 2)]
    assert document["metadata"]["place"][0]["coordinates"] == [-74.1, 4.7]


def test_an_unresolvable_boundary_falls_back_to_the_level_above():
    def centroid(ident, parent, level):
        return [{"type": "Point", "coordinates": [-74.0, 4.0]}] if level == 0 else None

    resource = {
        "status": "published",
        "metadata": {"place": [{"level_0": {"ident": "CO"}, "level_1": {"ident": "XX"}}]},
    }

    document = build(
        resource, [_field("location", "metadata.place")], centroid_lookup=centroid
    )

    assert document["metadata"]["place"] == [{"type": "Point", "coordinates": [-74.0, 4.0]}]


# ---------------------------------------------------------------------------
# The document as a whole
# ---------------------------------------------------------------------------


def test_a_resource_with_no_status_is_not_indexed():
    """Nothing decides whether it is publicly visible, so nothing here may."""
    with pytest.raises(documents.NotIndexable):
        build({"post_type": "fondo"}, [])


def test_access_rights_default_to_public_but_the_resource_always_wins():
    assert build({"status": "published"}, [])["accessRights"] == "public"
    assert (
        build({"status": "published", "accessRights": "reserved"}, [])["accessRights"]
        == "reserved"
    )


def test_a_plugin_may_extend_the_document_but_not_widen_its_access():
    """The hook runs before the resource's own access right is applied, so a
    plugin cannot publish restricted material by rewriting the field."""

    def hook(name, document, resource):
        document["accessRights"] = "public"
        document["extra"] = "from a plugin"
        return document

    document = build(
        {"status": "published", "accessRights": "reserved"}, [], hook_call=hook
    )

    assert document["accessRights"] == "reserved"
    assert document["extra"] == "from a plugin"


def test_an_article_is_flattened_to_searchable_text():
    resource = {
        "status": "published",
        "articleBody": [
            {"type": "paragraph", "content": "<p>The <b>first</b> paragraph.</p>"},
            {"type": "image", "content": "ignored"},
            {"type": "paragraph", "content": "<p>Second.</p>"},
        ],
    }

    document = build(resource, [], is_article=True)

    assert document["article"] == "The first paragraph. Second."


def test_a_non_article_has_no_article_text():
    assert build({"status": "published"}, [])["article"] is None


def test_files_is_a_count_not_a_list():
    resource = {"status": "published", "filesObj": [{"id": "1"}, {"id": "2"}]}

    assert build(resource, [])["files"] == 2


def test_strip_html_tidies_punctuation_spacing():
    assert documents.strip_html("<p>Hello , world.Next</p>") == "Hello, world. Next"


def test_strip_html_does_not_invent_a_separator_between_blocks():
    """It is called per paragraph; the joining is the caller's, which is why an
    article's paragraphs end up space-separated and a single fragment does not
    gain spaces it never had."""
    assert documents.strip_html("<p>one</p><p>two</p>") == "onetwo"


# ---------------------------------------------------------------------------
# Files attached to a resource
# ---------------------------------------------------------------------------


def test_records_keep_the_stored_display_order():
    resource = {
        "filesObj": [
            {"id": "b", "order": 2, "tag": "back"},
            {"id": "a", "order": 1, "tag": "front"},
        ]
    }

    resolved = documents.resolve_records(resource, {"a": "image", "b": "image"})

    assert [entry["id"] for entry in resolved] == ["a", "b"]
    assert resolved[0]["tag"] == "front"


def test_a_file_with_an_unusable_order_sorts_first_rather_than_raising():
    resource = {"filesObj": [{"id": "a", "order": "?"}, {"id": "b", "order": 1}]}

    resolved = documents.resolve_records(resource, {"a": "image", "b": "image"})

    assert [entry["id"] for entry in resolved] == ["a", "b"]


def test_an_unprocessed_file_is_left_out():
    resource = {"filesObj": [{"id": "a"}, {"id": "b"}]}

    resolved = documents.resolve_records(resource, {"a": "image"})

    assert [entry["id"] for entry in resolved] == ["a"]


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def test_a_known_field_kind_maps_to_its_elasticsearch_type():
    built = mapping.build_resources_mapping({"metadata": {"title": {"type": "text"}}})

    assert built["properties"]["metadata"]["properties"]["title"]["analyzer"] == "analyzer_spanish"


def test_an_unknown_field_kind_is_dropped_rather_than_guessed():
    """Elasticsearch would otherwise infer the type from the first document
    carrying it, and the guess sticks until the next regeneration."""
    built = mapping.build_resources_mapping({"metadata": {"weird": {"type": "hologram"}}})

    assert "metadata" not in built["properties"]


def test_a_malformed_schema_entry_costs_one_field_not_the_whole_request():
    """The original raised ValueError, which the route turned into a 500 that
    did not say which entry was at fault."""
    built = mapping.build_resources_mapping({"metadata": {"ok": {"type": "text"}, "bad": "nonsense"}})

    assert set(built["properties"]["metadata"]["properties"]) == {"ok"}


def test_the_file_field_is_never_indexed():
    built = mapping.build_resources_mapping({"file": {"type": "text"}, "ident": {"type": "text"}})

    assert "file" not in built["properties"]


def test_the_system_fields_are_always_present():
    built = mapping.build_resources_mapping({})

    assert {"post_type", "status", "ident", "createdAt", "files", "article"} <= set(
        built["properties"]
    )


def test_building_a_mapping_does_not_mutate_the_shared_definitions():
    """`file` used to be popped out of the built mapping; if the returned
    fragments were the module constants themselves, one build would corrupt
    every later one."""
    first = mapping.build_resources_mapping({"metadata": {"title": {"type": "text"}}})
    first["properties"]["metadata"]["properties"]["title"]["type"] = "corrupted"

    second = mapping.build_resources_mapping({"metadata": {"title": {"type": "text"}}})

    assert second["properties"]["metadata"]["properties"]["title"]["type"] == "text"
