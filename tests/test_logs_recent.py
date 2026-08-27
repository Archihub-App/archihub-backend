"""Recent activity, and who is allowed to see what of it.

THE HEADLINE CASE IS `test_a_requested_category_cannot_widen_what_a_role_allows`.

The role's restriction and the caller's own filters are joined with `$and`.
Merged into one document instead, a requested `category` would overwrite the
`action` clause the role imposed - and the single category a role exists to deny
is exactly what asking for it would then return. The tests below assert both the
shape of the composed filter and the behaviour, because the shape is what makes
the behaviour hold for filters nobody has thought of yet.

The second theme is that an unclassified action is INFRASTRUCTURE. A plugin can
add an audited action at any time and nobody will remember to categorise it, so
the default has to be the restrictive one rather than "visible to everybody".
"""

from __future__ import annotations

import datetime

import pytest
from bson.objectid import ObjectId

from archihub.api.logs import recent

#: A resource id has to be a real ObjectId: the resolver converts before it
#: queries, so a made-up string is silently dropped and every resource comes
#: back unresolved - which would make the tests below pass for the wrong reason.
RES = ObjectId()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, logs=(), users=(), resources=()):
        self.logs = list(logs)
        self.users = list(users)
        self.resources = list(resources)
        self.queries: list[tuple] = []

    def get_all_records(self, collection, filters=None, **kwargs):
        self.queries.append((collection, filters))
        if collection == "logs":
            rows = _apply(self.logs, filters or {})
            rows.sort(key=lambda r: r.get("date"), reverse=True)
            skip = kwargs.get("skip") or 0
            limit = kwargs.get("limit")
            return rows[skip: skip + limit] if limit else rows[skip:]
        if collection == "users":
            wanted = ((filters or {}).get("username") or {}).get("$in") or []
            return [u for u in self.users if u["username"] in wanted]
        if collection == "resources":
            wanted = ((filters or {}).get("_id") or {}).get("$in") or []
            return [r for r in self.resources if r["_id"] in wanted]
        return []

    def count(self, collection, filters=None):
        return len(_apply(self.logs, filters or {}))


def _apply(rows, filters):
    """Just enough of a Mongo filter to make the composition testable."""
    if not filters:
        return list(rows)
    if "$and" in filters:
        result = list(rows)
        for clause in filters["$and"]:
            result = _apply(result, clause)
        return result

    kept = []
    for row in rows:
        ok = True
        for field, condition in filters.items():
            value = row.get(field)
            if isinstance(condition, dict):
                if "$in" in condition and value not in condition["$in"]:
                    ok = False
                if "$nin" in condition and value in condition["$nin"]:
                    ok = False
                if "$gte" in condition and not (value and value >= condition["$gte"]):
                    ok = False
            elif value != condition:
                ok = False
        if ok:
            kept.append(row)
    return kept


def entry(action, username="alice", when=None, metadata=None, identifier="1"):
    return {
        "_id": identifier,
        "username": username,
        "action": action,
        "date": when or datetime.datetime(2026, 8, 27, 12, 0),
        "metadata": metadata or {},
    }


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(recent, "_mongo", lambda: fake)
    return fake


@pytest.fixture
def roles(monkeypatch):
    """Decide what the caller holds without reaching for a database."""
    held: dict[str, set] = {}

    def has_role(username, role):
        return role in held.get(username, set())

    from archihub.api.users import services as user_services

    monkeypatch.setattr(user_services, "has_role", has_role)
    return held


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_actions_land_in_the_category_they_belong_to():
    assert recent.category_of("RESOURCE_CREATE") == recent.CATALOGING
    assert recent.category_of("AV_TRANSCRIBE") == recent.PROCESSING
    assert recent.category_of("USER_LOGIN") == recent.SECURITY
    assert recent.category_of("SYSTEM_UPDATE") == recent.SYSTEM


def test_an_action_nobody_classified_is_infrastructure():
    """A plugin can add one at any time, and nobody will remember this file."""
    assert recent.category_of("SOMETHING_NEW") == recent.SYSTEM
    assert recent.category_of(None) == recent.SYSTEM


def test_an_action_stored_under_its_lowercase_key_is_still_classified():
    """`normalize_action` writes the key verbatim for an action the vocabulary
    does not name, so both spellings are real stored values."""
    assert recent.category_of("user_login") == recent.SECURITY
    assert recent.category_of("resource_create") == recent.CATALOGING


def test_deletions_read_as_warnings_and_processing_as_success():
    assert recent.level_of("RESOURCE_DELETE") == "warning"
    assert recent.level_of("AV_TRANSCRIBE") == "success"
    assert recent.level_of("RESOURCE_CREATE") == "info"
    assert recent.level_of("ANYTHING_ELSE") == "info"


# ---------------------------------------------------------------------------
# Who may see what
# ---------------------------------------------------------------------------


def test_an_administrator_is_not_restricted():
    assert recent.visibility_clause("alice", is_admin=True, is_editor=True) == {}


def test_an_editor_is_restricted_to_the_business_of_the_archive():
    clause = recent.visibility_clause("alice", is_admin=False, is_editor=True)
    allowed = set(clause["action"]["$in"])

    assert "RESOURCE_CREATE" in allowed
    assert "AV_TRANSCRIBE" in allowed
    assert "USER_LOGIN" not in allowed, "security is not an editor's business"
    assert "SYSTEM_UPDATE" not in allowed, "nor is infrastructure"


def test_everyone_else_sees_only_what_they_did_themselves():
    assert recent.visibility_clause(
        "alice", is_admin=False, is_editor=False
    ) == {"username": "alice"}


def test_an_unclassified_action_is_hidden_from_an_editor():
    """The default has to be restrictive: nobody will remember to classify one."""
    clause = recent.visibility_clause("alice", is_admin=False, is_editor=True)

    assert "SOMETHING_NEW" not in set(clause["action"]["$in"])


def test_the_system_filter_finds_actions_nobody_classified():
    """Otherwise an unclassified action falls out of every filter and is
    visible to no one, including the administrator who needs to see it."""
    clause = recent._categories_clause([recent.SYSTEM])

    assert "SOMETHING_NEW" not in set(clause["action"]["$nin"])
    assert "RESOURCE_CREATE" in set(clause["action"]["$nin"])


# ---------------------------------------------------------------------------
# Composition - the security property
# ---------------------------------------------------------------------------


def test_a_requested_category_cannot_widen_what_a_role_allows(mongo, roles):
    """THE HEADLINE CASE.

    Merged into one filter document rather than joined with `$and`, the
    requested category would replace the role's `action` clause - so asking for
    the one category an editor is denied would return exactly it.
    """
    roles["alice"] = {"editor"}
    mongo.logs = [
        entry("USER_LOGIN", identifier="login"),
        entry("RESOURCE_CREATE", identifier="catalogued"),
    ]

    payload, status = recent.recent({"category": "security"}, "alice")

    assert status == 200
    assert payload["data"] == []
    assert payload["total"] == 0


def test_the_role_and_the_request_are_joined_not_merged(mongo, roles):
    """Asserted on the composed filter as well as the result, because the shape
    is what keeps this true for filters nobody has written yet."""
    roles["alice"] = {"editor"}

    recent.recent({"category": "cataloging"}, "alice")

    _collection, filters = mongo.queries[0]
    assert "$and" in filters
    assert len(filters["$and"]) == 2
    assert "action" not in filters, "a top-level key would mean one clause replaced the other"


def test_a_requested_category_still_narrows(mongo, roles):
    roles["alice"] = {"editor"}
    mongo.logs = [
        entry("RESOURCE_CREATE", identifier="catalogued"),
        entry("AV_TRANSCRIBE", identifier="processed"),
    ]

    payload, _status = recent.recent({"category": "processing"}, "alice")

    assert [e["id"] for e in payload["data"]] == ["processed"]


def test_a_plain_user_asking_for_everything_still_sees_only_their_own(mongo, roles):
    mongo.logs = [
        entry("RESOURCE_CREATE", username="alice", identifier="mine"),
        entry("RESOURCE_CREATE", username="bob", identifier="theirs"),
    ]

    payload, _status = recent.recent({"category": "all"}, "alice")

    assert [e["id"] for e in payload["data"]] == ["mine"]


def test_a_plain_user_sees_their_own_security_events(mongo, roles):
    """Their own login is their own activity."""
    mongo.logs = [entry("USER_LOGIN", username="alice", identifier="mine")]

    payload, _status = recent.recent({}, "alice")

    assert [e["id"] for e in payload["data"]] == ["mine"]


def test_an_administrator_sees_every_author_and_every_category(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [
        entry("USER_LOGIN", username="bob", identifier="a"),
        entry("SYSTEM_UPDATE", username="carol", identifier="b"),
        entry("RESOURCE_CREATE", username="alice", identifier="c"),
    ]

    payload, _status = recent.recent({}, "alice")

    assert len(payload["data"]) == 3


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def test_an_unknown_category_is_refused(mongo, roles):
    payload, status = recent.recent({"category": "everything"}, "alice")

    assert status == 400
    assert mongo.queries == [], "refused before anything was read"


def test_the_limit_is_clamped_rather_than_refused(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [entry("RESOURCE_CREATE", identifier=str(i)) for i in range(200)]

    payload, _status = recent.recent({"limit": 9999}, "alice")

    assert payload["limit"] == recent.MAX_LIMIT
    assert len(payload["data"]) == recent.MAX_LIMIT


@pytest.mark.parametrize("value", [0, -5, "nonsense", None])
def test_an_unusable_limit_falls_back_to_something_sane(value):
    assert 1 <= recent._limit(value) <= recent.MAX_LIMIT


def test_page_is_translated_into_an_offset():
    assert recent._offset({"page": 3, "limit": 10}) == 30
    assert recent._offset({"offset": 7}) == 7
    assert recent._offset({"offset": -7}) == 0
    assert recent._offset({}) == 0


def test_an_explicit_offset_wins_over_a_page():
    assert recent._offset({"offset": 5, "page": 3, "limit": 10}) == 5


def test_since_accepts_a_plain_instant():
    assert recent.parse_since("2026-08-27T12:00:00") == datetime.datetime(2026, 8, 27, 12, 0)


def test_since_brings_an_offset_into_the_frame_the_entries_use():
    """Entries are written naive and local. Comparing an aware value against
    naive storage selects a window hours from the one that was asked for."""
    parsed = recent.parse_since("2026-08-27T12:00:00+00:00")

    assert parsed.tzinfo is None
    assert parsed == datetime.datetime(
        2026, 8, 27, 12, 0, tzinfo=datetime.timezone.utc
    ).astimezone().replace(tzinfo=None)


def test_an_unparseable_since_is_refused(mongo, roles):
    payload, status = recent.recent({"since": "last tuesday"}, "alice")

    assert status == 400
    assert mongo.queries == []


def test_since_filters(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [
        entry("RESOURCE_CREATE", when=datetime.datetime(2026, 8, 20), identifier="old"),
        entry("RESOURCE_CREATE", when=datetime.datetime(2026, 8, 26), identifier="new"),
    ]

    payload, _status = recent.recent({"since": "2026-08-25T00:00:00"}, "alice")

    assert [e["id"] for e in payload["data"]] == ["new"]


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def test_the_timestamp_is_a_plain_iso_string(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [entry("RESOURCE_CREATE", when=datetime.datetime(2026, 8, 27, 12, 45, 30))]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["timestamp"] == "2026-08-27T12:45:30"


def test_people_are_resolved_in_one_query_not_one_per_entry(mongo, roles):
    """Twenty entries by the same two people is two names, not twenty lookups."""
    roles["alice"] = {"admin"}
    mongo.users = [
        {"_id": "u1", "username": "alice", "name": "Alice A"},
        {"_id": "u2", "username": "bob", "name": "Bob B"},
    ]
    mongo.logs = [
        entry("RESOURCE_CREATE", username="alice" if i % 2 else "bob", identifier=str(i))
        for i in range(20)
    ]

    payload, _status = recent.recent({}, "alice")

    assert sum(1 for c, _f in mongo.queries if c == "users") == 1
    assert payload["data"][0]["user"]["name"] in {"Alice A", "Bob B"}


def test_an_author_with_no_account_is_still_named(mongo, roles):
    """Scheduled work is recorded against `system`, which is not a user."""
    roles["alice"] = {"admin"}
    mongo.logs = [entry("TYPE_UPDATE", username="system")]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["user"] == {"id": None, "username": "system", "name": "system"}


def test_a_resource_recorded_as_a_bare_id_is_resolved(mongo, roles):
    """The stored shape varies by action: a document for a create, an id for a
    delete. Reading only one of them leaves half the feed unnamed."""
    roles["alice"] = {"admin"}
    mongo.resources = [{
        "_id": RES, "post_type": "document",
        "metadata": {"firstLevel": {"title": "Acta de Sesión 1985"}},
    }]
    mongo.logs = [entry("RESOURCE_DELETE", metadata={"resource": str(RES)})]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["resource"] == {
        "id": str(RES), "post_type": "document", "title": "Acta de Sesión 1985"
    }


def test_a_resource_recorded_as_a_document_is_resolved(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.resources = [{
        "_id": RES, "post_type": "document",
        "metadata": {"firstLevel": {"title": "Current title"}},
    }]
    mongo.logs = [entry("RESOURCE_CREATE", metadata={"resource": {
        "_id": str(RES), "post_type": "document",
        "metadata": {"firstLevel": {"title": "Title as recorded"}},
    }})]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["resource"]["id"] == str(RES)
    # The CURRENT title, not the one the entry happens to have kept: an entry
    # written before a rename would otherwise name the resource by a title it
    # no longer has.
    assert payload["data"][0]["resource"]["title"] == "Current title"


def test_an_entry_naming_no_resource_says_so(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [entry("USER_LOGIN")]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["resource"] is None


def test_a_title_the_caller_may_not_see_is_withheld(mongo, roles, monkeypatch):
    """The entry is theirs to see; the resource's name is not.

    This feed is read by editors, so an entry naming a reserved resource would
    otherwise disclose its title to someone who cannot open it.
    """
    roles["alice"] = {"editor"}
    mongo.resources = [{
        "_id": RES, "post_type": "document",
        "metadata": {"firstLevel": {"title": "Reserved until 2050"}},
        "accessRights": "restricted",
    }]
    mongo.logs = [entry("RESOURCE_DELETE", metadata={"resource": str(RES)})]

    from archihub.api.resources import access

    monkeypatch.setattr(access, "may_view_resource", lambda *a, **k: False)

    payload, _status = recent.recent({}, "alice")
    shown = payload["data"][0]

    assert shown["resource"] == {"id": str(RES), "post_type": "document", "title": None}
    assert "Reserved until 2050" not in shown["description"]
    assert "a restricted resource" in shown["description"]


def test_the_description_names_the_person_and_the_resource(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.users = [{"_id": "u1", "username": "alice", "name": "Alice A"}]
    mongo.resources = [{
        "_id": RES, "post_type": "document",
        "metadata": {"firstLevel": {"title": "Acta 1985"}},
    }]
    mongo.logs = [entry("RESOURCE_CREATE", metadata={"resource": str(RES)})]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["description"] == 'Alice A created the resource “Acta 1985”'


def test_an_action_with_no_sentence_is_still_described(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [entry("SOMETHING_NEW")]

    payload, _status = recent.recent({}, "alice")

    assert payload["data"][0]["description"] == "alice performed the action SOMETHING_NEW"


def test_a_resource_with_no_title_is_not_called_restricted(mongo, roles):
    """"Untitled" and "you may not see this" are different facts."""
    roles["alice"] = {"admin"}
    mongo.resources = [{"_id": RES, "post_type": "document", "metadata": {}}]
    mongo.logs = [entry("RESOURCE_CREATE", metadata={"resource": str(RES)})]

    payload, _status = recent.recent({}, "alice")

    assert "(untitled)" in payload["data"][0]["description"]


def test_the_article_body_never_reaches_the_feed(mongo, roles):
    """It is megabytes of HTML, and the audit view strips it already."""
    roles["alice"] = {"admin"}
    mongo.logs = [entry("RESOURCE_ARTICLE_UPDATE", metadata={
        "articleBody": "<p>" + "x" * 5000 + "</p>",
        "resource": {"_id": "res1", "articleBody": "<p>also here</p>"},
    })]

    payload, _status = recent.recent({}, "alice")
    shown = payload["data"][0]["metadata"]

    assert "articleBody" not in shown
    assert "articleBody" not in shown.get("resource", {})


def test_the_metadata_does_not_hand_back_a_title_the_resource_field_withheld(
    mongo, roles, monkeypatch
):
    """The access check is defeated if the same title is in the entry twice.

    Several actions record a COPY of the document they acted on, title
    included. Withholding it from the `resource` field while leaving the copy
    in `metadata` discloses exactly what the check was there to stop.
    """
    roles["alice"] = {"editor"}
    mongo.resources = [{
        "_id": RES, "post_type": "document",
        "metadata": {"firstLevel": {"title": "Reserved until 2050"}},
        "accessRights": "restricted",
    }]
    mongo.logs = [entry("RESOURCE_CREATE", metadata={"resource": {
        "_id": str(RES), "post_type": "document",
        "metadata": {"firstLevel": {"title": "Reserved until 2050"}},
    }})]

    from archihub.api.resources import access

    monkeypatch.setattr(access, "may_view_resource", lambda *a, **k: False)

    shown = recent.recent({}, "alice")[0]["data"][0]

    assert "Reserved until 2050" not in str(shown)


def test_the_metadata_keeps_what_is_not_a_document_copy(mongo, roles):
    """Stripping the copy must not empty the block it lives in."""
    roles["alice"] = {"admin"}
    mongo.logs = [entry("AV_TRANSCRIBE", metadata={
        "ids": ["a", "b"], "form": {"prompt": "p", "resources": [], "parent": None,
                                    "post_type": "doc"},
    })]

    shown = recent.recent({}, "alice")[0]["data"][0]

    assert shown["metadata"]["prompt"] == "p"
    assert "12 file" not in shown["description"]


def test_the_response_states_every_field_the_contract_promises(mongo, roles):
    roles["alice"] = {"admin"}
    mongo.logs = [entry("RESOURCE_CREATE")]

    payload, _status = recent.recent({}, "alice")

    assert set(payload) == {"total", "limit", "offset", "data"}
    assert set(payload["data"][0]) == {
        "id", "timestamp", "action", "category", "level",
        "description", "user", "resource", "metadata",
    }
