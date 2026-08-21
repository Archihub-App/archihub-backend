"""Metadata validation for the resource write path.

ArchiHUB's content model is runtime-defined - an administrator builds a form and
a content type points at it - so this is the code that decides what a resource
is allowed to contain. Nothing else does.

The original was 285 lines of nine near-identical blocks, which is how several
of these defects survived: they are present in some copies of the block and not
others.
"""

from __future__ import annotations

import datetime

import pytest

from archihub.api.resources import validation


def field(destiny, type_, **extra):
    return {"destiny": destiny, "type": type_, "label": destiny, **extra}


def form(*fields):
    return {"fields": list(fields)}


def published(**body):
    return {"status": "published", **body}


def draft(**body):
    return {"status": "draft", **body}


@pytest.fixture(autouse=True)
def no_access_rights_list(monkeypatch):
    """An instance with no configured access-rights vocabulary."""
    import archihub.core.roles as roles

    monkeypatch.setattr(roles, "get_access_rights", lambda: {"options": []})
    return roles


@pytest.fixture(autouse=True)
def no_hooks(monkeypatch):
    class NoHooks:
        def call(self, *args, **kwargs):
            return None

    import archihub.core.hooks as hooks

    monkeypatch.setattr(hooks, "get_hook_handler", lambda: NoHooks())
    return hooks


class FakeMongo:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get_record(self, collection, filters=None, fields=None):
        return self.rows.get((collection, str(filters.get("_id"))))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(validation, "_mongo", lambda: fake)
    monkeypatch.setattr(validation, "_to_object_id", lambda value: value)
    return fake


# ---------------------------------------------------------------------------
# Dotted paths
# ---------------------------------------------------------------------------


def test_a_path_through_a_non_mapping_returns_none_rather_than_raising():
    """The original tested ``key in value`` without checking it was a mapping,
    so a path descending through a string raised TypeError - which the caller
    caught and reported as a validation error against the user's field."""
    assert validation.get_value_by_path({"a": "text"}, "a.b.c") is None
    assert validation.get_value_by_path({"a": [1, 2]}, "a.b") is None


def test_a_missing_path_is_none():
    assert validation.get_value_by_path({}, "metadata.firstLevel.title") is None


def test_writing_a_path_creates_the_intermediate_levels():
    document = validation.set_value_by_path({}, "metadata.firstLevel.title", "Hola")
    assert document == {"metadata": {"firstLevel": {"title": "Hola"}}}


def test_writing_over_a_scalar_replaces_it_with_a_mapping():
    document = validation.set_value_by_path({"metadata": "junk"}, "metadata.a", 1)
    assert document == {"metadata": {"a": 1}}


# ---------------------------------------------------------------------------
# Requiredness
# ---------------------------------------------------------------------------


def test_a_required_field_is_enforced_on_publish():
    metadata = form(field("metadata.x", "text", required=True))
    _body, errors = validation.validate_fields(published(), metadata)
    assert "metadata.x" in errors


def test_a_draft_may_be_missing_anything():
    """That is what a draft is for."""
    metadata = form(field("metadata.x", "text", required=True))
    _body, errors = validation.validate_fields(draft(), metadata)
    assert errors == {}


def test_an_optional_field_is_never_required():
    metadata = form(field("metadata.x", "text", required=False))
    _body, errors = validation.validate_fields(published(), metadata)
    assert errors == {}


def test_access_rights_is_exempt_from_requiredness():
    """It is validated by its own rule below, not by the generic one."""
    metadata = form(field("accessRights", "select", required=True))
    _body, errors = validation.validate_fields(published(), metadata)
    assert errors == {}


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_every_empty_spelling_counts_as_absent(empty):
    metadata = form(field("metadata.x", "text", required=True))
    _body, errors = validation.validate_fields(
        published(metadata={"x": empty}), metadata
    )
    assert "metadata.x" in errors


def test_file_and_separator_fields_carry_no_value():
    metadata = form(field("f", "file", required=True), field("s", "separator", required=True))
    _body, errors = validation.validate_fields(published(), metadata)
    assert errors == {}


# ---------------------------------------------------------------------------
# The title rule
# ---------------------------------------------------------------------------


def test_an_untitled_draft_gets_a_placeholder():
    """A resource with no title is unfindable in every listing and tree."""
    metadata = form(field(validation.TITLE_PATH, "text", required=True))
    body, errors = validation.validate_fields(draft(), metadata)

    assert errors == {}
    assert validation.get_value_by_path(body, validation.TITLE_PATH) == validation.UNTITLED


def test_an_untitled_publish_is_refused_when_the_title_is_required():
    metadata = form(field(validation.TITLE_PATH, "text", required=True))
    _body, errors = validation.validate_fields(published(), metadata)
    assert validation.TITLE_PATH in errors


def test_an_untitled_publish_is_allowed_when_the_title_is_optional():
    metadata = form(field(validation.TITLE_PATH, "text", required=False))
    body, errors = validation.validate_fields(published(), metadata)

    assert errors == {}
    assert validation.get_value_by_path(body, validation.TITLE_PATH) == validation.UNTITLED


# ---------------------------------------------------------------------------
# Per-type validation
# ---------------------------------------------------------------------------


def test_a_text_field_rejects_a_non_string():
    metadata = form(field("metadata.x", "text"))
    _body, errors = validation.validate_fields(published(metadata={"x": 5}), metadata)
    assert "must be of type string" in errors["metadata.x"]


def test_the_specific_message_survives():
    """Every legacy validator wrapped its own message in a generic
    'Error while validating the field X', so none of the specific text those
    functions carefully produced ever reached a user."""
    metadata = form(field("metadata.x", "number"))
    _body, errors = validation.validate_fields(published(metadata={"x": "abc"}), metadata)
    assert "must be a number" in errors["metadata.x"]


def test_a_boolean_is_not_a_number():
    """``isinstance(True, numbers.Number)`` is True in Python."""
    metadata = form(field("metadata.x", "number"))
    _body, errors = validation.validate_fields(published(metadata={"x": True}), metadata)
    assert "metadata.x" in errors


def test_a_checkbox_must_be_a_boolean():
    metadata = form(field("metadata.x", "checkbox"))
    _body, errors = validation.validate_fields(published(metadata={"x": "yes"}), metadata)
    assert "must be a boolean" in errors["metadata.x"]


def test_a_select_multiple_enforces_its_item_bounds():
    metadata = form(field("metadata.x", "select-multiple2", max_items=2))
    _body, errors = validation.validate_fields(
        published(metadata={"x": ["a", "b", "c"]}), metadata
    )
    assert "at most" in errors["metadata.x"]


def test_a_location_must_be_a_list_of_dicts():
    metadata = form(field("metadata.x", "location"))
    _body, errors = validation.validate_fields(published(metadata={"x": ["nope"]}), metadata)
    assert "list of dicts" in errors["metadata.x"]


@pytest.mark.parametrize("author", ["Borges,Jorge", "Borges|Jorge", "Borges,", "|Jorge"])
def test_an_author_may_be_written_either_way(author):
    metadata = form(field("metadata.x", "author"))
    _body, errors = validation.validate_fields(published(metadata={"x": [author]}), metadata)
    assert errors == {}


@pytest.mark.parametrize("author", [",", "|", "Borges", 5])
def test_a_meaningless_author_is_refused(author):
    metadata = form(field("metadata.x", "author"))
    _body, errors = validation.validate_fields(published(metadata={"x": [author]}), metadata)
    assert "metadata.x" in errors


def test_an_iso_string_date_is_stored_as_a_datetime():
    metadata = form(field("metadata.x", "simple-date"))
    body, errors = validation.validate_fields(
        published(metadata={"x": "2024-03-01T00:00:00"}), metadata
    )

    assert errors == {}
    assert isinstance(body["metadata"]["x"], datetime.datetime)


def test_a_json_encoded_date_string_is_still_parsed():
    """The frontend has been known to send the value with its quotes."""
    metadata = form(field("metadata.x", "simple-date"))
    body, errors = validation.validate_fields(
        published(metadata={"x": '"2024-03-01T00:00:00"'}), metadata
    )

    assert errors == {}
    assert body["metadata"]["x"].year == 2024


def test_an_unparseable_date_is_an_error_not_a_crash():
    metadata = form(field("metadata.x", "simple-date"))
    _body, errors = validation.validate_fields(published(metadata={"x": "not a date"}), metadata)
    assert "must be of type date" in errors["metadata.x"]


# ---------------------------------------------------------------------------
# References to other records
# ---------------------------------------------------------------------------


def test_a_userslist_entry_must_name_a_real_user(mongo):
    metadata = form(field("metadata.x", "userslist"))
    _body, errors = validation.validate_fields(
        published(metadata={"x": [{"id": "ghost"}]}), metadata
    )
    assert "metadata.x" in errors


def test_a_userslist_of_real_users_passes(mongo):
    mongo.rows[("users", "u1")] = {"_id": "u1"}
    metadata = form(field("metadata.x", "userslist"))
    _body, errors = validation.validate_fields(published(metadata={"x": [{"id": "u1"}]}), metadata)
    assert errors == {}


def test_a_relation_is_reduced_to_id_and_type(mongo):
    mongo.rows[("resources", "r1")] = {"_id": "r1", "post_type": "foto"}
    metadata = form(field("metadata.x", "relation", relation_type="foto"))

    body, errors = validation.validate_fields(
        published(metadata={"x": [{"id": "r1", "junk": "dropped"}]}), metadata
    )

    assert errors == {}
    assert body["metadata"]["x"] == [{"id": "r1", "post_type": "foto"}]


def test_a_relation_to_the_wrong_content_type_is_refused(mongo):
    mongo.rows[("resources", "r1")] = {"_id": "r1", "post_type": "carpeta"}
    metadata = form(field("metadata.x", "relation", relation_type="foto"))

    _body, errors = validation.validate_fields(published(metadata={"x": [{"id": "r1"}]}), metadata)
    assert "metadata.x" in errors


def test_a_relation_to_a_missing_resource_is_refused(mongo):
    metadata = form(field("metadata.x", "relation", relation_type="foto"))
    _body, errors = validation.validate_fields(published(metadata={"x": [{"id": "gone"}]}), metadata)
    assert "metadata.x" in errors


def test_a_malformed_id_is_a_validation_error_not_a_bson_message():
    """The original handed client input straight to ObjectId(), and the raw
    InvalidId text became the message shown to the user."""
    metadata = form(field("metadata.x", "relation", relation_type="foto"))
    _body, errors = validation.validate_fields(
        published(metadata={"x": [{"id": "not-an-object-id"}]}), metadata
    )

    assert "metadata.x" in errors
    assert "InvalidId" not in errors["metadata.x"]


# ---------------------------------------------------------------------------
# Conditional fields
# ---------------------------------------------------------------------------


def test_a_hidden_conditional_field_is_cleared():
    metadata = form(
        field("metadata.toggle", "checkbox"),
        field("metadata.x", "text", conditionField=0),
    )
    body, _errors = validation.validate_fields(
        published(metadata={"toggle": False, "x": "left over"}), metadata
    )

    assert body["metadata"]["x"] == ""


def test_a_condition_on_the_first_field_of_the_form_is_honoured():
    """THE bug.

    ``conditionField`` is an index, and index 0 is falsy - so the original's
    ``if hasCondition`` guard treated a field conditioned on the *first* field
    of the form as unconditional, and kept a value the user had hidden.
    """
    metadata = form(
        field("metadata.toggle", "checkbox"),
        field("metadata.x", "text", conditionField="0"),
    )
    body, _errors = validation.validate_fields(
        published(metadata={"toggle": False, "x": "should be cleared"}), metadata
    )

    assert body["metadata"]["x"] == ""


def test_a_visible_conditional_field_keeps_its_value():
    metadata = form(
        field("metadata.toggle", "checkbox"),
        field("metadata.x", "text", conditionField=0),
    )
    body, _errors = validation.validate_fields(
        published(metadata={"toggle": True, "x": "kept"}), metadata
    )

    assert body["metadata"]["x"] == "kept"


@pytest.mark.parametrize("bad", [99, -1, "abc", None])
def test_an_unusable_condition_index_is_ignored_not_raised(bad):
    metadata = form(
        field("metadata.toggle", "checkbox"),
        field("metadata.x", "text", conditionField=bad),
    )
    body, errors = validation.validate_fields(
        published(metadata={"toggle": False, "x": "kept"}), metadata
    )

    assert errors == {}
    assert body["metadata"]["x"] == "kept"


def test_a_condition_on_a_non_checkbox_is_ignored():
    metadata = form(
        field("metadata.other", "text"),
        field("metadata.x", "text", conditionField=0),
    )
    body, _errors = validation.validate_fields(
        published(metadata={"other": "", "x": "kept"}), metadata
    )

    assert body["metadata"]["x"] == "kept"


@pytest.mark.parametrize(
    "field_type,cleared",
    [
        ("text", ""),
        ("select", None),
        ("number", None),
        ("checkbox", False),
        ("select-multiple2", []),
        ("repeater", []),
    ],
)
def test_each_type_is_cleared_to_its_own_empty_value(field_type, cleared):
    metadata = form(
        field("metadata.toggle", "checkbox"),
        field("metadata.x", field_type, conditionField=0),
    )
    body, _errors = validation.validate_fields(
        published(metadata={"toggle": False, "x": "whatever"}), metadata
    )

    assert body["metadata"]["x"] == cleared


# ---------------------------------------------------------------------------
# Repeaters
# ---------------------------------------------------------------------------


def repeater(*subfields):
    return field("metadata.rows", "repeater", subfields=list(subfields))


def test_a_repeater_validates_each_row():
    metadata = form(repeater({"destiny": "n", "type": "number", "name": "Number"}))
    _body, errors = validation.validate_fields(
        published(metadata={"rows": [{"n": 1}, {"n": "nope"}]}), metadata
    )

    assert list(errors) == ["metadata.rows.1.n"]


def test_two_rows_with_the_same_problem_are_reported_separately():
    """The original keyed errors by subfield name alone, so several bad rows
    collapsed into one message and the user could not tell which to fix."""
    metadata = form(repeater({"destiny": "n", "type": "number", "name": "Number"}))
    _body, errors = validation.validate_fields(
        published(metadata={"rows": [{"n": "a"}, {"n": "b"}]}), metadata
    )

    assert set(errors) == {"metadata.rows.0.n", "metadata.rows.1.n"}


def test_a_missing_required_subfield_is_reported_on_publish():
    metadata = form(repeater({"destiny": "n", "type": "text", "name": "Name", "required": True}))
    _body, errors = validation.validate_fields(published(metadata={"rows": [{}]}), metadata)
    assert "metadata.rows.0.n" in errors


def test_a_missing_subfield_key_does_not_raise():
    """The original subscripted ``v[subfield['destiny']]`` directly, so a row
    saved before the subfield existed raised KeyError, and the raw key name
    became the error message."""
    metadata = form(repeater({"destiny": "n", "type": "text", "name": "Name"}))
    _body, errors = validation.validate_fields(published(metadata={"rows": [{}]}), metadata)
    assert errors == {}


def test_a_repeater_row_date_is_parsed_in_place():
    metadata = form(repeater({"destiny": "d", "type": "simple-date", "name": "When"}))
    body, errors = validation.validate_fields(
        published(metadata={"rows": [{"d": "2024-03-01"}]}), metadata
    )

    assert errors == {}
    assert isinstance(body["metadata"]["rows"][0]["d"], datetime.datetime)


def test_validating_a_repeater_does_not_mutate_the_form_definition():
    """The original wrote ``subfield['label'] = subfield['name']`` into the
    shared, cached content-type definition on every save."""
    subfield = {"destiny": "n", "type": "text", "name": "Name"}
    metadata = form(repeater(subfield))

    validation.validate_fields(published(metadata={"rows": [{"n": "x"}]}), metadata)

    assert "label" not in subfield


def test_a_repeater_that_is_not_a_list_is_an_error():
    metadata = form(repeater({"destiny": "n", "type": "text", "name": "Name"}))
    _body, errors = validation.validate_fields(published(metadata={"rows": "nope"}), metadata)
    assert "metadata.rows" in errors


# ---------------------------------------------------------------------------
# Access rights
# ---------------------------------------------------------------------------


def test_an_absent_access_right_is_stored_as_none():
    body, errors = validation.validate_fields(published(), form())
    assert body["accessRights"] is None
    assert errors == {}


def test_public_is_stored_as_none():
    body, errors = validation.validate_fields(published(accessRights="public"), form())
    assert body["accessRights"] is None
    assert errors == {}


def test_an_empty_access_right_is_refused():
    """It is what a half-filled form sends; treating it as public would be the
    wrong default for an archive."""
    _body, errors = validation.validate_fields(published(accessRights=""), form())
    assert "accessRights" in errors


def test_an_unknown_access_right_is_refused():
    _body, errors = validation.validate_fields(published(accessRights="made-up"), form())
    assert "accessRights" in errors


def test_a_configured_access_right_is_accepted(monkeypatch):
    import archihub.core.roles as roles

    monkeypatch.setattr(roles, "get_access_rights", lambda: {"options": [{"id": "reserved"}]})

    body, errors = validation.validate_fields(published(accessRights="reserved"), form())
    assert errors == {}
    assert body["accessRights"] == "reserved"


# ---------------------------------------------------------------------------
# The validate_field hook
# ---------------------------------------------------------------------------


def test_a_failing_plugin_hook_does_not_take_the_save_down(monkeypatch):
    """The original let the exception surface as that field's error message,
    so a broken plugin looked to the user like bad data in their own form."""

    class Exploding:
        def call(self, *args, **kwargs):
            raise RuntimeError("plugin is broken")

    import archihub.core.hooks as hooks

    monkeypatch.setattr(hooks, "get_hook_handler", lambda: Exploding())

    metadata = form(field("metadata.x", "text"))
    _body, errors = validation.validate_fields(published(metadata={"x": "fine"}), metadata)

    assert errors == {}


def test_a_hook_may_rewrite_the_body(monkeypatch):
    class Rewriting:
        def call(self, name, body, *args, **kwargs):
            return {**body, "metadata": {"x": "rewritten"}}

    import archihub.core.hooks as hooks

    monkeypatch.setattr(hooks, "get_hook_handler", lambda: Rewriting())

    metadata = form(field("metadata.x", "text"))
    body, _errors = validation.validate_fields(published(metadata={"x": "original"}), metadata)

    assert body["metadata"]["x"] == "rewritten"


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def test_a_file_field_ceiling_is_enforced():
    metadata = form({"destiny": "f", "type": "file", "filetag": "scan", "maxFiles": 2, "label": "Scans"})
    errors = validation.validate_files(
        [{"tag": "scan"}, {"tag": "scan"}, {"tag": "scan"}], metadata
    )
    assert "scan" in errors


def test_exactly_the_ceiling_is_allowed():
    metadata = form({"destiny": "f", "type": "file", "filetag": "scan", "maxFiles": 2, "label": "Scans"})
    assert validation.validate_files([{"tag": "scan"}, {"tag": "scan"}], metadata) == {}


@pytest.mark.parametrize("no_ceiling", ["", 0, None])
def test_an_unset_ceiling_means_unlimited(no_ceiling):
    metadata = form(
        {"destiny": "f", "type": "file", "filetag": "scan", "maxFiles": no_ceiling, "label": "Scans"}
    )
    assert validation.validate_files([{"tag": "scan"}] * 50, metadata) == {}


def test_the_older_filetag_spelling_still_counts():
    metadata = form({"destiny": "f", "type": "file", "filetag": "scan", "maxFiles": 1, "label": "Scans"})
    errors = validation.validate_files([{"filetag": "scan"}, {"filetag": "scan"}], metadata)
    assert "scan" in errors


def test_files_for_an_undeclared_tag_are_not_counted():
    metadata = form({"destiny": "f", "type": "file", "filetag": "scan", "maxFiles": 1, "label": "Scans"})
    assert validation.validate_files([{"tag": "other"}, {"tag": "other"}], metadata) == {}


def test_a_form_with_no_file_fields_needs_no_check():
    assert validation.validate_files([{"tag": "scan"}], form()) == {}
