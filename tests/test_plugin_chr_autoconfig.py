"""CHRAutoConfig — filing a case under the place it happened.

The two things worth testing here are the two that are invisible when they go
wrong. The folder chain has to come out in the order the resource domain reads
ancestry in, or every breadcrumb in the archive points the wrong way. And the
run has to stop re-entering itself: it is registered on `resource_update` and
ends by updating a resource, so without a terminating condition each pass
queues the next one for as long as the workers keep up.
"""

from __future__ import annotations

import pytest

plugin = pytest.importorskip(
    "archihub.plugins.CHRAutoConfig",
    reason="CHRAutoConfig is not installed in this checkout",
)

ROOT = {"id": "68772d8e900618851958218e", "post_type": "fondo"}
FOLDER_TYPE = "location"


# ---------------------------------------------------------------------------
# Which point is looked up
# ---------------------------------------------------------------------------


def test_the_first_entry_with_coordinates_is_the_one_used():
    location = [
        {"name": "sin coordenadas"},
        {"name": "primera", "coordinates": [-74.0, 4.6]},
        {"name": "segunda", "coordinates": [-75.5, 6.2]},
    ]
    assert plugin.first_coordinates(location) == [-74.0, 4.6]


@pytest.mark.parametrize("location", [None, [], "text", [{}], [{"coordinates": []}]])
def test_a_location_field_with_nothing_usable_yields_no_point(location):
    assert plugin.first_coordinates(location) is None


# ---------------------------------------------------------------------------
# The folder chain
# ---------------------------------------------------------------------------


class FakeMongo:
    """Existing folders keyed by (name, ident); inserts get a generated id."""

    def __init__(self, existing=None):
        self.existing = dict(existing or {})
        self.inserted = []

    def get_record(self, collection, filters, fields=None):
        key = (filters.get("metadata.firstLevel.title"), filters.get("ident"))
        found = self.existing.get(key)
        return {"_id": found} if found else None

    def insert_record(self, collection, record):
        new_id = f"new{len(self.inserted) + 1}"
        self.inserted.append(record)
        # Recorded as existing, because a real database would: the second pass
        # over the same case has to FIND these folders rather than make more.
        self.existing[(record["metadata"]["firstLevel"]["title"], record["ident"])] = new_id

        class Result:
            inserted_id = new_id

        return Result()


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(plugin, "_mongo", lambda: fake)
    return fake


def _polygon(name, ident, level):
    return {"properties": {"name": name, "ident": ident, "admin_level": level}}


POLYGONS = [_polygon("Antioquia", "05", 1), _polygon("Medellín", "05001", 2)]


def test_a_chain_is_created_root_first_one_folder_per_level(mongo):
    chain, _locations = plugin.resolve_folder_chain(POLYGONS, ROOT, FOLDER_TYPE)

    assert chain[0] == ROOT
    assert [entry["id"] for entry in chain] == [ROOT["id"], "new1", "new2"]
    assert [record["metadata"]["firstLevel"]["title"] for record in mongo.inserted] == [
        "Antioquia",
        "Medellín",
    ]


def test_each_new_folder_is_filed_under_the_one_above_it(mongo):
    plugin.resolve_folder_chain(POLYGONS, ROOT, FOLDER_TYPE)

    antioquia, medellin = mongo.inserted
    assert antioquia["parent"] == [ROOT]
    assert medellin["parent"] == [{"id": "new1", "post_type": FOLDER_TYPE}]


def test_a_folders_ancestry_is_stored_nearest_first(mongo):
    """`resources/hierarchy.py`'s `ancestors()` returns nearest-first, and the
    listing and search index filter on this field."""
    plugin.resolve_folder_chain(POLYGONS, ROOT, FOLDER_TYPE)

    _antioquia, medellin = mongo.inserted
    assert [entry["id"] for entry in medellin["parents"]] == ["new1", ROOT["id"]]


def test_a_folder_that_already_exists_is_reused_rather_than_duplicated(mongo):
    mongo.existing = {("Antioquia", "05"): "existing1"}

    chain, _locations = plugin.resolve_folder_chain(POLYGONS, ROOT, FOLDER_TYPE)

    assert [entry["id"] for entry in chain] == [ROOT["id"], "existing1", "new1"]
    assert len(mongo.inserted) == 1


def test_each_folder_records_only_the_levels_resolved_when_it_was_created(mongo):
    """The structure is filled in as the walk descends, so a shared reference
    would leave every folder showing the levels found after it."""
    plugin.resolve_folder_chain(POLYGONS, ROOT, FOLDER_TYPE)

    antioquia, medellin = mongo.inserted
    assert antioquia["metadata"]["firstLevel"]["loc"][0]["level_2"] is None
    assert medellin["metadata"]["firstLevel"]["loc"][0]["level_2"] == {
        "ident": "05001",
        "name": "Medellín",
    }


def test_a_boundary_with_no_name_is_skipped_rather_than_filed_as_a_blank_folder(mongo):
    chain, _locations = plugin.resolve_folder_chain(
        [{"properties": {"admin_level": 1}}, {}, _polygon("Antioquia", "05", 1)],
        ROOT,
        FOLDER_TYPE,
    )

    assert len(chain) == 2
    assert len(mongo.inserted) == 1


def test_a_non_numeric_admin_level_does_not_stop_the_walk(mongo):
    polygons = [{"properties": {"name": "Rara", "ident": "x", "admin_level": "alto"}}]

    _chain, locations = plugin.resolve_folder_chain(polygons, ROOT, FOLDER_TYPE)

    assert locations[0]["level_0"] == {"ident": "x", "name": "Rara"}


# ---------------------------------------------------------------------------
# The run stops re-entering itself
# ---------------------------------------------------------------------------


def test_a_resource_already_filed_under_the_computed_parent_is_recognised():
    parent = {"id": "new2", "post_type": FOLDER_TYPE}
    assert plugin._already_filed({"parent": [parent]}, parent) is True


def test_the_dict_spelling_of_parent_is_recognised_too():
    """Both spellings occur in real data - `hierarchy.direct_parents` normalises
    them, and a resource this plugin has not yet touched may carry either."""
    parent = {"id": "new2", "post_type": FOLDER_TYPE}
    assert plugin._already_filed({"parent": parent}, parent) is True


@pytest.mark.parametrize("current", [None, [], "text", [{"id": "other"}]])
def test_a_resource_filed_somewhere_else_still_needs_the_write(current):
    assert plugin._already_filed({"parent": current}, {"id": "new2"}) is False


def test_a_resource_with_several_parents_is_not_treated_as_already_filed():
    """It is filed under the computed folder AND something else, which is not
    the state this run produces."""
    parent = {"id": "new2", "post_type": FOLDER_TYPE}
    assert plugin._already_filed({"parent": [parent, {"id": "other"}]}, parent) is False


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


@pytest.fixture
def task_environment(monkeypatch, mongo):
    """A configured plugin whose writes are recorded rather than performed."""
    writes = []
    monkeypatch.setattr(
        plugin,
        "_stored_settings",
        lambda: {
            plugin.SETTING_ROOT: ROOT["id"],
            plugin.SETTING_ROOT_TYPE: "fondo",
            plugin.SETTING_FOLDER_TYPE: FOLDER_TYPE,
        },
    )
    monkeypatch.setattr(plugin, "containing_polygons", lambda coordinates: list(POLYGONS))
    monkeypatch.setattr(
        plugin.plugin_data,
        "update_resource",
        lambda resource_id, update: (writes.append((resource_id, update)), ({"msg": "ok"}, 200))[1],
    )
    return writes


def _case(**overrides):
    body = {
        "_id": "6a88b6e74bb8b789b188db90",
        "post_type": "caso",
        "metadata": {"firstLevel": {"loc_docu": [{"coordinates": [-75.5, 6.2]}]}},
    }
    body.update(overrides)
    return body


TYPE_CONFIG = {"type": "caso", "order": 0}


def test_a_case_is_refiled_under_the_deepest_place_folder(task_environment):
    plugin.automatic(TYPE_CONFIG, _case())

    (resource_id, update), = task_environment
    assert resource_id == "6a88b6e74bb8b789b188db90"
    assert update["parent"] == [{"id": "new2", "post_type": FOLDER_TYPE}]
    assert [entry["id"] for entry in update["parents"]] == ["new2", "new1", ROOT["id"]]


def test_the_case_keeps_its_own_identity_fields(task_environment):
    plugin.automatic(TYPE_CONFIG, _case(ident="CHR1", status="published"))

    (_resource_id, update), = task_environment
    assert update["ident"] == "CHR1"
    assert update["post_type"] == "caso"
    # `_id` is the filter, not a field to write over.
    assert "_id" not in update


def test_a_second_pass_over_an_already_filed_case_writes_nothing(task_environment):
    """This is what terminates the loop: the write fires `resource_update`,
    which queues this task again with the payload it just wrote."""
    plugin.automatic(TYPE_CONFIG, _case())
    (_resource_id, update), = task_environment

    plugin.automatic(TYPE_CONFIG, {**update, "_id": "6a88b6e74bb8b789b188db90"})

    assert len(task_environment) == 1


def test_a_resource_of_another_content_type_is_ignored(task_environment):
    plugin.automatic(TYPE_CONFIG, _case(post_type="otro"))
    assert task_environment == []


def test_a_case_with_no_location_is_ignored(task_environment):
    plugin.automatic(TYPE_CONFIG, _case(metadata={"firstLevel": {}}))
    assert task_environment == []


def test_a_payload_with_no_id_is_ignored(task_environment):
    body = _case()
    del body["_id"]
    plugin.automatic(TYPE_CONFIG, body)
    assert task_environment == []


def test_an_unconfigured_root_stops_the_run_rather_than_inventing_one(monkeypatch, task_environment):
    monkeypatch.setattr(plugin, "_stored_settings", dict)
    plugin.automatic(TYPE_CONFIG, _case())
    assert task_environment == []


def test_a_location_outside_every_boundary_is_left_where_it_is(monkeypatch, task_environment):
    monkeypatch.setattr(plugin, "containing_polygons", lambda coordinates: [])
    plugin.automatic(TYPE_CONFIG, _case())
    assert task_environment == []


def test_a_failed_boundary_lookup_does_not_fail_the_task(monkeypatch, task_environment):
    def explode(coordinates):
        raise RuntimeError("no geo index")

    monkeypatch.setattr(plugin, "containing_polygons", explode)

    assert plugin.automatic(TYPE_CONFIG, _case()) == "ok"
    assert task_environment == []


def test_the_task_name_is_the_one_queued_messages_resolve_by():
    assert plugin.automatic.name == "CHRAutoConfig.auto"


# ---------------------------------------------------------------------------
# The routes, through the real routing stack
# ---------------------------------------------------------------------------


def test_the_settings_that_decide_where_cases_are_filed_are_not_anonymous():
    """This plugin declares no routes of its own - it works through the
    resource hooks - so its settings are the whole of its surface, and they
    decide which content types get refiled and where the hierarchy is rooted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from archihub.core.errors import register_exception_handlers
    from archihub.plugins.framework.mounting import build_plugin

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(build_plugin(plugin.SLUG).build())
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/CHRAutoConfig/settings/all").status_code == 401
    assert client.post("/CHRAutoConfig/settings", data={"data": "{}"}).status_code == 401
