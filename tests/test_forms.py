"""Metadata-form domain.

Forms define the fields a content type catalogues. Every form contributes to one
combined schema, so cross-form consistency is enforced before any write.
"""

from __future__ import annotations

import pytest

from archihub.api.forms import services
from archihub.core.errors import ValidationError


class FakeInsertResult:
    inserted_id = "000000000000000000000001"


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.collections: dict[str, list] = {}
        self.counts: dict[str, int] = {}
        self.inserted: list = []
        self.updated: list = []
        self.deleted: list = []

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        rows = self.collections.get(collection, [])
        if filters and "slug" in filters and isinstance(filters["slug"], dict):
            excluded = filters["slug"].get("$ne")
            rows = [r for r in rows if r.get("slug") != excluded]
        return list(rows)

    def count(self, collection, filters=None):
        return self.counts.get(collection, 0)

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))
        return FakeInsertResult()

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))

    def delete_record(self, collection, filters):
        self.deleted.append((collection, filters))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    monkeypatch.setattr(services, "_clear_cache", lambda: None)
    monkeypatch.setattr(services, "_call_hook", lambda *a, **k: None)
    monkeypatch.setattr(services, "_call_hook_with_result", lambda name, payload: None)
    monkeypatch.setattr(services, "_get_access_rights", lambda: {"options": [{"id": "public"}]})
    monkeypatch.setattr(services, "_get_access_rights_id", lambda: "rights-list-id")
    return fake


def title_field(**overrides):
    field = {"label": "Title", "destiny": services.TITLE_DESTINY, "type": "text", "name": "title"}
    field.update(overrides)
    return field


# ---------------------------------------------------------------------------
# Slug generation - the cumulative-suffix fix
# ---------------------------------------------------------------------------


def test_unique_slug_does_not_accumulate_suffixes(mongo):
    """Regression guard for slugs like `test-1-2-3`.

    The legacy loop reassigned the suffixed value back into the variable it was
    suffixing, so the third form named "Test" became `test-1-2` and the fourth
    `test-1-2-3`. The types domain kept a separate base and got this right;
    forms did not.
    """
    taken = {"test", "test-1", "test-2"}
    mongo.records["forms"] = lambda filters: {"slug": filters["slug"]} if filters["slug"] in taken else None

    assert services.make_unique_slug("test") == "test-3"


def test_free_slug_is_used_unchanged(mongo):
    mongo.records["forms"] = None
    assert services.make_unique_slug("brand-new") == "brand-new"


# ---------------------------------------------------------------------------
# validate_form
# ---------------------------------------------------------------------------


def test_form_must_have_a_title_field(mongo):
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [{"label": "X", "type": "text", "destiny": "metadata.x"}]})


def test_title_field_must_be_text(mongo):
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [title_field(type="number")]})


def test_field_needs_a_non_empty_label(mongo):
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [{"type": "text", "destiny": "metadata.x"}]})
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [{"label": "", "type": "text", "destiny": "metadata.x"}]})


def test_destiny_must_start_with_metadata(mongo):
    with pytest.raises(ValidationError):
        services.validate_form(
            {"fields": [title_field(), {"label": "X", "type": "text", "destiny": "somewhere"}]}
        )


@pytest.mark.parametrize("field_type", ["separator", "file"])
def test_separator_and_file_are_exempt_from_the_destiny_rule(mongo, field_type):
    extra = {"label": "X", "type": field_type, "destiny": "anything"}
    if field_type == "file":
        extra["filetag"] = "tag"
    services.validate_form({"fields": [title_field(), extra]})


def test_ident_destiny_is_rejected(mongo):
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [title_field(), {"label": "X", "type": "text", "destiny": "ident"}]})


def test_file_field_requires_a_filetag(mongo):
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [title_field(), {"label": "F", "type": "file"}]})


@pytest.mark.parametrize(
    "repeater",
    [
        {"label": "R", "type": "repeater"},                                  # no subfields
        {"label": "R", "type": "repeater", "subfields": "not-a-list"},
        {"label": "R", "type": "repeater", "subfields": []},
        {"label": "R", "type": "repeater", "subfields": [{"name": "a", "type": "text"}]},  # no destiny
    ],
)
def test_repeater_subfield_rules(mongo, repeater):
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [title_field(), repeater]})


def test_condition_keys_are_stripped_when_there_is_no_condition(mongo):
    """Without setCondition the condition parameters are meaningless.

    validate_form mutates the payload here, and the write that follows depends
    on the normalised shape.
    """
    field = {
        "label": "X", "type": "text", "destiny": "metadata.x",
        "conditionField": "other", "conditionType": "eq", "conditionValueText": "v",
    }
    services.validate_form({"fields": [title_field(), field]})

    assert field["setCondition"] is False
    assert "conditionField" not in field
    assert "conditionType" not in field
    assert "conditionValueText" not in field


def test_condition_keys_survive_when_a_condition_is_set(mongo):
    field = {
        "label": "X", "type": "text", "destiny": "metadata.x",
        "setCondition": True, "conditionField": "other",
    }
    services.validate_form({"fields": [title_field(), field]})
    assert field["conditionField"] == "other"


def test_unknown_access_right_is_rejected(mongo):
    field = {"label": "X", "type": "text", "destiny": "metadata.x", "accessRights": ["nope"]}
    with pytest.raises(ValidationError):
        services.validate_form({"fields": [title_field(), field]})


# ---------------------------------------------------------------------------
# update_main_schema - cross-form consistency
# ---------------------------------------------------------------------------


def test_conflicting_types_on_the_same_destiny_are_rejected(mongo):
    """Two forms may share a destiny, but not with incompatible types.

    The destination is one field on the resource.
    """
    mongo.collections["forms"] = [
        {"slug": "other", "fields": [{"destiny": "metadata.x", "type": "text"}]}
    ]
    new_form = {"slug": "new", "fields": [{"destiny": "metadata.x", "type": "number"}]}

    with pytest.raises(ValidationError):
        services.update_main_schema(new_form=new_form)


def test_interchangeable_select_types_do_not_conflict(mongo):
    mongo.collections["forms"] = [
        {"slug": "other", "fields": [{"destiny": "metadata.x", "type": "select"}]}
    ]
    new_form = {"slug": "new", "fields": [{"destiny": "metadata.x", "type": "select-multiple2"}]}

    services.update_main_schema(new_form=new_form)  # must not raise


def test_a_form_does_not_conflict_with_its_own_previous_definition(mongo):
    """When updating, the stored version of the same form is excluded."""
    mongo.collections["forms"] = [
        {"slug": "mine", "fields": [{"destiny": "metadata.x", "type": "text"}]}
    ]
    updated = {"slug": "mine", "fields": [{"destiny": "metadata.x", "type": "number"}]}

    services.update_main_schema(updated_form=updated)  # must not raise


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_get_by_slug_prepends_the_synthetic_access_rights_field(mongo):
    """Not stored - every form gets it at read time."""
    mongo.records["forms"] = {"_id": "x", "name": "F", "slug": "f", "fields": [title_field()]}

    payload, status = services.get_by_slug("f")

    assert status == 200
    assert payload["fields"][0]["name"] == "accessRights"
    assert payload["fields"][0]["list"] == "rights-list-id"


def test_missing_form_is_404_and_translated(mongo):
    """Legacy returned a hardcoded, untranslated Spanish string here."""
    mongo.records["forms"] = None
    payload, status = services.get_by_slug("nope")

    assert status == 404
    assert payload["msg"] != "Formulario no existe"


def test_update_checks_existence_before_validating(mongo):
    """Legacy validated and rebuilt the combined schema BEFORE looking the form
    up, so an update aimed at a nonexistent form could fail with a validation
    error instead of the real answer: 404."""
    mongo.records["forms"] = None

    # Deliberately invalid body: no title field. The 404 must still win.
    payload, status = services.update_by_slug("nope", {"fields": []}, "admin")
    assert status == 404


def test_delete_refuses_while_a_content_type_uses_the_form(mongo):
    mongo.records["forms"] = {"name": "F", "slug": "f"}
    mongo.counts["post_types"] = 1

    payload, status = services.delete_by_slug("f", "admin")

    assert status == 400
    assert mongo.deleted == []


def test_delete_succeeds_with_204_when_unused(mongo):
    mongo.records["forms"] = {"name": "F", "slug": "f"}
    mongo.counts["post_types"] = 0

    _payload, status = services.delete_by_slug("f", "admin")

    assert status == 204
    assert mongo.deleted == [("forms", {"slug": "f"})]


def test_slug_search_is_bounded(mongo):
    """An existence query that always answers "yes" must not spin forever.

    The legacy loop had no bound: it asked the database whether a slug was taken
    and suffixed until told otherwise. Any condition that makes that query always
    report "taken" hangs the request thread indefinitely. Found by a test fixture
    that returned the same record regardless of filter - which is exactly how the
    real failure would look.
    """
    mongo.records["forms"] = {"slug": "always-taken"}  # every lookup matches

    with pytest.raises(RuntimeError):
        services.make_unique_slug("anything")


def test_duplicate_renames_and_reslugs(mongo):
    # Filter-aware: only the original slug exists, so the derived slug is free.
    original = {"_id": "x", "name": "Original", "slug": "original", "fields": [title_field()]}
    mongo.records["forms"] = lambda filters: original if filters.get("slug") == "original" else None
    mongo.collections["forms"] = []

    payload, status = services.duplicate_by_slug("original", "admin")

    assert status == 201
    _collection, record = mongo.inserted[-1]
    assert record["name"] == "Original (copia)"
    assert record["slug"] != "original"


def test_duplicate_missing_form_is_404(mongo):
    mongo.records["forms"] = None
    _payload, status = services.duplicate_by_slug("nope", "admin")
    assert status == 404


def test_create_does_not_write_an_id(mongo):
    mongo.records["forms"] = None
    mongo.collections["forms"] = []

    services.create({"name": "F", "description": "d", "fields": [title_field()]}, "admin")

    _collection, record = mongo.inserted[-1]
    assert "_id" not in record and "id" not in record


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------


def test_field_type_ids_are_a_stable_contract():
    """Ids are stored on every form field and matched by the frontend.

    `userslit` is a long-standing typo for "user list"; correcting the spelling
    would orphan every field already using it.
    """
    from archihub.api.forms.field_types import FIELD_TYPES

    ids = [f["id"] for f in FIELD_TYPES]
    assert "userslit" in ids
    assert ids == [
        "text", "text-area", "number", "simple-date", "select", "select-multiple2",
        "checkbox", "file", "repeater", "separator", "author", "location", "userslit",
    ]


def test_field_types_are_copied_not_shared(mongo):
    """Labels are translated in place, so the catalogue must not be handed out."""
    from archihub.api.forms.field_types import FIELD_TYPES

    first, _status = services.get_all_fields_types()
    first[0]["label"] = "MUTATED"

    assert FIELD_TYPES[0]["label"] != "MUTATED"
    second, _status = services.get_all_fields_types()
    assert second[0]["label"] != "MUTATED"


# ---------------------------------------------------------------------------
# A rejected form is an answer, not a server fault
#
# Everything validate_form and update_main_schema refuse is something a
# cataloguer typed. Reporting it as 500 says "the server is broken" about a typo
# the user can fix, and buries genuine faults among the typos.
# ---------------------------------------------------------------------------


def _conflicting_form():
    return {
        "name": "Test",
        "slug": "test",
        "fields": [
            {"label": "A", "destiny": "metadata.firstLevel.dup", "type": "text"},
            {"label": "B", "destiny": "metadata.firstLevel.dup", "type": "simple-date"},
        ],
    }


def test_a_duplicated_destiny_is_a_client_error(mongo):
    mongo.collections["forms"] = []

    with pytest.raises(ValidationError) as raised:
        services.update_main_schema(new_form=_conflicting_form())

    assert raised.value.status_code == 400


def test_the_refusal_names_the_offending_field(mongo):
    """The message is what the form builder shows; without the destiny in it the
    cataloguer cannot tell which of forty fields to fix."""
    mongo.collections["forms"] = []

    with pytest.raises(ValidationError) as raised:
        services.update_main_schema(new_form=_conflicting_form())

    assert "metadata.firstLevel.dup" in raised.value.message


def test_create_does_not_convert_a_refusal_into_a_500(mongo):
    """The blanket `except Exception` must not swallow it on the way out."""
    mongo.collections["forms"] = []

    with pytest.raises(ValidationError):
        services.create(_conflicting_form(), "admin")

    assert mongo.inserted == []


def test_update_does_not_convert_a_refusal_into_a_500(mongo):
    mongo.collections["forms"] = []
    mongo.records["forms"] = {"slug": "test"}

    with pytest.raises(ValidationError):
        services.update_by_slug("test", _conflicting_form(), "admin")

    assert mongo.updated == []


def test_a_refusal_is_not_logged_as_an_unexpected_error(mongo, caplog):
    """`logger.exception` at ERROR with a traceback is for faults nobody
    anticipated. A traceback per typo is what makes a log unreadable."""
    import logging

    mongo.collections["forms"] = []

    with caplog.at_level(logging.ERROR, logger="archihub.api.forms.services"):
        with pytest.raises(ValidationError):
            services.create(_conflicting_form(), "admin")

    assert caplog.records == []


def test_an_unexpected_failure_is_still_a_500_with_its_traceback(mongo, monkeypatch):
    """The distinction only helps if genuine faults keep their loud treatment."""
    mongo.collections["forms"] = []

    def explode(*_args, **_kwargs):
        raise RuntimeError("mongo is on fire")

    monkeypatch.setattr(services, "validate_form", explode)

    payload, status = services.create({"name": "Test", "fields": []}, "admin")

    assert status == 500
