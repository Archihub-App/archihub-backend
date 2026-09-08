"""CHRAutoIdent — the identifier and title a resource is saved with.

This runs on `resource_pre_create`/`resource_pre_update`, so its return value
IS the resource that gets stored. Two things therefore matter more than they
would in a background job: it must never return something other than a payload,
and it must never change a field on a resource that already has one.
"""

from __future__ import annotations

import datetime

import pytest

plugin = pytest.importorskip(
    "archihub.plugins.CHRAutoIdent",
    reason="CHRAutoIdent is not installed in this checkout",
)

CATALOGER = "6a88b6e74bb8b789b188db90"
TYPE_CONFIG = {"type": "caso", "order": 0}


# ---------------------------------------------------------------------------
# The parts of an identifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Pedro Elí Ruiz Zárate", "PERZ"),
        ("Ana Gómez", "AGXX"),
        ("Ángela", "AXXX"),
        ("", "XXXX"),
        (None, "XXXX"),
        ("Uno Dos Tres Cuatro Cinco", "UDTC"),
        ("  espacios   dobles ", "EDXX"),
    ],
)
def test_initials_are_always_four_unaccented_characters(name, expected):
    """The block is fixed width so identifiers line up, and unaccented because
    the same person must not produce two prefixes depending on how their name
    was typed."""
    assert plugin.cataloger_initials(name) == expected


def test_an_identifier_carries_the_prefix_date_and_sequence():
    when = datetime.datetime(2026, 9, 7, 14, 30)
    assert plugin.build_ident("PERZ", when, 3) == "CHRPERZ2026090703"


def test_the_sequence_is_zero_padded_to_two_digits():
    when = datetime.datetime(2026, 1, 2)
    assert plugin.build_ident("AAAA", when, 1).endswith("2026010201")


@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("ident", True), ("CHRPERZ2026090701", False),
])
def test_the_placeholder_counts_as_no_identifier(value, expected):
    """The resource domain writes the literal string `ident` when none was sent."""
    assert plugin.needs_ident(value) is expected


def test_a_title_needs_both_halves():
    assert plugin.build_title("Juan Pérez", "CHR1") == "Juan Pérez - CHR1"
    assert plugin.build_title(None, "CHR1") is None
    assert plugin.build_title("Juan Pérez", None) is None


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, term="Pedro Elí Ruiz Zárate", stored_ident=None, todays=0):
        self.term = term
        self.stored_ident = stored_ident
        self.todays = todays

    def get_record(self, collection, filters, fields=None):
        if collection == "options":
            return {"term": self.term} if self.term is not None else None
        if collection == "resources":
            return {"ident": self.stored_ident} if self.stored_ident is not None else None
        return None

    def count(self, collection, filters=None):
        return self.todays


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(plugin, "_mongo", lambda: fake)
    return fake


def _body(**overrides):
    body = {
        "post_type": "caso",
        "metadata": {"firstLevel": {"encargado_reg": CATALOGER}},
    }
    body.update(overrides)
    return body


def test_a_new_resource_is_given_an_identifier(mongo):
    result = plugin.automatic(TYPE_CONFIG, _body())

    assert result["ident"].startswith("CHRPERZ")
    assert result["ident"].endswith("01")


def test_the_sequence_follows_what_the_cataloguer_already_filed_today(mongo):
    mongo.todays = 6
    assert plugin.automatic(TYPE_CONFIG, _body())["ident"].endswith("07")


def test_a_resource_of_another_content_type_is_returned_untouched(mongo):
    body = _body(post_type="otro")
    assert plugin.automatic(TYPE_CONFIG, body) == body
    assert "ident" not in body


def test_a_resource_with_no_cataloguer_is_returned_untouched(mongo):
    body = {"post_type": "caso", "metadata": {"firstLevel": {}}}
    assert plugin.automatic(TYPE_CONFIG, body) is body
    assert "ident" not in body


def test_an_unknown_cataloguer_leaves_the_resource_alone(mongo):
    mongo.term = None
    assert "ident" not in plugin.automatic(TYPE_CONFIG, _body())


def test_a_cataloguer_id_that_is_not_an_object_id_leaves_the_resource_alone(mongo):
    body = _body(metadata={"firstLevel": {"encargado_reg": "not-an-id"}})
    assert "ident" not in plugin.automatic(TYPE_CONFIG, body)


def test_an_identifier_the_client_sent_is_kept(mongo):
    result = plugin.automatic(TYPE_CONFIG, _body(ident="CHRAAAA2020010101"))
    assert result["ident"] == "CHRAAAA2020010101"


def test_an_update_that_omits_the_identifier_does_not_renumber_the_resource(mongo):
    """An update payload carries only the fields the client sent, so an absent
    `ident` means "not mentioned" - minting one here would renumber a resource
    already filed and cited under its current identifier."""
    mongo.stored_ident = "CHRAAAA2020010101"

    result = plugin.automatic(TYPE_CONFIG, _body(_id="6a88b6e74bb8b789b188db90"))

    assert "ident" not in result


def test_a_title_is_built_when_the_identifier_already_exists(mongo):
    """The path that raised UnboundLocalError: an identifier was present, so
    nothing was generated, and the title was then built out of the name the
    generating branch would have bound."""
    mongo.stored_ident = "CHRAAAA2020010101"
    body = _body(
        _id="6a88b6e74bb8b789b188db90",
        metadata={"firstLevel": {"encargado_reg": CATALOGER, "desaparecido": "Juan Pérez"}},
    )

    result = plugin.automatic(TYPE_CONFIG, body)

    assert result["metadata"]["firstLevel"]["title"] == "Juan Pérez - CHRAAAA2020010101"


def test_a_title_is_built_from_the_identifier_just_assigned(mongo):
    body = _body(metadata={"firstLevel": {"encargado_reg": CATALOGER, "desaparecido": "Juan Pérez"}})

    result = plugin.automatic(TYPE_CONFIG, body)

    assert result["metadata"]["firstLevel"]["title"] == f"Juan Pérez - {result['ident']}"


def test_a_title_the_cataloguer_wrote_is_not_overwritten(mongo):
    body = _body(metadata={
        "firstLevel": {"encargado_reg": CATALOGER, "desaparecido": "Juan Pérez", "title": "Mi título"}
    })
    assert plugin.automatic(TYPE_CONFIG, body)["metadata"]["firstLevel"]["title"] == "Mi título"


def test_no_title_is_invented_without_a_name(mongo):
    result = plugin.automatic(TYPE_CONFIG, _body())
    assert "title" not in result["metadata"]["firstLevel"]


@pytest.mark.parametrize("body", [None, "text", 42, []])
def test_a_payload_that_is_not_a_resource_is_handed_straight_back(mongo, body):
    """The return value replaces what the caller goes on to store."""
    assert plugin.automatic(TYPE_CONFIG, body) is body


def test_the_hook_is_synchronous_rather_than_a_queued_task():
    """A Celery task registered on a `pre_` hook is dispatched, and its return
    value never reaches the payload - the resource stores exactly as it arrived."""
    assert not hasattr(plugin.automatic, "si")


# ---------------------------------------------------------------------------
# The routes, through the real routing stack
# ---------------------------------------------------------------------------


def test_the_settings_that_decide_which_types_are_numbered_are_not_anonymous():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from archihub.core.errors import register_exception_handlers
    from archihub.plugins.framework.mounting import build_plugin

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(build_plugin(plugin.SLUG).build())
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/CHRAutoIdent/settings/all").status_code == 401
    assert client.post("/CHRAutoIdent/settings", data={"data": "{}"}).status_code == 401
