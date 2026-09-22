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
