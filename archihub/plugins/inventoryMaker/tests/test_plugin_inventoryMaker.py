"""Who an inventory export is for decides what goes in it."""

from __future__ import annotations

from bson.objectid import ObjectId

from archihub.plugins import inventoryMaker
from archihub.plugins.inventoryMaker import export

PARENT_ID = "6a70b833497d4440325c94b1"


class FakeMongo:
    def __init__(self, resources=None, records=None):
        self.resources = resources or []
        self.records = records or []
        self.queries: list[tuple[str, dict, int]] = []

    def get_record(self, collection, filters, fields=None):
        for row in self.resources:
            if row["_id"] == filters.get("_id"):
                return dict(row)
        return None

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        self.queries.append((collection, filters, limit))
        return [dict(r) for r in (self.resources if collection == "resources" else self.records)]


def _stub_roles(monkeypatch, roles: set[str]):
    monkeypatch.setattr(
        "archihub.api.users.services.has_role", lambda user, role: role in roles
    )


def test_an_authenticated_export_applies_the_listing_access_rule(monkeypatch):
    _stub_roles(monkeypatch, set())
    monkeypatch.setattr(
        "archihub.api.resources.access.access_rights_clause", lambda user: {"rights": user}
    )
    monkeypatch.setattr(
        "archihub.api.resources.access.may_see_all_drafts", lambda *a: False
    )

    filters = inventoryMaker._selection_filters({"post_type": ["fondo"]}, "alice")
    assert filters["$and"] == [{"rights": "alice"}]

    drafts = inventoryMaker._selection_filters({"post_type": ["fondo"], "status": "draft"}, "alice")
    assert all(branch["createdBy"] == "alice" for branch in drafts["$or"])


def test_exporting_under_a_parent_checks_that_parent(monkeypatch):
    """The parent's effective right is read from the parent document."""
    _stub_roles(monkeypatch, set())
    monkeypatch.setattr(inventoryMaker, "_mongo", lambda: FakeMongo(
        resources=[{"_id": ObjectId(PARENT_ID), "accessRights": "reserved"}]
    ))
    monkeypatch.setattr(
        "archihub.api.resources.access.may_view_resource",
        lambda user, resource, is_admin: resource.get("accessRights") is None,
    )

    refusal = inventoryMaker._may_export({"post_type": [], "parent": {"id": PARENT_ID}}, "alice")

    assert refusal is not None and refusal[1] == 403


def test_a_restricted_field_is_not_a_column_of_a_public_export(monkeypatch):
    fake = FakeMongo(resources=[{
        "_id": ObjectId(PARENT_ID),
        "post_type": "fondo",
        "metadata": {"title": "Visible", "secret": "Withheld"},
    }])
    monkeypatch.setattr(inventoryMaker, "_mongo", lambda: fake)
    monkeypatch.setattr(export, "metadata_fields", lambda post_types: [
        {"destiny": "metadata.title", "label": "Title", "type": "text"},
        {"destiny": "metadata.secret", "label": "Secret", "type": "text", "accessRights": ["r"]},
    ])
    monkeypatch.setattr(export, "option_term_lookup", lambda: (lambda ids: {}))

    rows, _records = inventoryMaker._resource_rows({}, None)

    assert "Secret" not in rows[1]
    assert rows[1]["Title"] == "Visible"
    assert fake.queries[0][2] == inventoryMaker.MAX_EXPORT_ROWS
