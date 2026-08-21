"""Targeted edits: file order, granular metadata, change-post-type.

The reordering rule is pure and gets tested directly. The two service functions
around it are mostly authorisation, which is where the legacy versions each
invented their own partial rule.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.resources import editing

VALID_ID = "6a70b833497d4440325c94b1"
RECORD_ID = "6a70b833497d4440325c94c1"
PARENT_A = "6a70b833497d4440325c94d1"
PARENT_B = "6a70b833497d4440325c94d2"


class FakeMongo:
    def __init__(self):
        self.resources: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.type: dict | None = None
        self.user: dict | None = None
        self.ancestors: list[dict] = []
        self.writes: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        if collection == "post_types":
            return self.type
        if collection == "users":
            return self.user
        if collection == "records":
            return self.records.get(str(filters.get("_id")))
        return self.resources.get(str(filters.get("_id")))

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return list(self.ancestors)

    def update_record(self, collection, filters, update_model):
        self.writes.append((filters, update_model))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(editing, "_mongo", lambda: fake)
    monkeypatch.setattr(editing.access, "_mongo", lambda: fake)
    monkeypatch.setattr("archihub.api.resources.hierarchy._mongo", lambda: fake)
    monkeypatch.setattr(editing, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(editing, "_call_hook", lambda name, payload: payload)
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


def with_roles(monkeypatch, *roles):
    held = set(roles)
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r in held)


# ---------------------------------------------------------------------------
# The reordering rule
# ---------------------------------------------------------------------------


def files(*ids):
    return [{"id": i, "order": n} for n, i in enumerate(ids)]


def ids(result):
    return [f["id"] for f in result]


def test_an_empty_move_list_leaves_the_order_alone():
    assert ids(editing.reorder(files("a", "b", "c"), [])) == ["a", "b", "c"]


def test_a_file_moves_to_the_front():
    assert ids(editing.reorder(files("a", "b", "c"), [{"id": "c", "order": 0}])) == ["c", "a", "b"]


def test_a_file_moves_to_the_end():
    assert ids(editing.reorder(files("a", "b", "c"), [{"id": "a", "order": 2}])) == ["b", "c", "a"]


def test_orders_are_renumbered_densely():
    """Otherwise repeated edits leave gaps that later sorts read differently."""
    result = editing.reorder(files("a", "b", "c"), [{"id": "c", "order": 0}])
    assert [f["order"] for f in result] == [0, 1, 2]


def test_a_position_past_the_end_is_clamped():
    assert ids(editing.reorder(files("a", "b"), [{"id": "a", "order": 99}])) == ["b", "a"]


def test_a_negative_position_is_clamped():
    assert ids(editing.reorder(files("a", "b"), [{"id": "b", "order": -5}])) == ["b", "a"]


def test_two_files_move_at_once():
    result = editing.reorder(files("a", "b", "c", "d"), [{"id": "d", "order": 0}, {"id": "c", "order": 1}])
    assert ids(result) == ["d", "c", "a", "b"]


def test_a_move_naming_an_unknown_file_is_ignored():
    assert ids(editing.reorder(files("a", "b"), [{"id": "ghost", "order": 0}])) == ["a", "b"]


def test_a_move_without_an_order_is_ignored():
    assert ids(editing.reorder(files("a", "b"), [{"id": "b"}])) == ["a", "b"]


def test_a_boolean_order_is_ignored():
    """``True`` is an int in Python and would silently mean position 1."""
    assert ids(editing.reorder(files("a", "b", "c"), [{"id": "c", "order": True}])) == ["a", "b", "c"]


def test_a_malformed_entry_does_not_take_the_request_down():
    """The original indexed straight into these, so one bad entry raised
    KeyError and the raw key name became the error message."""
    assert ids(editing.reorder([{"id": "a"}, {"no": "id"}, None], [{"bad": True}, None])) == ["a"]


def test_files_without_an_order_sort_last():
    result = editing.reorder([{"id": "a"}, {"id": "b", "order": 0}], [])
    assert ids(result) == ["b", "a"]


# ---------------------------------------------------------------------------
# update_files_order
# ---------------------------------------------------------------------------


def resource(**overrides):
    return {
        "_id": ObjectId(VALID_ID),
        "post_type": "foto",
        "createdBy": "owner",
        "accessRights": None,
        "parents": [],
        "filesObj": files("a", "b"),
        **overrides,
    }


def test_reordering_writes_only_the_files_and_audit_fields(mongo):
    mongo.resources[VALID_ID] = resource()
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = editing.update_files_order(VALID_ID, {"files": [{"id": "b", "order": 0}]}, "alice")

    assert status == 200
    _filters, written = mongo.writes[0]
    assert set(written) == {"filesObj", "updatedAt", "updatedBy"}
    assert ids(written["filesObj"]) == ["b", "a"]


def test_reordering_a_missing_resource_is_404_not_500(mongo):
    """The original resolved access rights before its own existence check, and
    that raised."""
    _payload, status = editing.update_files_order(VALID_ID, {"files": []}, "alice")
    assert status == 404


def test_a_malformed_resource_id_is_404(mongo):
    _payload, status = editing.update_files_order("nope", {"files": []}, "alice")
    assert status == 404


def test_the_content_types_edit_roles_now_apply_to_reordering(mongo):
    """The original checked access rights only, so a type naming exactly who may
    edit it had that ignored on this route."""
    mongo.resources[VALID_ID] = resource()
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": ["curator"], "viewRoles": []}

    _payload, status = editing.update_files_order(VALID_ID, {"files": []}, "alice")

    assert status == editing.ROLE_FAILURE_STATUS
    assert mongo.writes == []


def test_reordering_does_not_require_being_the_creator(mongo):
    """Reordering someone else's files is ordinary editorial work."""
    mongo.resources[VALID_ID] = resource(createdBy="someone-else")
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = editing.update_files_order(VALID_ID, {"files": []}, "alice")
    assert status == 200


def test_an_unreadable_resource_cannot_be_reordered(mongo):
    mongo.resources[VALID_ID] = resource(accessRights="reserved")
    mongo.user = {"accessRights": ["public"]}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = editing.update_files_order(VALID_ID, {"files": []}, "alice")
    assert status == editing.ROLE_FAILURE_STATUS


def test_a_resource_with_no_files_is_not_an_error(mongo):
    mongo.resources[VALID_ID] = resource(filesObj=None)
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = editing.update_files_order(VALID_ID, {"files": []}, "alice")
    assert status == 200


# ---------------------------------------------------------------------------
# Granular metadata edit
# ---------------------------------------------------------------------------


@pytest.fixture
def granular(mongo, monkeypatch):
    mongo.records[RECORD_ID] = {"_id": ObjectId(RECORD_ID), "parent": [{"id": PARENT_A}]}
    mongo.resources[PARENT_A] = {
        "_id": ObjectId(PARENT_A),
        "post_type": "foto",
        "createdBy": "alice",
        "status": "draft",
        "accessRights": None,
        "parents": [],
        "metadata": {"firstLevel": {"title": "Antes"}},
    }
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: {
            "fields": [
                {"destiny": "metadata.firstLevel.title", "type": "text", "label": "Title"},
                {"destiny": "metadata.firstLevel.count", "type": "number", "label": "Count"},
            ]
        },
    )
    return mongo


def test_a_granular_edit_replaces_the_field(granular):
    payload, status = editing.update_granular(
        RECORD_ID, "metadata.firstLevel.title", "Después", "alice"
    )

    assert (status, payload["updated"]) == (200, 1)
    _filters, written = granular.writes[0]
    assert written["metadata"]["firstLevel"]["title"] == "Después"


def test_concat_appends_to_the_existing_text(granular):
    editing.update_granular(RECORD_ID, "metadata.firstLevel.title", "más", "alice", concat=True)

    _filters, written = granular.writes[0]
    assert written["metadata"]["firstLevel"]["title"] == "Antes más"


def test_concat_onto_an_empty_field_does_not_leave_a_leading_space(granular):
    granular.resources[PARENT_A]["metadata"] = {"firstLevel": {"title": ""}}
    editing.update_granular(RECORD_ID, "metadata.firstLevel.title", "solo", "alice", concat=True)

    _filters, written = granular.writes[0]
    assert written["metadata"]["firstLevel"]["title"] == "solo"


def test_only_declared_fields_may_be_written(granular):
    """The path comes from the request body, so the schema lookup is what keeps
    a caller from writing to arbitrary parts of the document."""
    _payload, status = editing.update_granular(
        RECORD_ID, "createdBy", "attacker", "alice"
    )

    assert status == 400
    assert granular.writes == []


def test_only_free_text_fields_may_be_written(granular):
    _payload, status = editing.update_granular(
        RECORD_ID, "metadata.firstLevel.count", "7", "alice"
    )
    assert status == 400


@pytest.mark.parametrize("path", [None, "", 5])
def test_an_unusable_path_is_rejected(granular, path):
    _payload, status = editing.update_granular(RECORD_ID, path, "x", "alice")
    assert status == 400


@pytest.mark.parametrize("value", [None, 5, [], {"a": 1}])
def test_a_non_string_value_is_rejected(granular, value):
    _payload, status = editing.update_granular(RECORD_ID, "metadata.firstLevel.title", value, "alice")
    assert status == 400


def test_a_missing_record_is_404(granular):
    _payload, status = editing.update_granular(
        "6a70b833497d4440325c94ff", "metadata.firstLevel.title", "x", "alice"
    )
    assert status == 404


def test_a_record_with_no_parents_is_404(granular):
    granular.records[RECORD_ID]["parent"] = []
    _payload, status = editing.update_granular(RECORD_ID, "metadata.firstLevel.title", "x", "alice")
    assert status == 404


def test_a_parent_stored_as_a_dict_is_handled(granular):
    granular.records[RECORD_ID]["parent"] = {"id": PARENT_A}
    _payload, status = editing.update_granular(RECORD_ID, "metadata.firstLevel.title", "x", "alice")
    assert status == 200


def test_partial_success_is_success(granular):
    """A file can hang off several resources and the caller may be entitled to
    edit only some of them."""
    granular.records[RECORD_ID]["parent"] = [{"id": PARENT_A}, {"id": PARENT_B}]
    granular.resources[PARENT_B] = {
        **granular.resources[PARENT_A],
        "_id": ObjectId(PARENT_B),
        "createdBy": "someone-else",
    }

    payload, status = editing.update_granular(
        RECORD_ID, "metadata.firstLevel.title", "x", "alice"
    )

    assert (status, payload["updated"]) == (200, 1)
    assert [r["id"] for r in payload["resources"]] == [PARENT_A]


def test_no_permitted_parent_is_a_400(granular):
    granular.resources[PARENT_A]["createdBy"] = "someone-else"
    _payload, status = editing.update_granular(RECORD_ID, "metadata.firstLevel.title", "x", "alice")

    assert status == 400
    assert granular.writes == []


def test_a_stranger_cannot_edit_someone_elses_resource(granular):
    granular.resources[PARENT_A]["createdBy"] = "owner"
    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == editing.ROLE_FAILURE_STATUS


def test_a_super_editor_may_edit_anyones_resource(granular, monkeypatch):
    with_roles(monkeypatch, "super_editor")
    granular.resources[PARENT_A]["createdBy"] = "owner"

    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == 200


def test_editing_a_published_resource_requires_the_publisher_role(granular):
    granular.resources[PARENT_A]["status"] = "published"

    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == editing.ROLE_FAILURE_STATUS


def test_a_publisher_may_edit_a_published_resource(granular, monkeypatch):
    with_roles(monkeypatch, "publisher")
    granular.resources[PARENT_A]["status"] = "published"

    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == 200


def test_an_unreadable_resource_cannot_be_edited(granular):
    """The original omitted access rights on this path, as on every write path."""
    granular.resources[PARENT_A]["accessRights"] = "reserved"
    granular.user = {"accessRights": ["public"]}

    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == editing.ROLE_FAILURE_STATUS


def test_a_resource_predating_created_by_does_not_500(granular):
    """Documents without the field exist; the original subscripted it."""
    del granular.resources[PARENT_A]["createdBy"]

    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == editing.ROLE_FAILURE_STATUS


def test_a_hook_returning_a_non_string_is_a_400_not_a_500(granular, monkeypatch):
    """A `resource_pre_update` hook can rewrite the body; the original let the
    text validator raise, and the outer handler turned that into a 500."""
    monkeypatch.setattr(
        editing,
        "_call_hook",
        lambda name, payload: {**payload, "metadata": {"firstLevel": {"title": 42}}},
    )

    _payload, status = editing.update_resource_granular(
        PARENT_A, "metadata.firstLevel.title", "x", "alice"
    )
    assert status == 400


# ---------------------------------------------------------------------------
# change-post-type
# ---------------------------------------------------------------------------


@pytest.fixture
def reclassify(mongo, monkeypatch):
    """A resource with no hierarchy constraints and a permissive target form."""
    mongo.resources[VALID_ID] = resource()
    mongo.resources[VALID_ID]["post_type"] = "serie"
    mongo.type = {"editRoles": [], "viewRoles": [], "_id": "t"}
    monkeypatch.setattr("archihub.api.types.services.get_metadata", lambda slug: {"fields": []})
    monkeypatch.setattr("archihub.api.resources.hierarchy.direct_children", lambda rid: [])
    monkeypatch.setattr("archihub.api.resources.hierarchy.parent_type_allowed", lambda c, p: True)
    return mongo


def test_a_resource_is_reclassified_and_written(reclassify):
    """Reporting success while writing nothing is the failure this guards."""
    payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == 200
    _filters, update = reclassify.writes[0]
    assert update["post_type"] == "fondo"
    assert update["updatedBy"] == "alice"


def test_reclassifying_to_the_same_type_writes_nothing(reclassify):
    payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "serie"}, "alice")

    assert status == 200
    assert reclassify.writes == []


def test_a_missing_target_type_is_refused(reclassify):
    _payload, status = editing.change_post_type({"id": VALID_ID}, "alice")

    assert status == 400
    assert reclassify.writes == []


def test_a_target_type_that_does_not_exist_is_404(reclassify, monkeypatch):
    reclassify.type = None

    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "ghost"}, "alice")

    assert status == 404
    assert reclassify.writes == []


def test_the_edit_role_is_required_on_the_target_type_too(reclassify, monkeypatch):
    """Reclassifying moves a resource into another editorial domain.

    Checking only the current type would let someone file a resource into a
    type they have no business filling.
    """
    calls = []

    def holds(user, post_type, is_admin):
        calls.append(post_type)
        return post_type != "fondo"

    monkeypatch.setattr(editing.access, "holds_edit_role", holds)

    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == editing.ROLE_FAILURE_STATUS
    assert "fondo" in calls
    assert reclassify.writes == []


def test_a_caller_who_cannot_see_the_resource_may_not_reclassify_it(reclassify, monkeypatch):
    monkeypatch.setattr(editing.access, "may_view_resource", lambda u, r, a: False)

    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == editing.ROLE_FAILURE_STATUS
    assert reclassify.writes == []


def test_a_parent_that_would_not_accept_the_new_type_refuses_the_change(reclassify, monkeypatch):
    """The resource is not moving, so its existing parents must still accept it."""
    reclassify.resources[VALID_ID]["parent"] = [{"id": PARENT_A}]
    monkeypatch.setattr("archihub.api.resources.hierarchy._post_type_of", lambda rid: "caja")
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.parent_type_allowed", lambda c, p: False
    )

    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == 400
    assert reclassify.writes == []


def test_a_child_that_would_not_accept_the_new_type_refuses_the_change(reclassify, monkeypatch):
    """Both directions matter: children must still accept it as their parent."""
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.direct_children",
        lambda rid: [{"id": PARENT_B, "post_type": "item"}],
    )
    monkeypatch.setattr(
        "archihub.api.resources.hierarchy.parent_type_allowed", lambda c, p: c != "item"
    )

    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == 400
    assert reclassify.writes == []


def test_a_published_resource_missing_a_required_field_is_refused(reclassify, monkeypatch):
    """Reclassification must not leave a published record invalid."""
    reclassify.resources[VALID_ID]["status"] = "published"
    reclassify.resources[VALID_ID]["metadata"] = {"firstLevel": {"title": "A series"}}
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: {
            "fields": [
                {"destiny": "firstLevel.title", "type": "text", "required": True},
                {"destiny": "firstLevel.scope", "type": "text", "required": True},
            ]
        },
    )

    payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == 400
    assert "errors" in payload
    assert reclassify.writes == []


def test_a_draft_missing_a_required_field_is_allowed(reclassify, monkeypatch):
    """Incompleteness is what a draft is for - same rule as validate_fields."""
    reclassify.resources[VALID_ID]["status"] = "draft"
    reclassify.resources[VALID_ID]["metadata"] = {"firstLevel": {"title": "A series"}}
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: {
            "fields": [
                {"destiny": "firstLevel.title", "type": "text", "required": True},
                {"destiny": "firstLevel.scope", "type": "text", "required": True},
            ]
        },
    )

    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == 200


def test_fields_the_new_form_does_not_declare_are_kept_and_reported(reclassify, monkeypatch):
    """An archive must not lose description because someone reclassified.

    The values stay; the caller is told which ones the new form will not render.
    """
    reclassify.resources[VALID_ID]["metadata"] = {
        "firstLevel": {"title": "A series", "legacyNote": "keep me"}
    }
    monkeypatch.setattr(
        "archihub.api.types.services.get_metadata",
        lambda slug: {"fields": [{"destiny": "firstLevel.title", "type": "text"}]},
    )

    payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "fondo"}, "alice")

    assert status == 200
    assert payload["undeclaredFields"] == ["legacyNote", "title"]
    _filters, update = reclassify.writes[0]
    assert "metadata" not in update


def test_change_post_type_on_a_missing_resource_is_404(mongo):
    """The original raised a KeyError for a missing id and 500'd for a
    nonexistent resource; both are documented in its own Swagger."""
    _payload, status = editing.change_post_type({"id": VALID_ID, "post_type": "x"}, "alice")
    assert status == 404


def test_change_post_type_without_an_id_is_400(mongo):
    _payload, status = editing.change_post_type({}, "alice")
    assert status == 400
