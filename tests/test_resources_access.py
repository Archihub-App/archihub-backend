"""Who may see which resources.

The archive's main read path. Every test here is an access-control boundary, so
a change that makes one fail is either a deliberate policy decision or a leak.
"""

from __future__ import annotations

import pytest

from archihub.api.resources import access

_REAL_RESTRICTED_ANCESTORS = access.restricted_ancestors


class FakeMongo:
    def __init__(self, rights=None):
        self.rights = rights

    def get_record(self, collection, filters, fields=None):
        return {"accessRights": self.rights} if self.rights is not None else None


@pytest.fixture(autouse=True)
def no_restricted_folders(monkeypatch):
    """By default nothing is filed under a restricted resource."""
    monkeypatch.setattr(access, "restricted_ancestors", lambda: [])


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo(rights=["public", "internal"])
    monkeypatch.setattr(access, "_mongo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Access rights
# ---------------------------------------------------------------------------


def test_a_non_admin_query_is_constrained_by_access_rights(mongo):
    filters, error = access.build_listing_filters(
        {}, username="alice", is_admin=False, is_publisher=False, status="published"
    )

    assert error is None
    assert "$and" in filters
    clause = filters["$and"][0]["$or"]
    assert {"accessRights": {"$in": ["public", "internal"]}} in clause


def test_an_admin_query_is_not_constrained(mongo):
    filters, _error = access.build_listing_filters(
        {}, username="admin", is_admin=True, is_publisher=False, status="published"
    )
    assert "$and" not in filters


def test_all_four_no_rights_spellings_are_matched(mongo):
    """Absent, null, empty string and empty list all occur in real data.

    Missing one silently hides content that should be visible.
    """
    clause = access.access_rights_clause("alice")["$or"]

    assert {"accessRights": None} in clause
    assert {"accessRights": {"$exists": False}} in clause
    assert {"accessRights": ""} in clause
    assert {"accessRights": []} in clause


def test_a_user_with_no_rights_still_sees_unrestricted_resources(monkeypatch):
    monkeypatch.setattr(access, "_mongo", lambda: FakeMongo(rights=[]))
    clause = access.access_rights_clause("alice")["$or"]

    assert {"accessRights": {"$in": []}} in clause
    assert {"accessRights": None} in clause


def test_an_anonymous_caller_has_no_rights(monkeypatch):
    monkeypatch.setattr(access, "_mongo", lambda: FakeMongo(rights=None))
    assert access.user_access_rights(None) == []


# ---------------------------------------------------------------------------
# Deleted
# ---------------------------------------------------------------------------


def test_only_admins_may_browse_deleted_resources(mongo):
    _filters, error = access.build_listing_filters(
        {}, username="alice", is_admin=False, is_publisher=True, status="deleted"
    )
    assert error == "unauthorized"


def test_an_admin_may_browse_deleted_resources(mongo):
    _filters, error = access.build_listing_filters(
        {}, username="admin", is_admin=True, is_publisher=False, status="deleted"
    )
    assert error is None


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


def test_a_draft_query_covers_all_three_pre_publication_states(mongo):
    filters, _error = access.build_listing_filters(
        {}, username="admin", is_admin=True, is_publisher=True, status="draft"
    )
    assert {branch["status"] for branch in filters["$or"]} == {"draft", "created", "updated"}


def test_an_ordinary_user_sees_only_their_own_drafts(mongo):
    filters, _error = access.build_listing_filters(
        {}, username="alice", is_admin=False, is_publisher=False, status="draft"
    )
    assert all(branch["createdBy"] == "alice" for branch in filters["$or"])


@pytest.mark.parametrize(
    ("is_publisher", "is_admin", "restricted"),
    [
        (False, False, True),
        (False, True, True),   # admin alone is NOT enough - see below
        (True, False, True),   # publisher alone is NOT enough
        (True, True, False),
    ],
)
def test_only_a_publisher_who_is_also_admin_sees_all_drafts(mongo, is_publisher, is_admin, restricted):
    """Only someone who is BOTH publisher and admin sees everyone's drafts.

    Deliberately strict: it fails closed, and widening who can read other
    people's unpublished work is a policy decision.
    """
    filters, _error = access.build_listing_filters(
        {}, username="alice", is_admin=is_admin, is_publisher=is_publisher, status="draft"
    )
    has_owner_restriction = all("createdBy" in branch for branch in filters["$or"])
    assert has_owner_restriction is restricted


def test_the_base_filter_is_not_mutated(mongo):
    """The caller's dict must not be modified in place - it is reused."""
    base = {"post_type": "carpeta"}
    access.build_listing_filters(
        base, username="alice", is_admin=False, is_publisher=False, status="published"
    )
    assert base == {"post_type": "carpeta"}


def test_base_filters_survive_into_every_draft_branch(mongo):
    filters, _error = access.build_listing_filters(
        {"post_type": "carpeta"}, username="alice", is_admin=False, is_publisher=False, status="draft"
    )
    assert all(branch["post_type"] == "carpeta" for branch in filters["$or"])


# ---------------------------------------------------------------------------
# Inherited access rights in queries
# ---------------------------------------------------------------------------


def _values(doc, dotted):
    """Every value at a dotted path, descending into lists as Mongo does."""
    current = [doc]
    for key in dotted.split("."):
        found = []
        for item in current:
            if isinstance(item, dict) and key in item:
                value = item[key]
                found.extend(value if isinstance(value, list) and "." in dotted and key != dotted.split(".")[-1] else [value])
        current = found
    return current


def _field(doc, key, condition):
    values = _values(doc, key)
    present = bool(values)
    flat = []
    for value in values:
        flat.extend(value if isinstance(value, list) else [value])
    if isinstance(condition, dict):
        if "$exists" in condition:
            return present == condition["$exists"]
        if "$in" in condition:
            return any(v in condition["$in"] for v in flat)
        if "$nin" in condition:
            return not any(v in condition["$nin"] for v in flat)
        raise AssertionError(f"unsupported operator {condition}")
    if not present:
        return condition is None
    return any(v == condition for v in values) or condition in flat


def _match(doc, query):
    for key, condition in query.items():
        if key == "$and":
            ok = all(_match(doc, q) for q in condition)
        elif key == "$or":
            ok = any(_match(doc, q) for q in condition)
        elif key == "$nor":
            ok = not any(_match(doc, q) for q in condition)
        else:
            ok = _field(doc, key, condition)
        if not ok:
            return False
    return True


class TreeMongo:
    """Resources keyed by id, answering the queries ``access`` makes."""

    def __init__(self, resources):
        self.resources = {rid: {**doc, "_id": rid} for rid, doc in resources.items()}

    def get_all_records(self, collection, filters=None, fields=None, **kwargs):
        return [doc for doc in self.resources.values() if _match(doc, filters or {})]

    def distinct(self, collection, field, filters=None):
        found = set()
        for doc in self.get_all_records(collection, filters):
            found.update(v for v in _values(doc, field) if v is not None)
        return sorted(found)


def _under(*ids):
    return {"parents": [{"id": i} for i in ids]}


@pytest.fixture
def archive(monkeypatch):
    """A small archive with access rights at several depths.

    fondo_a (reserved)
      serie_a1                  -> inherits reserved
        item_a1                 -> inherits reserved
      serie_a2 (open)           -> its own right overrides the fondo's
        item_a2                 -> inherits open
          item_a2_x (reserved)  -> its own right
            item_a2_y           -> inherits reserved, from the nearer ancestor
    fondo_b                     -> no rights anywhere
      item_b
    """
    resources = {
        "fondo_a": {"accessRights": "reserved"},
        "serie_a1": _under("fondo_a"),
        "item_a1": _under("serie_a1", "fondo_a"),
        "serie_a2": {"accessRights": "open", **_under("fondo_a")},
        "item_a2": _under("serie_a2", "fondo_a"),
        "item_a2_x": {"accessRights": "reserved", **_under("item_a2", "serie_a2", "fondo_a")},
        "item_a2_y": _under("item_a2_x", "item_a2", "serie_a2", "fondo_a"),
        "fondo_b": {"accessRights": None},
        "item_b": _under("fondo_b"),
    }
    fake = TreeMongo(resources)
    held = {"rights": []}
    monkeypatch.setattr(access, "_mongo", lambda: fake)
    monkeypatch.setattr(access, "restricted_ancestors", _REAL_RESTRICTED_ANCESTORS)
    monkeypatch.setattr(access, "user_access_rights", lambda username: held["rights"])
    return fake, held


def _visible(fake):
    clause = access.access_rights_clause("alice")
    return {rid for rid, doc in fake.resources.items() if _match(doc, clause)}


def test_a_caller_with_no_rights_sees_nothing_under_a_reserved_fondo(archive):
    fake, _held = archive
    assert _visible(fake) == {"fondo_b", "item_b"}


def test_holding_only_the_nested_right_opens_that_branch_alone(archive):
    fake, held = archive
    held["rights"] = ["open"]
    assert _visible(fake) == {"fondo_b", "item_b", "serie_a2", "item_a2"}


def test_holding_only_the_outer_right_leaves_the_nested_branch_closed(archive):
    fake, held = archive
    held["rights"] = ["reserved"]
    assert _visible(fake) == {
        "fondo_a", "serie_a1", "item_a1", "item_a2_x", "item_a2_y", "fondo_b", "item_b",
    }


def test_holding_both_rights_shows_everything(archive):
    fake, held = archive
    held["rights"] = ["reserved", "open"]
    assert _visible(fake) == set(fake.resources)


@pytest.mark.parametrize("rights", [[], ["open"], ["reserved"], ["reserved", "open"]])
def test_the_query_agrees_with_the_per_resource_rule(archive, monkeypatch, rights):
    """The listing and opening a resource must never disagree about what is visible."""
    fake, held = archive
    held["rights"] = rights

    def effective(resource):
        own = resource.get("accessRights")
        if own:
            return own
        for parent in resource.get("parents") or []:
            inherited = fake.resources[parent["id"]].get("accessRights")
            if inherited:
                return inherited
        return None

    monkeypatch.setattr(access, "effective_access_right", effective)
    expected = {
        rid for rid, doc in fake.resources.items()
        if access.may_view_resource("alice", doc, is_admin=False)
    }
    assert _visible(fake) == expected


def test_only_resources_with_descendants_are_candidates(archive):
    fake, _held = archive
    ids = {node["id"] for node in _REAL_RESTRICTED_ANCESTORS()}
    assert ids == {"fondo_a", "serie_a2", "item_a2_x"}

