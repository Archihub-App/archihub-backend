"""Content-type domain.

First domain ported in Phase 3, chosen to prove the router/schema/service
pattern on low-risk surface.
"""

from __future__ import annotations

import pytest

from archihub.api.types import services


class FakeMongo:
    def __init__(self, records=None):
        self.records = records or {}
        self.collections: dict[str, list] = {}
        self.inserted: list = []
        self.updated: list = []
        self.deleted: list = []
        self.updated_many: list = []

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        if callable(source):
            return source(filters)
        return source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        rows = self.collections.get(collection, [])
        if filters and "slug" in filters and "$in" in filters["slug"]:
            wanted = filters["slug"]["$in"]
            rows = [r for r in rows if r.get("slug") in wanted]
        elif filters and "parentType.id" in filters:
            wanted = filters["parentType.id"]
            rows = [r for r in rows if any(p.get("id") == wanted for p in r.get("parentType", []))]
        return list(rows)

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))

    def update_records(self, collection, filters, update):
        self.updated_many.append((collection, filters, update))

    def delete_record(self, collection, filters):
        self.deleted.append((collection, filters))

    def count(self, collection, filters=None):
        return 0

    def increment_record(self, *args, **kwargs):
        pass

    def aggregate(self, collection, pipeline):
        self.aggregated = (collection, pipeline)
        return iter(self.aggregate_result)

    aggregate_result: list = []


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    monkeypatch.setattr(services, "_call_hook", lambda *a, **k: None)
    return fake


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Simple Name", "simple-name"),
        ("  Padded  ", "padded"),
        # Accented letters are alphanumeric in Python, so they survive; only
        # punctuation is dropped.
        ("Wéird! Ch@rs", "wéird-chrs"),
        ("--leading-and-trailing--", "leading-and-trailing"),
    ],
)
def test_slugify(name, expected):
    assert services.slugify(name) == expected


def test_slugify_does_not_fully_collapse_runs_of_hyphens():
    """A legacy quirk, preserved on purpose.

    `replace('--', '-')` runs once and replaces non-overlapping matches, so three
    consecutive spaces leave two hyphens rather than one. It looks like a bug and
    is tempting to "fix" - but slugs are URLs. Changing the derivation would
    generate a different slug for the same name, so newly created types would
    stop matching links, bookmarks and any stored reference from before the
    change. Preserved exactly; only worth revisiting alongside a redirect story.
    """
    assert services.slugify("Multiple   Spaces") == "multiple--spaces"


def test_unique_slug_suffixes_until_free(mongo):
    taken = {"report", "report-1"}
    mongo.records["post_types"] = lambda filters: {"slug": filters["slug"]} if filters["slug"] in taken else None

    assert services.make_unique_slug("Report") == "report-2"


# ---------------------------------------------------------------------------
# get_parents - the crash fix
# ---------------------------------------------------------------------------


def test_parents_of_a_deleted_type_returns_empty_not_a_crash(mongo):
    """Regression guard for a reachable 500.

    The legacy guard read `if not parent and not parent['hierarchical']` where
    `parent` is a list. When the list came back empty the first operand was
    true, so Python evaluated the second and raised TypeError. Reaching it takes
    nothing exotic: delete a parent type, then open one of its children - the
    declared parent id no longer resolves and the list is empty.
    """
    mongo.collections["post_types"] = []  # the declared parent no longer exists

    child = {"slug": "child", "parentType": [{"id": "deleted-parent"}]}
    assert services.get_parents(child) == []


def test_parents_are_resolved_and_marked_direct(mongo):
    mongo.collections["post_types"] = [
        {"slug": "parent", "name": "Parent", "icon": "p", "parentType": []},
    ]
    child = {"slug": "child", "parentType": [{"id": "parent"}]}

    parents = services.get_parents(child)

    assert len(parents) == 1
    assert parents[0]["slug"] == "parent"
    assert parents[0]["direct"] is True


def test_grandparents_are_marked_indirect(mongo):
    mongo.collections["post_types"] = [
        {"slug": "parent", "name": "P", "icon": "p", "parentType": [{"id": "grandparent"}]},
        {"slug": "grandparent", "name": "G", "icon": "g", "parentType": []},
    ]
    child = {"slug": "child", "parentType": [{"id": "parent"}]}

    parents = services.get_parents(child)
    by_slug = {p["slug"]: p for p in parents}

    assert by_slug["parent"]["direct"] is True
    assert by_slug["grandparent"]["direct"] is False


def test_a_type_that_parents_itself_does_not_recurse_forever(mongo):
    """Hierarchical types list themselves as a parent."""
    mongo.collections["post_types"] = [
        {"slug": "folder", "name": "Folder", "icon": "f", "parentType": [{"id": "folder"}]},
    ]
    folder = {"slug": "folder", "parentType": [{"id": "folder"}]}

    assert services.get_parents(folder) == []


def test_a_parent_cycle_terminates(mongo):
    """Two types listing each other must not loop."""
    mongo.collections["post_types"] = [
        {"slug": "a", "name": "A", "icon": "", "parentType": [{"id": "b"}]},
        {"slug": "b", "name": "B", "icon": "", "parentType": [{"id": "a"}]},
    ]

    parents = services.get_parents({"slug": "a", "parentType": [{"id": "b"}]})
    assert {p["slug"] for p in parents} <= {"a", "b"}


def test_get_parents_uses_immutable_defaults():
    """Mutable default arguments are a classic source of cross-call bleed."""
    import inspect

    signature = inspect.signature(services.get_parents)
    for name in ("fields", "seen"):
        default = signature.parameters[name].default
        assert isinstance(default, (tuple, frozenset)), f"{name} default is mutable"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_get_all_projects_only_three_fields(mongo):
    mongo.collections["post_types"] = [
        {"slug": "a", "name": "A", "description": "d", "icon": "x", "secret": "s"},
    ]
    payload, status = services.get_all()

    assert status == 200
    assert payload == [{"name": "A", "description": "d", "slug": "a"}]


def test_create_requires_name_and_slug(mongo):
    payload, status = services.create({"name": "", "slug": ""}, "admin")
    assert status == 400


def test_create_answers_201(mongo):
    """The status the route DECLARES comes from here: the decorator's
    ``status_code`` is only a default, and a service tuple overrides it. If this
    drifts to 200 the published spec keeps promising 201 and no request fails.
    """
    _payload, status = services.create(
        {"name": "Report", "description": "d", "slug": "report-201"}, "admin"
    )
    assert status == 201


def test_create_does_not_write_an_id(mongo):
    """The legacy model declared a UUID `_id` default that only stayed harmless
    because insert used exclude_unset. Nothing here should emit one - MongoDB
    assigns the ObjectId."""
    services.create({"name": "Report", "description": "d", "slug": "report"}, "admin")

    _collection, record = mongo.inserted[0]
    assert "_id" not in record
    assert "id" not in record


def test_delete_soft_deletes_the_resources_of_that_type(mongo):
    mongo.records["post_types"] = {"slug": "report", "name": "Report"}

    payload, status = services.delete_by_slug("report", "admin")

    assert status == 200
    assert mongo.deleted == [("post_types", {"slug": "report"})]

    collection, filters, update = mongo.updated_many[0]
    assert collection == "resources"
    assert filters == {"post_type": "report"}
    assert update["status"] == "deleted"


def test_delete_missing_type_is_404(mongo):
    mongo.records["post_types"] = None
    payload, status = services.delete_by_slug("nope", "admin")
    assert status == 404


def test_update_strips_self_from_parent_types(mongo):
    """A type must not become its own parent - the parent walk would loop."""
    mongo.records["post_types"] = {"slug": "folder"}

    services.update_by_slug(
        "folder", {"parentType": [{"id": "folder"}, {"id": "other"}]}, "admin"
    )

    _collection, _filters, update = mongo.updated[0]
    assert update["parentType"] == [{"id": "other"}]


def test_update_without_parent_types_does_not_raise(mongo):
    """A partial update omitting parentType is legitimate.

    The legacy version indexed `body['parentType']` unconditionally and raised
    KeyError on any patch that did not include it.
    """
    mongo.records["post_types"] = {"slug": "folder"}

    payload, status = services.update_by_slug("folder", {"name": "Renamed"}, "admin")
    assert status == 200


def test_update_missing_type_is_404(mongo):
    mongo.records["post_types"] = None
    payload, status = services.update_by_slug("nope", {"name": "x"}, "admin")
    assert status == 404


# ---------------------------------------------------------------------------
# Info-panel statistics
#
# `type` arrives in a request body and selects an aggregation. It indexes a fixed
# table rather than describing a pipeline, because the alternative - assembling
# stages from what the request contained - is the shortest path from a form field
# to running arbitrary Mongo.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("viz_type", ["timeCreated", "statusCount", "authorCount"])
def test_each_known_chart_aggregates_over_that_content_type(mongo, viz_type):
    mongo.aggregate_result = [{"_id": "x", "count": 3}]

    payload, status = services.get_type_viz("casos", viz_type)

    assert status == 200
    assert payload == [{"_id": "x", "count": 3}]

    collection, pipeline = mongo.aggregated
    assert collection == "resources"
    # Always scoped to the requested type, and scoped FIRST.
    assert pipeline[0] == {"$match": {"post_type": "casos"}}


def test_an_unknown_chart_is_answered_ok_not_an_error(mongo):
    """The panel asks for several charts by name; one it does not recognise must
    not fail the screen. Legacy fell through to this same answer."""
    payload, status = services.get_type_viz("casos", "somethingElse")

    assert status == 200
    assert payload == {"msg": "ok"}


def test_an_unknown_chart_never_reaches_the_database(mongo):
    mongo.aggregated = None

    services.get_type_viz("casos", "$where")

    assert mongo.aggregated is None


def test_the_requested_type_cannot_inject_pipeline_stages(mongo):
    """The only values that reach Mongo come from a table the application owns."""
    mongo.aggregate_result = []

    for hostile in ('{"$out": "resources"}', "../admin", "timeCreated;drop"):
        mongo.aggregated = None
        _payload, status = services.get_type_viz("casos", hostile)
        assert status == 200
        assert mongo.aggregated is None


def test_a_failing_aggregation_is_reported_not_returned_as_data(mongo, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("no mongo")

    monkeypatch.setattr(mongo, "aggregate", explode)

    payload, status = services.get_type_viz("casos", "statusCount")

    assert status == 500
    assert "msg" in payload
