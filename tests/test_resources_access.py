"""Who may see which resources.

The archive's main read path. Every test here is an access-control boundary, so
a change that makes one fail is either a deliberate policy decision or a leak.
"""

from __future__ import annotations

import pytest

from archihub.api.resources import access


class FakeMongo:
    def __init__(self, rights=None):
        self.rights = rights

    def get_record(self, collection, filters, fields=None):
        return {"accessRights": self.rights} if self.rights is not None else None


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
def test_draft_visibility_matches_legacy_exactly(mongo, is_publisher, is_admin, restricted):
    """Pins the legacy behaviour, including what looks like a typo.

    The original guard (`not publisher or not admin`) grants "see all drafts"
    only to someone who is BOTH. Every comparable check in the codebase is
    written as "neither", so `or` is probably a slip.

    It is preserved rather than fixed because the bug fails CLOSED - it shows
    people less than intended, never more - and correcting it would widen who
    can read other people's unpublished work. That is a policy decision, not a
    migration one. See .
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
