"""The article editor.

``articleBody`` is the long-form narrative some content types carry alongside
their catalogue metadata. The write path is where the interesting problems are:
the original could be used to change fields that have nothing to do with
articles, and in the common configuration it required no authorisation at all.
"""

from __future__ import annotations

import datetime

import pytest
from bson.objectid import ObjectId

from archihub.api.resources import article

VALID_ID = "6a70b833497d4440325c94b1"
PARENT_ID = "6a70b833497d4440325c94b2"


class FakeMongo:
    def __init__(self):
        self.resource: dict | None = None
        self.ancestors: list[dict] = []
        self.type: dict | None = None
        self.user: dict | None = None
        self.writes: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        if collection == "post_types":
            return self.type
        if collection == "users":
            return self.user
        return self.resource

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return list(self.ancestors)

    def update_record(self, collection, filters, update_model):
        self.writes.append((filters, update_model))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(article, "_mongo", lambda: fake)
    monkeypatch.setattr(article.access, "_mongo", lambda: fake)
    monkeypatch.setattr(article.hierarchy, "_mongo", lambda: fake)
    monkeypatch.setattr(article, "_audit", lambda user, details: None)
    return fake


@pytest.fixture(autouse=True)
def as_nobody(monkeypatch):
    """No roles at all unless a test says otherwise."""
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: False)


def with_roles(monkeypatch, *roles):
    held = set(roles)
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: r in held)


def resource(**overrides):
    return {
        "_id": ObjectId(VALID_ID),
        "post_type": "foto",
        "createdBy": "owner",
        "accessRights": None,
        "parents": [],
        **overrides,
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_the_article_is_returned_to_someone_who_may_read_the_resource(mongo):
    mongo.resource = resource(articleBody=[{"id": "b1", "text": "hola"}])
    mongo.user = {"accessRights": []}

    payload, status = article.get_article_body(VALID_ID, "alice")

    assert status == 200
    assert payload["articleBody"] == [{"id": "b1", "text": "hola"}]


def test_a_resource_without_an_article_returns_null(mongo):
    mongo.resource = resource()
    mongo.user = {"accessRights": []}

    payload, status = article.get_article_body(VALID_ID, "alice")
    assert (payload["articleBody"], status) == (None, 200)


def test_a_missing_resource_is_404(mongo):
    mongo.resource = None
    _payload, status = article.get_article_body(VALID_ID, "alice")
    assert status == 404


def test_a_malformed_id_is_404_not_500(mongo):
    mongo.resource = None
    _payload, status = article.get_article_body("not-an-object-id", "alice")
    assert status == 404


def test_an_inherited_access_right_is_honoured_when_reading(mongo):
    mongo.resource = resource(parents=[{"id": PARENT_ID}], articleBody=[])
    mongo.ancestors = [{"_id": ObjectId(PARENT_ID), "accessRights": "reserved"}]
    mongo.user = {"accessRights": ["public"]}

    _payload, status = article.get_article_body(VALID_ID, "alice")
    assert status == article.ROLE_FAILURE_STATUS


def test_datetimes_inside_blocks_are_serialised(mongo):
    """Blocks are free-form and carry timestamps at arbitrary depth."""
    mongo.resource = resource(
        articleBody=[{"comments": [{"createdAt": datetime.datetime(2024, 3, 1)}]}]
    )
    mongo.user = {"accessRights": []}

    payload, _status = article.get_article_body(VALID_ID, "alice")
    assert payload["articleBody"][0]["comments"][0]["createdAt"].startswith("2024-03-01")


# ---------------------------------------------------------------------------
# Authorisation on write
# ---------------------------------------------------------------------------


def test_a_stranger_cannot_rewrite_an_article(mongo):
    """THE hole (S17).

    The original checked only the content type's editRoles. A type that
    declares none - the common case, including the default seeded type - meant
    any authenticated user could overwrite any resource's article.
    """
    mongo.resource = resource()
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "stranger")

    assert status == article.ROLE_FAILURE_STATUS
    assert mongo.writes == []


def test_the_creator_may_edit_their_own_resource(mongo):
    mongo.resource = resource(createdBy="alice")
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "alice")
    assert status == 200


def test_a_super_editor_may_edit_anyones_resource(mongo, monkeypatch):
    with_roles(monkeypatch, "super_editor")
    mongo.resource = resource()
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "editor")
    assert status == 200


def test_an_admin_may_always_edit(mongo, monkeypatch):
    with_roles(monkeypatch, "admin")
    mongo.resource = resource()
    mongo.type = {"editRoles": ["curator"], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "admin")
    assert status == 200


def test_a_declared_edit_role_is_sufficient(mongo, monkeypatch):
    """Exactly the original's rule, kept where the original had one."""
    with_roles(monkeypatch, "curator")
    mongo.resource = resource()
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": ["curator"], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "cur")
    assert status == 200


def test_lacking_the_declared_edit_role_is_refused(mongo):
    mongo.resource = resource(createdBy="alice")
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": ["curator"], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "alice")
    assert status == article.ROLE_FAILURE_STATUS


def test_nobody_may_edit_what_they_cannot_read(mongo, monkeypatch):
    """The original checked access rights on the read route and not the write
    one, so a reserved resource's narrative was rewritable by anyone."""
    with_roles(monkeypatch, "curator")
    mongo.resource = resource(accessRights="reserved")
    mongo.user = {"accessRights": ["public"]}
    mongo.type = {"editRoles": ["curator"], "viewRoles": []}

    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "cur")
    assert status == article.ROLE_FAILURE_STATUS


# ---------------------------------------------------------------------------
# What actually gets written
# ---------------------------------------------------------------------------


def test_only_the_article_and_its_audit_fields_are_written(mongo):
    """THE other half of S17.

    The original built its update from the whole request body and
    ``ResourceUpdate`` accepts ``status``, ``accessRights``, ``post_type``,
    ``parent``, ``parents``, ``metadata``, ``ident`` and ``favCount`` - and the
    database layer writes exactly the fields that were set. So an article save
    could publish a draft, clear its access restrictions and re-file it in the
    tree, bypassing every check the real update route performs.
    """
    mongo.resource = resource(createdBy="alice")
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    article.update_article_body(
        VALID_ID,
        {
            "articleBody": [{"id": "b1"}],
            "status": "published",
            "accessRights": None,
            "post_type": "otro",
            "parent": [{"id": "elsewhere"}],
            "metadata": {"firstLevel": {"title": "hijacked"}},
            "favCount": 9999,
        },
        "alice",
    )

    _filters, written = mongo.writes[0]
    assert set(written) == {"articleBody", "updatedAt", "updatedBy"}
    assert written["updatedBy"] == "alice"


def test_a_missing_article_body_is_rejected(mongo):
    mongo.resource = resource(createdBy="alice")
    _payload, status = article.update_article_body(VALID_ID, {}, "alice")
    assert status == 400


def test_an_article_body_that_is_not_a_list_is_rejected(mongo):
    mongo.resource = resource(createdBy="alice")
    _payload, status = article.update_article_body(VALID_ID, {"articleBody": "text"}, "alice")
    assert status == 400


def test_a_missing_resource_is_404_not_500(mongo):
    """The original read ``resource['post_type']`` several lines above its own
    existence check, so a stale id produced a 500 where 404 was documented."""
    mongo.resource = None
    _payload, status = article.update_article_body(VALID_ID, {"articleBody": []}, "alice")
    assert status == 404


# ---------------------------------------------------------------------------
# Block comments
# ---------------------------------------------------------------------------


@pytest.fixture
def commentable(mongo):
    mongo.resource = resource(
        createdBy="alice", articleBody=[{"id": "b1"}, {"id": "b2"}]
    )
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}
    return mongo


def test_a_comment_is_attached_to_the_block_named_by_id(commentable):
    payload, status = article.add_block_comment(
        VALID_ID, {"comment": "revisar", "blockId": "b2"}, "alice"
    )

    assert status == 200
    assert payload["blockIndex"] == 1
    _filters, written = commentable.writes[0]
    assert written["articleBody"][1]["comments"][0]["comment"] == "revisar"
    assert written["articleBody"][0].get("comments") is None


def test_a_comment_may_address_a_block_by_index(commentable):
    payload, status = article.add_block_comment(
        VALID_ID, {"comment": "ok", "blockIndex": 0}, "alice"
    )
    assert (status, payload["blockIndex"]) == (200, 0)


def test_a_boolean_index_is_refused(commentable):
    """``True`` is an int in Python, and would silently address block 1."""
    _payload, status = article.add_block_comment(
        VALID_ID, {"comment": "ok", "blockIndex": True}, "alice"
    )
    assert status == 400


def test_an_out_of_range_index_is_404(commentable):
    _payload, status = article.add_block_comment(
        VALID_ID, {"comment": "ok", "blockIndex": 99}, "alice"
    )
    assert status == 404


def test_an_unknown_block_id_is_404(commentable):
    _payload, status = article.add_block_comment(
        VALID_ID, {"comment": "ok", "blockId": "nope"}, "alice"
    )
    assert status == 404


def test_naming_no_block_at_all_is_400(commentable):
    _payload, status = article.add_block_comment(VALID_ID, {"comment": "ok"}, "alice")
    assert status == 400


@pytest.mark.parametrize("comment", ["", "   ", None, 5])
def test_an_empty_comment_is_refused(commentable, comment):
    _payload, status = article.add_block_comment(
        VALID_ID, {"comment": comment, "blockId": "b1"}, "alice"
    )
    assert status == 400


def test_a_comment_is_trimmed(commentable):
    payload, _status = article.add_block_comment(
        VALID_ID, {"comment": "  revisar  ", "blockId": "b1"}, "alice"
    )
    assert payload["comment"]["comment"] == "revisar"


def test_commenting_requires_the_same_authorisation_as_editing(mongo):
    mongo.resource = resource(articleBody=[{"id": "b1"}])
    mongo.user = {"accessRights": []}
    mongo.type = {"editRoles": [], "viewRoles": []}

    _payload, status = article.add_block_comment(
        VALID_ID, {"comment": "hola", "blockId": "b1"}, "stranger"
    )

    assert status == article.ROLE_FAILURE_STATUS
    assert mongo.writes == []


def test_existing_comments_are_preserved(commentable):
    commentable.resource["articleBody"][0]["comments"] = [{"comment": "primero"}]

    article.add_block_comment(VALID_ID, {"comment": "segundo", "blockId": "b1"}, "alice")

    _filters, written = commentable.writes[0]
    assert [c["comment"] for c in written["articleBody"][0]["comments"]] == [
        "primero",
        "segundo",
    ]


def test_malformed_existing_comments_are_reported_not_overwritten(commentable):
    commentable.resource["articleBody"][0]["comments"] = "not a list"

    _payload, status = article.add_block_comment(
        VALID_ID, {"comment": "hola", "blockId": "b1"}, "alice"
    )

    assert status == 400
    assert commentable.writes == []


def test_a_comment_records_who_left_it_and_when(commentable):
    payload, _status = article.add_block_comment(
        VALID_ID, {"comment": "hola", "blockId": "b1"}, "alice"
    )

    assert payload["comment"]["user"] == "alice"
    assert payload["comment"]["createdAt"]
