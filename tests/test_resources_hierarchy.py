"""Resource ancestry, parent validation, and the navigation tree.

These are the pieces the write path is about to be built on top of, and three of
them fix defects that are only visible once you exercise the graph rather than
read it. No database is required: a small in-memory stand-in for the Mongo
handler is enough, because every one of these functions is a query plus a
decision about its result.
"""

from __future__ import annotations

import pytest

from archihub.api.resources import hierarchy
from archihub.core.errors import ValidationError


class FakeMongo:
    """Enough of the Mongo handler to answer the hierarchy's queries.

    ``resources`` is keyed by id string; ``post_types`` by slug.
    """

    def __init__(self, resources=None, post_types=None):
        self.resources = resources or {}
        self.post_types = post_types or {}

    # -- helpers ---------------------------------------------------------

    def _resources(self):
        return [{**doc, "_id": rid} for rid, doc in self.resources.items()]

    def get_record(self, collection, filters=None, fields=None):
        rows = self.get_all_records(collection, filters)
        return rows[0] if rows else None

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "post_types":
            rows = [{**doc, "slug": slug} for slug, doc in self.post_types.items()]
        else:
            rows = self._resources()

        rows = [row for row in rows if _matches(row, filters or {})]

        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda r: _dig(r, key) or "", reverse=direction < 0)
        if skip:
            rows = rows[skip:]
        if limit:
            rows = rows[:limit]
        return rows

    def count(self, collection, filters=None):
        return len(self.get_all_records(collection, filters))


def _dig(doc, dotted):
    current = doc
    for key in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _matches(doc, filters):
    for key, expected in filters.items():
        if not _field_matches(doc, key, expected):
            return False
    return True


def _field_matches(doc, key, expected):
    # `parents.id` / `parent.id` must match any element of a list-valued field.
    head, _, tail = key.partition(".")
    value = doc.get(head)
    if tail and isinstance(value, list):
        return any(_compare(_dig(item, tail), expected) for item in value)
    if tail:
        return _compare(_dig(doc, key), expected)
    return _compare(value, expected)


def _compare(value, expected):
    if isinstance(expected, dict) and "$in" in expected:
        options = expected["$in"]
        # A whole-value match first: `parent: {'$in': [None, []]}` is asking
        # whether the field *is* empty, not whether it contains something.
        if any(value == option for option in options):
            return True
        if isinstance(value, list):
            return any(v in options for v in value)
        return False
    return value == expected


@pytest.fixture
def mongo(monkeypatch):
    """An in-memory stand-in, with readable ids.

    ``_to_object_id`` is bypassed so resources can be keyed ``"root"``/``"child"``
    instead of 24-character hex. Its real behaviour - rejecting a malformed id
    before any query runs - is covered separately below, against the unpatched
    function.
    """
    fake = FakeMongo()
    monkeypatch.setattr(hierarchy, "_mongo", lambda: fake)
    monkeypatch.setattr(hierarchy, "_to_object_id", lambda value: value)
    return fake


def test_a_malformed_id_never_reaches_the_database():
    """Checked against the real converter, which the fixture above replaces."""
    from archihub.api.resources.hierarchy import _to_object_id

    assert _to_object_id("not-an-object-id") is None
    assert _to_object_id("507f1f77bcf86cd799439011") is not None


@pytest.fixture(autouse=True)
def roles(monkeypatch):
    """Default identity: an ordinary user who is not an administrator."""
    import archihub.api.users.services as users

    monkeypatch.setattr(users, "has_role", lambda username, role: False)
    return users


# ---------------------------------------------------------------------------
# Ancestry
# ---------------------------------------------------------------------------


def test_a_resource_with_no_parent_has_no_ancestors(mongo):
    mongo.resources = {"a": {"parent": []}}
    assert hierarchy.ancestors("a") == []


def test_a_parent_stored_as_a_dict_is_normalised_to_a_list(mongo):
    """Both spellings exist in stored data."""
    mongo.resources = {"a": {"parent": {"id": "b"}}, "b": {"parent": []}}
    assert hierarchy.direct_parents("a") == [{"id": "b"}]


def test_ancestors_are_returned_nearest_first_with_their_level(mongo):
    mongo.resources = {
        "a": {"parent": [{"id": "b"}]},
        "b": {"parent": [{"id": "c"}]},
        "c": {"parent": []},
    }
    result = hierarchy.ancestors("a")
    assert [(entry["id"], entry["level"]) for entry in result] == [("b", 1), ("c", 2)]


def test_a_cycle_does_not_recurse_forever(mongo):
    """THE bug (S15).

    The original walked each parent with no memory of where it had been, so a
    two-node cycle raised RecursionError - and every read that resolves a
    breadcrumb through either resource failed permanently, for every user.
    """
    mongo.resources = {"a": {"parent": [{"id": "b"}]}, "b": {"parent": [{"id": "a"}]}}

    result = hierarchy.ancestors("a")

    assert [entry["id"] for entry in result] == ["b"]


def test_a_longer_cycle_also_terminates(mongo):
    mongo.resources = {
        "a": {"parent": [{"id": "b"}]},
        "b": {"parent": [{"id": "c"}]},
        "c": {"parent": [{"id": "a"}]},
    }
    assert {entry["id"] for entry in hierarchy.ancestors("a")} == {"b", "c"}


def test_a_resource_is_never_its_own_ancestor(mongo):
    """A self-parent is dropped rather than recorded, so no breadcrumb ever
    contains the page it is a breadcrumb for."""
    mongo.resources = {"a": {"parent": [{"id": "a"}]}}
    assert hierarchy.ancestors("a") == []


def test_depth_is_capped_even_without_a_repeat(mongo, monkeypatch):
    """The visited set catches cycles; the ceiling catches pathological depth."""
    monkeypatch.setattr(hierarchy, "MAX_ANCESTRY_DEPTH", 5)
    mongo.resources = {str(i): {"parent": [{"id": str(i + 1)}]} for i in range(50)}

    assert len(hierarchy.ancestors("0")) == 5


def test_an_ancestor_reached_twice_appears_once(mongo):
    """A diamond: two parents sharing a grandparent."""
    mongo.resources = {
        "a": {"parent": [{"id": "b"}, {"id": "c"}]},
        "b": {"parent": [{"id": "d"}]},
        "c": {"parent": [{"id": "d"}]},
        "d": {"parent": []},
    }
    result = hierarchy.ancestors("a")

    assert [entry["id"] for entry in result] == ["b", "c", "d"]
    assert result[-1]["parentOf"] == ["b", "c"]


# ---------------------------------------------------------------------------
# has_changed_parent
# ---------------------------------------------------------------------------


def test_reordering_the_parents_is_not_a_move(mongo):
    """Otherwise every save rewrites every descendant for nothing."""
    mongo.resources = {"a": {"parent": [{"id": "b"}, {"id": "c"}]}}
    assert hierarchy.has_changed_parent("a", [{"id": "c"}, {"id": "b"}]) is False


def test_adding_a_parent_is_a_move(mongo):
    mongo.resources = {"a": {"parent": [{"id": "b"}]}}
    assert hierarchy.has_changed_parent("a", [{"id": "b"}, {"id": "c"}]) is True


def test_clearing_the_parent_is_a_move(mongo):
    mongo.resources = {"a": {"parent": [{"id": "b"}]}}
    assert hierarchy.has_changed_parent("a", []) is True


def test_staying_at_the_top_level_is_not_a_move(mongo):
    mongo.resources = {"a": {"parent": []}}
    assert hierarchy.has_changed_parent("a", []) is False


# ---------------------------------------------------------------------------
# validate_parent
# ---------------------------------------------------------------------------


@pytest.fixture
def hierarchical_types(mongo, monkeypatch):
    mongo.post_types = {
        "fondo": {"hierarchical": True, "parentType": []},
        "serie": {"hierarchical": False, "parentType": [{"id": "fondo"}]},
        "foto": {"hierarchical": False, "parentType": []},
    }

    import archihub.api.types.services as types

    monkeypatch.setattr(
        types,
        "is_hierarchical",
        lambda slug: (mongo.post_types.get(slug, {}).get("hierarchical", False), True)
        if slug in mongo.post_types
        else ({"msg": "nope"}, 404),
    )
    return mongo


def test_no_parent_yields_empty_parent_and_parents(hierarchical_types):
    body = hierarchy.validate_parent({"post_type": "serie"})
    assert body["parent"] == []
    assert body["parents"] == []


def test_the_transitive_closure_is_recorded(hierarchical_types):
    hierarchical_types.resources = {
        "root": {"parent": [], "post_type": "fondo"},
        "mid": {"parent": [{"id": "root", "post_type": "fondo"}], "post_type": "fondo"},
    }
    body = hierarchy.validate_parent(
        {"post_type": "serie", "parent": [{"id": "mid", "post_type": "fondo"}]}
    )

    assert [p["id"] for p in body["parent"]] == ["mid"]
    assert [p["id"] for p in body["parents"]] == ["mid", "root"]


def test_naming_both_a_parent_and_its_own_ancestor_keeps_only_the_nearest(hierarchical_types):
    """Otherwise the resource shows up twice in its own breadcrumb."""
    hierarchical_types.resources = {
        "root": {"parent": [], "post_type": "fondo"},
        "mid": {"parent": [{"id": "root", "post_type": "fondo"}], "post_type": "fondo"},
    }
    body = hierarchy.validate_parent(
        {
            "post_type": "serie",
            "parent": [{"id": "mid", "post_type": "fondo"}, {"id": "root", "post_type": "fondo"}],
        }
    )

    assert [p["id"] for p in body["parent"]] == ["mid"]


def test_a_resource_may_not_be_its_own_parent(hierarchical_types):
    with pytest.raises(ValidationError):
        hierarchy.validate_parent(
            {"_id": "a", "post_type": "serie", "parent": [{"id": "a", "post_type": "fondo"}]},
            update=True,
        )


def test_a_resource_may_not_be_placed_under_its_own_descendant(hierarchical_types):
    """THE cycle-creation half of S15.

    The original refused only a resource naming itself, so naming a child - or
    any deeper descendant - was accepted, and that is precisely what produced
    the unbounded recursion above. Nothing else in the system rejected it later.
    """
    hierarchical_types.resources = {
        "a": {"parent": [], "post_type": "fondo"},
        "child": {"parent": [{"id": "a", "post_type": "fondo"}], "post_type": "fondo"},
    }

    with pytest.raises(ValidationError):
        hierarchy.validate_parent(
            {"_id": "a", "post_type": "fondo", "parent": [{"id": "child", "post_type": "fondo"}]},
            update=True,
        )


def test_a_grandchild_is_refused_too(hierarchical_types):
    hierarchical_types.resources = {
        "a": {"parent": [], "post_type": "fondo"},
        "child": {"parent": [{"id": "a", "post_type": "fondo"}], "post_type": "fondo"},
        "grandchild": {"parent": [{"id": "child", "post_type": "fondo"}], "post_type": "fondo"},
    }

    with pytest.raises(ValidationError):
        hierarchy.validate_parent(
            {
                "_id": "a",
                "post_type": "fondo",
                "parent": [{"id": "grandchild", "post_type": "fondo"}],
            },
            update=True,
        )


def test_a_same_type_parent_requires_a_hierarchical_type(hierarchical_types):
    hierarchical_types.resources = {"p": {"parent": [], "post_type": "serie"}}

    with pytest.raises(ValidationError):
        hierarchy.validate_parent(
            {"post_type": "serie", "parent": [{"id": "p", "post_type": "serie"}]}
        )


def test_a_same_type_parent_is_allowed_when_the_type_is_hierarchical(hierarchical_types):
    hierarchical_types.resources = {"p": {"parent": [], "post_type": "fondo"}}

    body = hierarchy.validate_parent(
        {"post_type": "fondo", "parent": [{"id": "p", "post_type": "fondo"}]}
    )
    assert [p["id"] for p in body["parent"]] == ["p"]


def test_a_parent_of_an_undeclared_type_is_refused(hierarchical_types):
    """F20.

    ``serie`` declares ``fondo`` as its only acceptable parent. Placing one
    under a ``foto`` was accepted by the original: the check ran only when
    parent and child shared a type, and the branch that would have caught this
    was unreachable.
    """
    hierarchical_types.resources = {"p": {"parent": [], "post_type": "foto"}}

    with pytest.raises(ValidationError):
        hierarchy.validate_parent(
            {"post_type": "serie", "parent": [{"id": "p", "post_type": "foto"}]}
        )


def test_a_type_declaring_no_parents_stays_unconstrained(hierarchical_types):
    """An empty allowlist means "unspecified", not "nothing".

    Existing instances have types with no ``parentType`` at all. Reading that as
    a prohibition would make every save of those resources start failing the
    moment this check began working.
    """
    hierarchical_types.resources = {"p": {"parent": [], "post_type": "fondo"}}

    body = hierarchy.validate_parent(
        {"post_type": "foto", "parent": [{"id": "p", "post_type": "fondo"}]}
    )
    assert [p["id"] for p in body["parent"]] == ["p"]


def test_a_hierarchical_allowlist_entry_accepts_any_type(hierarchical_types):
    hierarchical_types.post_types["serie"]["parentType"] = [{"id": "otro", "hierarchical": True}]
    hierarchical_types.resources = {"p": {"parent": [], "post_type": "foto"}}

    body = hierarchy.validate_parent(
        {"post_type": "serie", "parent": [{"id": "p", "post_type": "foto"}]}
    )
    assert [p["id"] for p in body["parent"]] == ["p"]


def test_a_parent_that_does_not_exist_is_refused(hierarchical_types):
    with pytest.raises(ValidationError):
        hierarchy.validate_parent({"post_type": "serie", "parent": [{"id": "507f1f77bcf86cd799439011"}]})


def test_a_parent_entry_without_an_id_clears_the_whole_set(hierarchical_types):
    """Matches the original: an unusable payload does not half-apply."""
    body = hierarchy.validate_parent({"post_type": "serie", "parent": [{"post_type": "fondo"}]})
    assert body["parent"] == []
    assert body["parents"] == []


# ---------------------------------------------------------------------------
# visible_type_slugs
# ---------------------------------------------------------------------------


def test_a_type_without_view_roles_is_visible_to_everyone(mongo):
    mongo.post_types = {"foto": {}}
    assert hierarchy.visible_type_slugs("alice", ["foto"]) == ["foto"]


def test_a_restricted_type_is_hidden_without_the_role(mongo):
    mongo.post_types = {"foto": {"viewRoles": ["curator"]}}
    assert hierarchy.visible_type_slugs("alice", ["foto"]) == []


def test_a_restricted_type_is_visible_with_the_role(mongo, monkeypatch):
    import archihub.api.users.services as users

    mongo.post_types = {"foto": {"viewRoles": ["curator"]}}
    monkeypatch.setattr(users, "has_role", lambda u, r: r == "curator")

    assert hierarchy.visible_type_slugs("alice", ["foto"]) == ["foto"]


def test_an_admin_sees_every_type(mongo, monkeypatch):
    import archihub.api.users.services as users

    mongo.post_types = {"foto": {"viewRoles": ["curator"]}}
    monkeypatch.setattr(users, "has_role", lambda u, r: r == "admin")

    assert hierarchy.visible_type_slugs("alice", ["foto"]) == ["foto"]


def test_holding_several_of_a_types_roles_does_not_repeat_it(mongo, monkeypatch):
    """The original appended the slug once per matching role, so the same type
    entered the query several times."""
    import archihub.api.users.services as users

    mongo.post_types = {"foto": {"viewRoles": ["a", "b", "c"]}}
    monkeypatch.setattr(users, "has_role", lambda u, r: r in {"a", "b", "c"})

    assert hierarchy.visible_type_slugs("alice", ["foto"]) == ["foto"]


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------


@pytest.fixture
def tree_data(mongo):
    mongo.post_types = {
        "fondo": {"name": "Fondo", "icon": "folder"},
        "foto": {"name": "Foto", "icon": "image"},
    }
    mongo.resources = {
        "root": {
            "post_type": "fondo",
            "parent": [],
            "status": "published",
            "metadata": {"firstLevel": {"title": "Archivo"}},
        },
        "child": {
            "post_type": "foto",
            "parent": [{"id": "root"}],
            "parents": [{"id": "root", "post_type": "fondo"}],
            "status": "published",
            "metadata": {"firstLevel": {"title": "Una foto"}},
        },
    }
    return mongo


def test_the_top_level_carries_its_type_name_and_icon(tree_data):
    nodes, status = hierarchy.get_tree("all", ["fondo", "foto"], "alice")

    assert status == 200
    assert nodes == [
        {
            "id": "root",
            "name": "Archivo",
            "post_type": "fondo",
            "children": True,
            "icon": "folder",
            "type": "Fondo",
        }
    ]


def test_a_leaf_is_marked_as_having_no_children(tree_data):
    nodes, _status = hierarchy.get_tree("root", ["fondo", "foto"], "alice")
    assert [(n["id"], n["children"]) for n in nodes] == [("child", False)]


def test_a_resource_with_no_title_does_not_take_the_level_down_with_it(tree_data):
    """The original subscripted straight through firstLevel.title."""
    tree_data.resources["broken"] = {
        "post_type": "fondo",
        "parent": [],
        "status": "published",
        "metadata": {},
    }
    nodes, status = hierarchy.get_tree("all", ["fondo", "foto"], "alice")

    assert status == 200
    assert len(nodes) == 2


def test_a_caller_with_no_visible_types_gets_an_empty_level(tree_data):
    assert hierarchy.get_tree("all", [], "alice") == ([], 200)


def test_the_recycle_bin_is_refused_to_non_admins(tree_data):
    """Refused with the legacy status, not the 403 this really is - the frontend
    compares the code exactly, so both sides flip together or neither does."""
    payload, status = hierarchy.get_tree("all", ["fondo"], "alice", status="deleted")
    assert status == hierarchy.ROLE_FAILURE_STATUS
    assert "msg" in payload


def test_an_admin_may_browse_the_recycle_bin(tree_data, monkeypatch):
    import archihub.api.users.services as users

    monkeypatch.setattr(users, "has_role", lambda u, r: r == "admin")
    tree_data.resources["gone"] = {
        "post_type": "fondo",
        "parent": [],
        "status": "deleted",
        "metadata": {"firstLevel": {"title": "Borrado"}},
    }

    nodes, status = hierarchy.get_tree("all", ["fondo"], "admin", status="deleted")
    assert status == 200
    assert [n["id"] for n in nodes] == ["gone"]


def test_draft_mode_shows_the_published_structure_around_the_draft(tree_data):
    """Otherwise there is nothing to file the draft under."""
    tree_data.resources["wip"] = {
        "post_type": "fondo",
        "parent": [],
        "status": "draft",
        "metadata": {"firstLevel": {"title": "Borrador"}},
    }
    nodes, _status = hierarchy.get_tree("all", ["fondo"], "alice", status="draft")

    assert {n["id"] for n in nodes} == {"root", "wip"}


def test_a_folder_whose_children_are_published_is_expandable_in_draft_mode(tree_data):
    """F21.

    The level query matched drafts *and* published resources while the
    has-children probe matched drafts only, so a folder holding nothing but
    published children was drawn as a leaf - unopenable, with its contents
    unreachable from the draft view.
    """
    nodes, _status = hierarchy.get_tree("all", ["fondo", "foto"], "alice", status="draft")

    assert [(n["id"], n["children"]) for n in nodes] == [("root", True)]


def test_an_unknown_status_is_rejected(tree_data):
    payload, status = hierarchy.get_tree("all", ["fondo"], "alice", status="whatever")
    assert status == 400
    assert "msg" in payload


def test_a_level_is_paginated_when_a_page_is_given(tree_data):
    for index in range(15):
        tree_data.resources[f"n{index:02d}"] = {
            "post_type": "fondo",
            "parent": [],
            "status": "published",
            "metadata": {"firstLevel": {"title": f"Item {index:02d}"}},
        }

    first, _status = hierarchy.get_tree("all", ["fondo"], "alice", page=0)
    second, _status = hierarchy.get_tree("all", ["fondo"], "alice", page=1)

    assert len(first) == hierarchy.TREE_PAGE_SIZE
    assert {n["id"] for n in first} & {n["id"] for n in second} == set()
