"""Search.

The first section pins that a public caller never chooses the publication state:
every resource is indexed whatever its state.
"""

from __future__ import annotations

import pytest

from archihub.api.search import query, rss, services


# ---------------------------------------------------------------------------
# A public caller does not choose what to search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("requested", ["draft", "created", "updated", "deleted", "published", None, {"$ne": None}])
def test_a_public_caller_always_gets_published_whatever_they_ask_for(requested):
    """16 real drafts were reachable this way."""
    assert query.resolve_status(
        requested, public=True, may_see_drafts=True, may_see_deleted=True
    ) == ["published"]


def test_the_built_public_query_can_only_match_published():
    built = query.build(
        {"post_type": ["x"], "status": "draft"},
        statuses=query.resolve_status("draft", public=True, may_see_drafts=True, may_see_deleted=True),
        access_rights=["public"],
        declared_fields=set(),
        sortable_text_fields=set(),
    )

    status_filter = next(
        f for f in built["query"]["bool"]["filter"] if "status.keyword" in str(f)
    )
    assert status_filter == {"terms": {"status.keyword": ["published"]}}


def test_an_authenticated_caller_without_the_role_cannot_search_drafts():
    with pytest.raises(query.InvalidSearch):
        query.resolve_status("draft", public=False, may_see_drafts=False, may_see_deleted=False)


def test_a_publisher_searching_drafts_gets_all_three_pre_publication_states():
    """"draft" covers three states, exactly as the resources listing treats it."""
    assert query.resolve_status(
        "draft", public=False, may_see_drafts=True, may_see_deleted=False
    ) == ["draft", "created", "updated"]


def test_the_recycle_bin_needs_an_administrator():
    with pytest.raises(query.InvalidSearch):
        query.resolve_status("deleted", public=False, may_see_drafts=True, may_see_deleted=False)

    assert query.resolve_status(
        "deleted", public=False, may_see_drafts=True, may_see_deleted=True
    ) == ["deleted"]


def test_an_unknown_status_is_refused():
    with pytest.raises(query.InvalidSearch):
        query.resolve_status("invented", public=False, may_see_drafts=True, may_see_deleted=True)


def test_an_anonymous_caller_only_holds_the_public_right():
    assert services.access_rights_for(None) == ["public"]


# ---------------------------------------------------------------------------
# The keyword is not a query language
# ---------------------------------------------------------------------------


def _build(body, **overrides):
    options = {
        "statuses": ["published"],
        "access_rights": ["public"],
        "declared_fields": set(),
        "sortable_text_fields": set(),
    }
    options.update(overrides)
    return query.build(body, **options)


def test_the_keyword_uses_simple_query_string_not_the_full_dsl():
    """`query_string` accepts regular expressions and leading wildcards - a
    denial of service on an open endpoint - and throws on malformed syntax."""
    built = _build({"post_type": ["x"], "keyword": "archive"})

    matcher = built["query"]["bool"]["must"][0]
    assert "simple_query_string" in matcher
    assert "query_string" not in matcher


def test_dangerous_query_features_are_not_enabled():
    built = _build({"post_type": ["x"], "keyword": "a"})

    flags = built["query"]["bool"]["must"][0]["simple_query_string"]["flags"]
    assert "PREFIX" not in flags
    assert "ALL" not in flags


def test_a_malformed_keyword_is_a_search_not_a_failure():
    """A stray bracket typed into a search box is not a 500."""
    built = _build({"post_type": ["x"], "keyword": 'unbalanced "quote and (bracket'})

    assert built["query"]["bool"]["must"][0]["simple_query_string"]["lenient"] is True


def test_an_empty_keyword_adds_no_matcher():
    built = _build({"post_type": ["x"], "keyword": "   "})

    assert "must" not in built["query"]["bool"]


# ---------------------------------------------------------------------------
# `_source` is an allowlist
# ---------------------------------------------------------------------------


def test_a_caller_cannot_name_an_arbitrary_field_to_have_returned():
    """A caller cannot pull fields the content type does not declare."""
    source = query.resolve_source(
        [{"destiny": "metadata.firstLevel.secret"}, {"destiny": "metadata.firstLevel.title"}],
        declared_fields={"metadata.firstLevel.title"},
    )

    assert "metadata.firstLevel.secret" not in source
    assert "metadata.firstLevel.title" in source


def test_the_base_fields_are_always_present():
    source = query.resolve_source([], declared_fields=set())

    assert set(query.BASE_SOURCE).issubset(source)


def test_a_column_that_is_not_a_string_is_ignored():
    source = query.resolve_source([{"destiny": {"$ne": None}}, 42, None], declared_fields=set())

    assert source == list(query.BASE_SOURCE)


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_a_post_type_is_required():
    with pytest.raises(query.InvalidSearch):
        _build({})


@pytest.mark.parametrize("post_type", [{"$ne": None}, [{"a": 1}], 42, [1, 2]])
def test_a_post_type_that_is_not_text_is_refused(post_type):
    """These become terms in a query; a nested object is not a filter the
    caller is entitled to write."""
    with pytest.raises(query.InvalidSearch):
        _build({"post_type": post_type})


def test_the_page_size_is_capped():
    built = _build({"post_type": ["x"], "size": 100000})

    assert built["size"] == query.MAX_SIZE


@pytest.mark.parametrize("value", [-1, "many", {"$gt": 0}, 1e400])
def test_an_unusable_size_or_page_is_refused(value):
    with pytest.raises(query.InvalidSearch):
        _build({"post_type": ["x"], "size": value})
    with pytest.raises(query.InvalidSearch):
        _build({"post_type": ["x"], "page": value})


def test_paging_beyond_the_searchable_window_is_refused_with_an_explanation():
    """Elasticsearch errors past `max_result_window` rather than returning an
    empty page, so it is bounded before the query is built."""
    with pytest.raises(query.InvalidSearch) as exc:
        _build({"post_type": ["x"], "size": 100, "page": 500})

    assert "narrow" in str(exc.value)


def test_an_unknown_view_type_is_refused():
    with pytest.raises(query.InvalidSearch):
        _build({"post_type": ["x"], "viewType": "carousel"})


# ---------------------------------------------------------------------------
# Filters and views
# ---------------------------------------------------------------------------


def test_only_known_record_types_reach_the_query():
    built = _build({"post_type": ["x"], "record_types": ["image", "nonsense"]})

    terms = [f for f in built["query"]["bool"]["filter"] if "records.type.keyword" in str(f)]
    assert terms[0] == {"terms": {"records.type.keyword": ["image"]}}


def test_a_text_field_is_sorted_by_its_keyword_subfield():
    """A text field is not sortable in Elasticsearch; its `.keyword` is."""
    built = _build(
        {"post_type": ["x"], "sortBy": "metadata.firstLevel.title"},
        sortable_text_fields={"metadata.firstLevel.title"},
    )

    assert built["sort"] == [{"metadata.firstLevel.title.keyword": {"order": "asc"}}]


def test_sorting_by_relevance_adds_no_sort_clause():
    built = _build({"post_type": ["x"], "sortBy": "relevance"})

    assert "sort" not in built


def test_the_gallery_view_filters_to_images():
    built = _build({"post_type": ["x"], "viewType": "gallery"})

    assert "records" in built["_source"]
    assert {"term": {"records.type.keyword": "image"}} in built["query"]["bool"]["filter"]


@pytest.mark.parametrize("view", ["gallery", "blog"])
def test_a_card_view_fetches_the_title_whatever_columns_are_active(view):
    """The card is labelled with the title; without it every card reads 'untitled'."""
    built = _build({"post_type": ["x"], "viewType": view})

    assert query.TITLE_FIELD in built["_source"]
    assert len(built["_source"]) == len(set(built["_source"]))


def test_the_list_view_returns_only_the_columns_asked_for():
    built = _build({"post_type": ["x"], "viewType": "list"})

    assert query.TITLE_FIELD not in built["_source"]


def test_the_blog_view_requires_an_article_and_sorts_newest_first():
    built = _build({"post_type": ["x"], "viewType": "blog"})

    assert "article" in built["_source"]
    assert built["sort"] == [{"createdAt": {"order": "desc"}}]


def test_a_malformed_date_filter_is_skipped_rather_than_raising():
    built = _build(
        {"post_type": ["x"], "date_filters": [{"destiny": "d", "range": ["a"]}, "nonsense"]}
    )

    assert not any("range" in str(f) and "d" in str(f) for f in built["query"]["bool"]["filter"])


# ---------------------------------------------------------------------------
# Shaping the response
# ---------------------------------------------------------------------------


def _hits(*sources):
    return {
        "hits": {
            "total": {"value": len(sources)},
            "hits": [{"_id": f"id{n}", "_source": s} for n, s in enumerate(sources)],
        }
    }


@pytest.fixture
def rights(monkeypatch):
    monkeypatch.setattr(
        services, "_right_terms", lambda: {"reserved": "Reserved collection"}
    )


def test_a_public_result_drops_its_access_right(rights):
    shaped = services.shape(_hits({"accessRights": "public", "post_type": "x"}), {}, view="list")

    assert "accessRights" not in shaped["resources"][0]


def test_a_restricted_result_shows_the_terms_display_name(rights):
    shaped = services.shape(_hits({"accessRights": "reserved"}), {}, view="list")

    assert shaped["resources"][0]["accessRights"] == "Reserved collection"


def test_a_result_carrying_an_access_right_the_instance_no_longer_defines_is_dropped(rights):
    """We cannot say who it is for, so we do not show it."""
    shaped = services.shape(_hits({"accessRights": "removed-long-ago"}), {}, view="list")

    assert shaped["resources"] == []


def test_a_blog_article_is_excerpted_unless_the_caller_asked_for_all_of_it(rights):
    long_article = "x" * 1000

    short = services.shape(_hits({"article": long_article}), {}, view="blog")
    full = services.shape(_hits({"article": long_article}), {"full_article": True}, view="blog")

    assert short["resources"][0]["article"].endswith("...")
    assert len(full["resources"][0]["article"]) == 1000


def test_an_empty_response_is_a_zero_total(rights):
    assert services.shape({}, {}, view="list") == {"total": 0, "resources": []}


# ---------------------------------------------------------------------------
# Image previews on gallery and blog cards
# ---------------------------------------------------------------------------


def _image(record_id, visible=True, kind="image"):
    return {
        "_id": record_id,
        "visible": visible,
        "processing": {"fileProcessing": {"type": kind, "path": f"2026/09/{record_id}"}},
    }


@pytest.fixture
def previews(monkeypatch, tmp_path):
    """Stored records, their derivatives on disk, and the record access rules."""
    import types

    from archihub.api.records import access
    from archihub.core import settings as settings_module

    state = types.SimpleNamespace(records={}, batches=[])

    def load(ids):
        state.batches.append(list(ids))
        return {i: state.records[i] for i in ids if i in state.records}

    def add(*records):
        for record in records:
            state.records[record["_id"]] = record
            for suffix in services.PREVIEW_SUFFIXES.values():
                path = tmp_path / f"{record['processing']['fileProcessing']['path']}{suffix}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{record['_id']}{suffix}".encode())

    state.add = add
    monkeypatch.setattr(services, "_load_records", load)
    monkeypatch.setattr(settings_module, "get_settings", lambda: types.SimpleNamespace(web_files_path=str(tmp_path)))
    monkeypatch.setattr(access, "may_view_record", lambda user, record, is_admin: record["visible"])
    monkeypatch.setattr(access, "is_public", lambda record: record["visible"])
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda user, role: False)
    return state


def _data_uri(record_id, suffix):
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(f"{record_id}{suffix}".encode()).decode()


def _entries(*ids, kind="image", tag="Archivos asociados"):
    return [{"id": i, "type": kind, "tag": tag} for i in ids]


def test_a_gallery_card_gets_its_first_three_visible_images_as_data(previews):
    previews.add(_image("a"), _image("b", visible=False), _image("c"), _image("d"), _image("e"))
    resource = {"records": [*_entries("a", "b"), *_entries("doc", kind="document"), *_entries("c", "d", "e")]}

    services.attach_previews([resource], "gallery", user="someone", public=False)

    assert [r["id"] for r in resource["records"]] == ["a", "c", "d"]
    assert resource["records"][0]["file"] == _data_uri("a", "_small.jpg")
    assert resource["files"] == 5


def test_a_record_the_caller_may_not_see_is_not_disclosed(previews):
    previews.add(_image("hidden", visible=False))
    resource = {"records": _entries("hidden")}

    services.attach_previews([resource], "gallery", user="someone", public=False)

    assert resource["records"] == []


def test_images_hidden_from_the_caller_are_topped_up_from_the_ones_after_them(previews):
    previews.add(*(_image(i, visible=False) for i in "abc"), _image("d"), _image("e"))
    resource = {"records": _entries("a", "b", "c", "d", "e")}

    services.attach_previews([resource], "gallery", user="someone", public=False)

    assert [r["id"] for r in resource["records"]] == ["d", "e"]
    assert previews.batches == [["a", "b", "c"], ["d", "e"]]


def test_one_read_serves_every_card_on_the_page(previews):
    previews.add(*(_image(i) for i in "abcdef"))
    resources = [{"records": _entries("a", "b", "c")}, {"records": _entries("d", "e", "f")}]

    services.attach_previews(resources, "gallery", user="someone", public=False)

    assert len(previews.batches) == 1


def test_an_anonymous_caller_is_judged_by_the_public_rule(previews, monkeypatch):
    from archihub.api.records import access

    def refuse(*args):
        raise AssertionError("the authenticated rule was applied to an anonymous caller")

    monkeypatch.setattr(access, "may_view_record", refuse)
    previews.add(_image("a"), _image("b", visible=False))
    resource = {"records": _entries("a", "b")}

    services.attach_previews([resource], "gallery", user=None, public=True)

    assert [r["id"] for r in resource["records"]] == ["a"]


def test_a_stored_path_outside_the_web_root_yields_no_image(previews):
    record = _image("a")
    previews.add(record)
    record["processing"]["fileProcessing"]["path"] = "../../../etc/passwd"
    resource = {"records": _entries("a")}

    services.attach_previews([resource], "gallery", user="someone", public=False)

    assert resource["records"] == []


def test_a_blog_card_gets_its_thumbnail_at_medium_size(previews):
    previews.add(_image("cover"), _image("other"))
    resource = {"records": [*_entries("other"), *_entries("cover", tag="thumbnail")]}

    services.attach_previews([resource], "blog", user="someone", public=False)

    assert resource["thumbnail"] == _data_uri("cover", "_medium.jpg")
    assert len(resource["records"]) == 2


def test_a_blog_card_without_a_tagged_thumbnail_uses_its_first_visible_image(previews):
    """No form in a default install declares a `thumbnail` file tag, so without
    this fallback no blog card would ever have an image."""
    previews.add(_image("hidden", visible=False), _image("first"), _image("second"))
    resource = {"records": [*_entries("doc", kind="document"), *_entries("hidden", "first", "second")]}

    services.attach_previews([resource], "blog", user=None, public=True)

    assert resource["thumbnail"] == _data_uri("first", "_medium.jpg")


def test_a_blog_card_with_no_image_has_no_thumbnail(previews):
    resource = {"records": _entries("doc", kind="document")}

    services.attach_previews([resource], "blog", user=None, public=True)

    assert "thumbnail" not in resource


def test_the_list_view_reads_no_images(previews):
    resource = {"records": _entries("a")}

    services.attach_previews([resource], "list", user="someone", public=False)

    assert previews.batches == []


# ---------------------------------------------------------------------------
# Content-type visibility
# ---------------------------------------------------------------------------


@pytest.fixture
def type_roles(monkeypatch):
    def roles(slug):
        return {"viewRoles": ["curator"]} if slug == "restricted" else {"viewRoles": []}

    monkeypatch.setattr("archihub.api.resources.hierarchy.type_roles", roles)


def test_an_open_type_is_searchable_by_anyone(type_roles):
    assert services.visible_types(["open"], None) == (["open"], None)


def test_a_restricted_type_is_refused_to_an_anonymous_caller(type_roles):
    types, error = services.visible_types(["restricted"], None)

    assert types == []
    assert error


def test_a_restricted_type_is_refused_without_the_role(type_roles, monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)

    types, error = services.visible_types(["restricted"], "alice")

    assert error


def test_the_role_admits_a_restricted_type(type_roles, monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r == "curator")

    assert services.visible_types(["restricted"], "alice") == (["restricted"], None)


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------


def test_a_feed_escapes_what_it_interpolates():
    feed = rss.build(
        {"resources": [{"id": "1", "metadata": {"firstLevel": {"title": "Tom & Jerry <script>"}}}]},
        base_url="https://example.org", link_template=None, title="A & B", description=None,
    )

    assert "<script>" not in feed
    assert "&amp;" in feed


def test_an_article_cannot_close_the_cdata_section_early():
    """Otherwise everything after `]]>` is parsed as feed markup.

    Asserted by parsing: the injected element must arrive as *text* inside the
    article, not as a child element of the item. Neutralising the terminator is
    what keeps it there.
    """
    from xml.etree import ElementTree

    feed = rss.build(
        {"resources": [{"id": "1", "article": "before ]]> <injected/> after"}]},
        base_url="https://example.org", link_template=None, title=None, description=None,
    )

    root = ElementTree.fromstring(feed)
    item = root.find("./channel/item")
    encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")

    assert item.find("injected") is None
    assert "<injected/>" in encoded.text


def test_a_broken_link_template_falls_back_rather_than_failing_the_feed():
    feed = rss.build(
        {"resources": [{"id": "abc"}]},
        base_url="https://example.org", link_template="/x/{nonexistent}",
        title=None, description=None,
    )

    assert "https://example.org/resource/abc" in feed


def test_an_unparseable_date_is_omitted_rather_than_raising():
    feed = rss.build(
        {"resources": [{"id": "1", "createdAt": "not a date"}]},
        base_url="https://example.org", link_template=None, title=None, description=None,
    )

    assert "<pubDate>" not in feed


def test_an_empty_result_still_produces_a_valid_feed():
    from xml.etree import ElementTree

    feed = rss.build(
        {"resources": []},
        base_url="https://example.org", link_template=None, title="Empty", description=None,
    )

    ElementTree.fromstring(feed)


def test_a_populated_feed_parses_as_xml():
    from xml.etree import ElementTree

    feed = rss.build(
        {
            "resources": [
                {"id": "1", "article": "Body & more", "createdAt": "2025-01-01T00:00:00Z",
                 "metadata": {"firstLevel": {"title": "A title"}}}
            ]
        },
        base_url="https://example.org", link_template=None, title=None, description=None,
    )

    root = ElementTree.fromstring(feed)
    assert root.find("./channel/item/title").text == "A title"


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_search_reports_503_when_indexing_is_off(monkeypatch):
    """Registered always, available per request, so turning indexing on needs
    no restart."""
    monkeypatch.setattr(services, "indexing_enabled", lambda: False)

    payload, status = services.search({"post_type": ["x"]}, None, public=True)

    assert status == 503


def test_the_cluster_error_text_is_not_returned_to_the_caller(monkeypatch):
    """It names indices and field mappings."""
    from archihub.infra.search import SearchUnavailable

    monkeypatch.setattr(services, "indexing_enabled", lambda: True)
    monkeypatch.setattr(services, "visible_types", lambda types, user: (types, None))
    monkeypatch.setattr(services, "_declared_fields", lambda types: (set(), set()))

    class Client:
        def resolve_index(self, suffix):
            return "idx"

        def search(self, index, body):
            raise SearchUnavailable("failed to parse field [metadata.secret] in index [archihub-prod]")

    monkeypatch.setattr(services, "_client", lambda: Client())

    payload, status = services.search({"post_type": ["x"]}, None, public=True)

    assert status == 502
    assert "archihub-prod" not in payload["msg"]
