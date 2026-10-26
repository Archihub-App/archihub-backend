"""Permission points: built-in roles always pass, configured roles are added.

Three things are held here. The catalogue stays closed and keeps the
administrator-only actions out of it. Every point passes exactly its built-in roles
when nothing is configured. And a role added
to a point really does pass the check it names, at the route and in the rules
that sit inside the services.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi.testclient import TestClient

from archihub.api.users import services as users
from archihub.core import permissions
from archihub.core.permissions import POINTS
from archihub.core.roles import BUILTIN_ROLES
from archihub.core.security.jwt import CurrentUser, get_current_user, require_permission

ROOT = pathlib.Path(__file__).resolve().parent.parent / "archihub"
CUSTOM = "curator"


@pytest.fixture
def held(monkeypatch) -> set[str]:
    """The roles the caller appears to hold. ``curator`` is a configured role."""
    granted: set[str] = set()
    monkeypatch.setattr(users, "has_role", lambda username, role: role in granted)
    monkeypatch.setattr(
        "archihub.core.roles.get_roles",
        lambda: {"options": [{"id": r} for r in (*BUILTIN_ROLES, CUSTOM)]},
    )
    return granted


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def _source_strings() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        if "tests" in path.relative_to(ROOT).parts or path.name == "permissions.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


def test_every_point_is_checked_somewhere():
    """A point nothing checks would let an administrator configure a no-op."""
    unused = set(POINTS) - _source_strings()
    assert not unused, f"points defined but never checked: {sorted(unused)}"


def test_built_in_roles_are_real_roles():
    for point in POINTS.values():
        assert set(point.default_roles) <= set(BUILTIN_ROLES), point.id


def test_a_point_without_built_in_roles_says_what_its_rule_is():
    for point in POINTS.values():
        if not point.default_roles:
            assert point.builtin_rule, point.id


def test_an_unknown_point_fails_loudly():
    with pytest.raises(KeyError):
        users.has_permission("someone", "resources.typo")
    with pytest.raises(KeyError):
        require_permission("resources.typo")


# ---------------------------------------------------------------------------
# What stays with administrators
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    from archihub.core.app_factory import create_app

    return create_app()


def _points_of(dependant) -> set[str]:
    found = set()
    point = getattr(dependant.call, "permission_point", None)
    if point:
        found.add(point)
    for child in dependant.dependencies:
        found |= _points_of(child)
    return found


#: Path prefixes whose routes administer accounts or the instance itself. None
#: of them may be reachable through a permission point.
ADMINISTRATION = (
    "/users", "/logs", "/tasks", "/adminApi",
    "/system/permission-points", "/system/role-mappings",
)


def _changes_a_provider(path: str, methods: set[str]) -> bool:
    """Using a provider is a point; configuring one, or its credentials, is not."""
    if not path.startswith("/aiservices/providers"):
        return False
    if path.endswith("/chat"):
        return False
    return bool(methods - {"GET"}) or path.endswith("/check")


def test_no_administration_route_is_behind_a_permission_point(app):
    from archihub.core.routing import iter_api_routes

    offenders = []
    for path, route in iter_api_routes(app):
        points = _points_of(route.dependant)
        if not points:
            continue
        if path.startswith(ADMINISTRATION) or "/settings" in path:
            offenders.append(path)
        elif _changes_a_provider(path, route.methods):
            offenders.append(path)
        elif path.startswith("/system") and path != "/system/access-rights":
            offenders.append(path)
        elif path == "/records" and "POST" in route.methods:
            offenders.append(path)
    assert not offenders, offenders


def test_the_guard_sees_permission_points(app):
    """A walk that finds nothing would pass the test above vacuously."""
    from archihub.core.routing import iter_api_routes

    gated = {path for path, route in iter_api_routes(app) if _points_of(route.dependant)}
    assert "/forms" in gated
    assert "/system/access-rights" in gated


@pytest.mark.parametrize(
    "point_id",
    ["users.manage", "system.settings", "admin", "logs.read", "records.filter_search"],
)
def test_an_administration_action_cannot_be_mapped(held, point_id):
    from archihub.api.system import role_mappings

    payload, status = role_mappings.update_role_mappings(
        {"mappings": {point_id: [CUSTOM]}}, "root"
    )
    assert status == 400
    assert point_id not in POINTS


# ---------------------------------------------------------------------------
# has_permission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("point", [p for p in POINTS.values() if p.default_roles], ids=str)
def test_built_in_roles_pass_with_nothing_configured(held, point):
    for role in point.default_roles:
        held.clear()
        held.add(role)
        assert users.has_permission("someone", point.id) is True


@pytest.mark.parametrize("point_id", sorted(POINTS))
def test_a_configured_role_passes_and_only_once_configured(held, role_mappings, point_id):
    held.add(CUSTOM)
    assert users.has_permission("someone", point_id) is False

    role_mappings[point_id] = [CUSTOM]
    assert users.has_permission("someone", point_id) is True


def test_configuring_a_point_never_removes_a_built_in_role(held, role_mappings):
    role_mappings["forms.manage"] = [CUSTOM]
    held.add("admin")
    assert users.has_permission("someone", "forms.manage") is True


def test_a_role_configured_on_one_point_does_not_reach_another(held, role_mappings):
    role_mappings["forms.manage"] = [CUSTOM]
    held.add(CUSTOM)
    assert users.has_permission("someone", "lists.manage") is False


def test_a_malformed_document_leaves_only_the_built_in_roles(held, monkeypatch):
    class Broken:
        def get_record(self, *args, **kwargs):
            return {"name": "role_mappings", "data": {"forms.manage": "curator", "nope": [1]}}

    monkeypatch.setattr(permissions, "_mongo", lambda: Broken())
    held.add(CUSTOM)
    assert users.has_permission("someone", "forms.manage") is False
    held.add("admin")
    assert users.has_permission("someone", "forms.manage") is True


def test_an_unreadable_document_leaves_only_the_built_in_roles(held, monkeypatch):
    class Down:
        def get_record(self, *args, **kwargs):
            raise ConnectionError("mongodb://admin:secret@db:27017 unreachable")

    monkeypatch.setattr(permissions, "_mongo", lambda: Down())
    held.add("editor")
    assert users.has_permission("someone", "lists.manage") is True


# ---------------------------------------------------------------------------
# The rules inside the services
# ---------------------------------------------------------------------------


def test_recycle_bin(held, role_mappings):
    from archihub.api.resources import access

    held.add(CUSTOM)
    assert access.may_see_deleted("someone", is_admin=False) is False
    role_mappings["resources.see_deleted"] = [CUSTOM]
    assert access.may_see_deleted("someone", is_admin=False) is True
    assert access.may_see_deleted(None, is_admin=False) is False


def test_other_peoples_drafts_keep_the_built_in_rule(held, role_mappings):
    from archihub.api.resources import access

    assert access.may_see_all_drafts("someone", is_publisher=True, is_admin=True) is True
    assert access.may_see_all_drafts("someone", is_publisher=True, is_admin=False) is False
    assert access.may_see_all_drafts("someone", is_publisher=False, is_admin=True) is False

    held.add(CUSTOM)
    role_mappings["resources.see_all_drafts"] = [CUSTOM]
    assert access.may_see_all_drafts("someone", is_publisher=False, is_admin=False) is True


def test_the_draft_listing_narrows_to_the_caller_without_the_permission(held, role_mappings):
    from archihub.api.resources import access

    held.add(CUSTOM)
    filters, _ = access.build_listing_filters(
        {}, username="someone", is_admin=False, is_publisher=False, status="draft"
    )
    assert all(branch.get("createdBy") == "someone" for branch in filters["$or"])

    role_mappings["resources.see_all_drafts"] = [CUSTOM]
    filters, _ = access.build_listing_filters(
        {}, username="someone", is_admin=False, is_publisher=False, status="draft"
    )
    assert not any("createdBy" in branch for branch in filters["$or"])


def test_super_edit(held, role_mappings):
    from archihub.api.resources import access

    theirs = {"createdBy": "someone-else"}
    held.add(CUSTOM)
    assert access.owns_or_supervises("someone", theirs, is_admin=False) is False
    role_mappings["resources.super_edit"] = [CUSTOM]
    assert access.owns_or_supervises("someone", theirs, is_admin=False) is True


def test_publish(held, role_mappings):
    from archihub.api.resources import write

    held.add(CUSTOM)
    assert write.may_publish("someone", is_admin=False) is False
    role_mappings["resources.publish"] = [CUSTOM]
    assert write.may_publish("someone", is_admin=False) is True


def test_search_recycle_bin(held, role_mappings):
    from archihub.api.search import services

    held.add(CUSTOM)
    assert services._may_see_deleted("someone") is False
    role_mappings["resources.see_deleted"] = [CUSTOM]
    assert services._may_see_deleted("someone") is True
    assert services._may_see_deleted(None) is False


def test_tree_recycle_bin(held, role_mappings):
    from archihub.api.resources import hierarchy
    from archihub.core.errors import PermissionDeniedError

    held.add(CUSTOM)
    with pytest.raises(PermissionDeniedError):
        hierarchy._status_filter("deleted", "someone", False)
    role_mappings["resources.see_deleted"] = [CUSTOM]
    assert hierarchy._status_filter("deleted", "someone", False) == "deleted"


def test_block_editing(held, role_mappings, monkeypatch):
    from archihub.api.records import blocks

    monkeypatch.setattr(blocks.access, "may_view_record", lambda *a: True)
    held.add(CUSTOM)
    assert blocks.may_edit("someone", {}, False) is False
    role_mappings["records.edit_blocks"] = [CUSTOM]
    assert blocks.may_edit("someone", {}, False) is True


class _Tasks:
    def __init__(self, open_task: bool) -> None:
        self.open_task = open_task

    def get_record(self, collection, filters, fields=None):
        assert collection == "usertasks"
        return {"_id": 1} if self.open_task else None


@pytest.mark.parametrize("open_task", [True, False])
def test_transcription_built_in_roles_are_unchanged(held, monkeypatch, open_task):
    from archihub.api.records import transcription

    monkeypatch.setattr(transcription, "_mongo", lambda: _Tasks(open_task))
    for roles, expected in [
        ({"admin"}, True),
        ({"team_lead"}, True),
        ({"editor"}, True),
        ({"transcriber"}, open_task),
        ({"editor", "transcriber"}, open_task),
        ({"user"}, False),
    ]:
        held.clear()
        held.update(roles)
        assert transcription.may_edit("rec", "someone") is expected, roles


@pytest.mark.parametrize("open_task", [True, False])
def test_transcription_configured_roles(held, role_mappings, monkeypatch, open_task):
    from archihub.api.records import transcription

    monkeypatch.setattr(transcription, "_mongo", lambda: _Tasks(open_task))
    held.add(CUSTOM)
    assert transcription.may_edit("rec", "someone") is False

    role_mappings["records.transcribe"] = [CUSTOM]
    assert transcription.may_edit("rec", "someone") is open_task

    role_mappings["records.transcribe_any"] = [CUSTOM]
    assert transcription.may_edit("rec", "someone") is True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="someone", claims={})
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_a_configured_role_opens_the_route(held, role_mappings, client, monkeypatch):
    from archihub.api.forms import services as forms

    monkeypatch.setattr(forms, "get_all", lambda: ({"forms": []}, 200))
    held.add(CUSTOM)
    assert client.get("/forms").status_code == 403

    role_mappings["forms.manage"] = [CUSTOM]
    assert client.get("/forms").status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/system/permission-points"), ("get", "/system/role-mappings"),
     ("put", "/system/role-mappings")],
)
def test_the_mapping_routes_are_for_administrators_only(held, client, method, path):
    held.update(BUILTIN_ROLES)
    held.discard("admin")
    kwargs = {"json": {"mappings": {"forms.manage": []}}} if method == "put" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_the_catalogue_route(held, client):
    held.add("admin")
    body = client.get("/system/permission-points").json()
    ids = [point["id"] for point in body["points"]]
    assert ids == list(POINTS)
    deleted = next(p for p in body["points"] if p["id"] == "resources.see_deleted")
    assert deleted["defaultRoles"] == ["admin"]


def test_reading_and_updating_the_mappings(held, role_mappings, client, audit_log):
    held.add("admin")
    assert client.get("/system/role-mappings").json()["mappings"]["forms.manage"] == []

    response = client.put(
        "/system/role-mappings",
        json={"mappings": {"forms.manage": [CUSTOM, CUSTOM], "lists.manage": [CUSTOM]}},
    )
    assert response.status_code == 200
    assert role_mappings == {"forms.manage": [CUSTOM], "lists.manage": [CUSTOM]}

    # Points not named keep their roles; an empty list removes them.
    client.put("/system/role-mappings", json={"mappings": {"forms.manage": []}})
    assert role_mappings == {"lists.manage": [CUSTOM]}

    entries = [e for e in audit_log if e["action"] == "ROLE_MAPPINGS_UPDATE"]
    assert len(entries) == 2
    assert entries[0]["metadata"]["mappings"] == {
        "forms.manage": [CUSTOM], "lists.manage": [CUSTOM]
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"mappings": {}},
        {"mappings": ["forms.manage"]},
        {"mappings": {"forms.manage": CUSTOM}},
        {"mappings": {"forms.manage": [1]}},
        {"mappings": {"forms.manage": ["no-such-role"]}},
        {"mappings": {"forms.manage": [CUSTOM], "no.such.point": [CUSTOM]}},
        {"mappings": {"forms.manage": ["editor"]}},
        {"mappings": {"forms.manage": [CUSTOM, "admin"]}},
    ],
)
def test_an_invalid_update_writes_nothing(held, role_mappings, client, body):
    held.add("admin")
    role_mappings["views.manage"] = [CUSTOM]

    assert client.put("/system/role-mappings", json=body).status_code == 400
    assert role_mappings == {"views.manage": [CUSTOM]}


def test_the_settings_screen_cannot_read_or_write_the_mappings():
    from archihub.api.system import services

    assert permissions.MAPPINGS_SETTING in services._HIDDEN_SETTINGS


# ---------------------------------------------------------------------------
# Role administration stays out of reach of added roles
# ---------------------------------------------------------------------------


@pytest.fixture
def vocabularies(monkeypatch):
    """The roles and access-rights lists' ids, and list writes that always succeed."""
    from archihub.api.lists import services as lists

    monkeypatch.setattr("archihub.core.roles.get_roles_id", lambda: "roles-list")
    monkeypatch.setattr("archihub.core.roles.get_access_rights_id", lambda: "rights-list")
    monkeypatch.setattr(lists, "update_by_id", lambda *a: ({"msg": "updated"}, 200))
    monkeypatch.setattr(lists, "delete_by_id", lambda *a: ({"msg": "deleted"}, 200))


def _write(client, method: str, list_id: str) -> int:
    if method == "put":
        return client.put(f"/lists/{list_id}", json={"name": "x"}).status_code
    return client.delete(f"/lists/{list_id}").status_code


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize("list_id", ["roles-list", "rights-list"])
def test_an_added_role_cannot_change_the_authorisation_vocabularies(
    held, role_mappings, client, vocabularies, method, list_id
):
    held.add(CUSTOM)
    role_mappings["lists.manage"] = [CUSTOM]

    assert _write(client, method, list_id) == 403
    assert _write(client, method, "another-list") == 200


@pytest.mark.parametrize("method", ["put", "delete"])
def test_built_in_list_managers_keep_the_authorisation_vocabularies(
    held, client, vocabularies, method
):
    held.add("editor")
    assert _write(client, method, "roles-list") == 200


def test_a_built_in_role_stored_by_hand_is_ignored(held, role_mappings):
    """Only custom roles extend a point, however the document came to hold one."""
    role_mappings["forms.manage"] = ["editor", CUSTOM]
    held.add("editor")
    assert users.has_permission("someone", "forms.manage") is False
    held.add(CUSTOM)
    assert users.has_permission("someone", "forms.manage") is True
