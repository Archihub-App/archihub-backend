"""The bulk importer's parent lookup.

A row may name the resource it is filed under. The lookup must apply the same
visibility rule as the rest of the archive: a parent the importing user may not
see is reported as unresolved, never used.
"""

from __future__ import annotations

import pytest

massive = pytest.importorskip("archihub.plugins.massiveUpdater")

PARENT_ID = "507f1f77bcf86cd799439011"


class _Mongo:
    def __init__(self, resource):
        self.resource = resource

    def get_record(self, collection, filters=None, fields=None):
        return self.resource


@pytest.fixture
def world(monkeypatch):
    """A reserved parent, and a user holding the rights listed in ``state``."""
    from bson.objectid import ObjectId

    import archihub.api.resources.access as access
    import archihub.api.users.services as users

    state = {"rights": [], "admin": False}
    resource = {"_id": ObjectId(PARENT_ID), "post_type": "fondo", "accessRights": "internal"}
    monkeypatch.setattr(massive, "_mongo", lambda: _Mongo(resource))
    monkeypatch.setattr(access, "user_access_rights", lambda username: state["rights"])
    monkeypatch.setattr(access, "effective_access_right", lambda r: r.get("accessRights"))
    monkeypatch.setattr(users, "has_role", lambda username, role: role == "admin" and state["admin"])
    return state


def test_a_parent_the_user_may_not_see_is_unresolved(world):
    assert massive._resolve_parent({"parent": PARENT_ID}, "alice") is massive._UNRESOLVED


def test_a_parent_the_user_holds_the_right_to_is_resolved(world):
    world["rights"] = ["internal"]
    assert massive._resolve_parent({"parent": PARENT_ID}, "alice") == {
        "id": PARENT_ID,
        "post_type": "fondo",
    }


def test_an_admin_resolves_any_parent(world):
    world["admin"] = True
    assert massive._resolve_parent({"parent": PARENT_ID}, "root")["id"] == PARENT_ID


def test_a_row_without_a_parent_names_none(world):
    assert massive._resolve_parent({"title": "x"}, "alice") is None


# ---------------------------------------------------------------------------
# Updating an existing resource
# ---------------------------------------------------------------------------


def test_a_row_keeps_every_field_it_does_not_name():
    existing = {
        "post_type": "fondo",
        "status": "published",
        "metadata": {"firstLevel": {"title": "R", "description": "keep me"}},
        "accessRights": "secret",
        "parent": [{"id": PARENT_ID, "post_type": "fondo"}],
        "ident": "F-1",
    }

    body = massive._merged_body(existing, {"metadata": {"firstLevel": {"title": "R2"}}})

    assert body["metadata"] == {"firstLevel": {"title": "R2", "description": "keep me"}}
    assert body["accessRights"] == "secret"
    assert body["parent"] == existing["parent"]
    assert body["ident"] == "F-1"


def test_a_resource_row_goes_through_the_edit_route(monkeypatch):
    """So the importing user needs the same rights a user edit needs."""
    import pandas as pd
    from bson.objectid import ObjectId

    import archihub.api.resources.write as write
    import archihub.api.types.services as types_services

    stored = {"_id": ObjectId(PARENT_ID), "post_type": "fondo", "status": "published",
              "metadata": {"firstLevel": {"title": "R"}}}
    monkeypatch.setattr(massive, "_mongo", lambda: _Mongo(stored))
    monkeypatch.setattr(types_services, "get_metadata", lambda slug: {"fields": [
        {"destiny": "metadata.firstLevel.title", "type": "text"},
    ]})
    calls = []

    def refuse(resource_id, body, user):
        calls.append((resource_id, user))
        return {"msg": "denied"}, 403

    monkeypatch.setattr(write, "update", refuse)

    frame = pd.DataFrame([{"id": PARENT_ID, "metadata.firstLevel.title": "R2"}])
    applied, errors = [], []
    massive._import_resources(frame, "processor", False, applied, errors)

    assert calls == [(PARENT_ID, "processor")]
    assert applied == [] and errors[0]["error"] == "denied"


@pytest.mark.parametrize("sheet", ["Tipo", "Estandar", "Listado"])
def test_a_definition_sheet_needs_its_permission(monkeypatch, sheet):
    import pandas as pd

    import archihub.api.users.services as users

    class Workbook:
        sheet_names = [sheet]

    monkeypatch.setattr(pd, "ExcelFile", lambda path: Workbook())
    monkeypatch.setattr(massive, "_rows", lambda *a, **k: pd.DataFrame([{}]))
    monkeypatch.setattr(users, "has_permission", lambda user, point: False)

    with pytest.raises(PermissionError):
        massive._import_workbook("x.xlsx", "processor", False, [], [])


def test_an_unknown_type_slug_is_created_not_updated(monkeypatch):
    import pandas as pd

    import archihub.api.types.services as types_services
    import archihub.api.users.services as users

    calls = []
    monkeypatch.setattr(types_services, "get_by_slug", lambda slug: ({"msg": "nope"}, 404))
    monkeypatch.setattr(
        types_services, "create", lambda body, user: calls.append("create") or ({}, 201)
    )
    monkeypatch.setattr(
        types_services, "update_by_slug", lambda *a: calls.append("update") or ({}, 200)
    )
    monkeypatch.setattr(users, "has_permission", lambda user, point: True)

    applied, errors = [], []
    frame = pd.DataFrame([{"slug": "new-type", "name": "New"}])
    massive._import_types(frame, "admin", applied, errors)

    assert calls == ["create"] and errors == []


def test_a_form_field_keeps_its_full_definition():
    import json

    import pandas as pd

    field = {"destiny": "metadata.x", "type": "select", "list": "abc", "conditionals": [{"a": 1}]}
    row = pd.Series({"label": "X", "type": "select", "destiny": "metadata.x",
                     "definition": json.dumps(field)})

    assert massive._form_field(row) == field
