"""Account lifecycle and API-key issuance.

Several tests here exist to keep a privilege boundary in place rather than to
check a happy path.
"""

from __future__ import annotations

import bcrypt
import pytest
from bson.objectid import ObjectId

from archihub.api.users import services


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.inserted: list = []
        self.updated: list = []
        self.deleted: list = []

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return []

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))
        return type("R", (), {"modified_count": 1})()

    def update_records(self, collection, filters, update):
        self.updated.append((collection, filters, update))

    def delete_record(self, collection, filters):
        self.deleted.append((collection, filters))

    def count(self, collection, filters=None):
        return 0


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    return fake


@pytest.fixture
def roles(monkeypatch):
    monkeypatch.setattr(
        "archihub.core.roles.get_roles",
        lambda: {"options": [{"id": "admin"}, {"id": "editor"}, {"id": "user"}, {"id": "visualizer"}]},
    )
    monkeypatch.setattr("archihub.core.roles.get_access_rights", lambda: {"options": [{"id": "public"}]})


def hashed(password="correct-horse"):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


VALID_ID = "6a70b8c3497d4440325c94c3"


# ---------------------------------------------------------------------------
# Self-registration must not be a privilege escalation
# ---------------------------------------------------------------------------


def test_self_registration_cannot_grant_roles(mongo, roles, monkeypatch):
    """A self-registering caller must not influence their own privileges.

    Enforced twice: the request schema ignores undeclared fields, and the
    service fixes the roles rather than reading them from the body.
    """
    monkeypatch.setattr(services, "self_registration_enabled", lambda: True)
    mongo.records["users"] = None

    services.register_me(
        {"username": "mallory@x.test", "password": "p", "roles": [{"id": "admin"}]}
    )

    _collection, record = mongo.inserted[0]
    assert record["roles"] == ["user"]
    assert record["accessRights"] == []


def test_self_registered_accounts_start_unverified(mongo, roles, monkeypatch):
    monkeypatch.setattr(services, "self_registration_enabled", lambda: True)
    mongo.records["users"] = None

    services.register_me({"username": "new@x.test", "password": "p"})

    _collection, record = mongo.inserted[0]
    assert record["verified"] is False


def test_self_registration_respects_the_instance_setting(mongo, monkeypatch):
    monkeypatch.setattr(services, "self_registration_enabled", lambda: False)
    _payload, status = services.register_me({"username": "new@x.test", "password": "p"})

    assert status == 400
    assert mongo.inserted == []


def test_schema_drops_privilege_fields_from_self_registration():
    from archihub.api.users.schemas import RegisterMeRequest

    parsed = RegisterMeRequest(username="a@b.c", password="p", roles=[{"id": "admin"}])
    assert not hasattr(parsed, "roles")


def test_passwords_are_hashed_not_stored(mongo, roles):
    mongo.records["users"] = None
    services.register_user(
        {"username": "a@b.c", "password": "plaintext", "roles": [], "accessRights": []}
    )

    _collection, record = mongo.inserted[0]
    assert record["password"] != "plaintext"
    assert bcrypt.checkpw(b"plaintext", record["password"].encode())


# ---------------------------------------------------------------------------
# Recovery must not report which accounts exist
# ---------------------------------------------------------------------------


def test_recovery_answers_identically_for_known_and_unknown_accounts(mongo, monkeypatch):
    monkeypatch.setattr(services, "password_recovery_enabled", lambda: True)
    monkeypatch.setattr(services, "_send_recovery_email", lambda username: None)

    mongo.records["users"] = {"username": "real@x.test"}
    known = services.forgot_password({"username": "real@x.test"})

    mongo.records["users"] = None
    unknown = services.forgot_password({"username": "nobody@x.test"})

    assert known == unknown


def test_a_mail_failure_does_not_change_the_response(mongo, monkeypatch):
    """Otherwise a real account with broken SMTP answers differently from an
    invented one, which is the same disclosure by another route."""
    monkeypatch.setattr(services, "password_recovery_enabled", lambda: True)

    def _explode(username):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(services, "_send_recovery_email", _explode)
    mongo.records["users"] = {"username": "real@x.test"}

    payload, status = services.forgot_password({"username": "real@x.test"})
    assert status == 200


# ---------------------------------------------------------------------------
# Administrative update
# ---------------------------------------------------------------------------


def test_a_user_must_keep_at_least_one_system_role(mongo, roles):
    mongo.records["users"] = {"_id": ObjectId(VALID_ID), "username": "a@b.c"}

    _payload, status = services.update_user(
        {"_id": VALID_ID, "username": "a@b.c", "roles": [{"id": "visualizer"}], "accessRights": []},
        "admin",
    )
    assert status == 400


def test_username_cannot_be_changed(mongo, roles):
    mongo.records["users"] = {"_id": ObjectId(VALID_ID), "username": "original@x.test"}

    _payload, status = services.update_user(
        {"_id": VALID_ID, "username": "hijack@x.test", "roles": [{"id": "user"}], "accessRights": []},
        "admin",
    )
    assert status == 400


def test_unknown_roles_are_rejected(mongo, roles):
    mongo.records["users"] = {"_id": ObjectId(VALID_ID), "username": "a@b.c"}

    _payload, status = services.update_user(
        {"_id": VALID_ID, "username": "a@b.c", "roles": [{"id": "superuser"}], "accessRights": []},
        "admin",
    )
    assert status == 400


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def test_you_cannot_delete_yourself(mongo):
    _payload, status = services.delete_user({"username": "admin@x.test"}, "admin@x.test")
    assert status == 400
    assert mongo.deleted == []


def test_deleting_an_account_revokes_its_api_keys(mongo, monkeypatch):
    """The user document is not the only thing that authenticates.

    API keys live in their own collection, so deleting the user alone would
    leave working credentials behind.
    """
    revoked = []
    monkeypatch.setattr(
        "archihub.core.security.api_keys.revoke_all", lambda username, scope=None: revoked.append(username)
    )
    mongo.records["users"] = {"username": "gone@x.test"}

    services.delete_user({"username": "gone@x.test"}, "admin@x.test")

    assert mongo.deleted
    assert revoked == ["gone@x.test"]


# ---------------------------------------------------------------------------
# Self-service update
# ---------------------------------------------------------------------------


def test_current_password_is_required(mongo):
    mongo.records["users"] = {"password": hashed(), "name": "Old"}
    _payload, status = services.update_me({"password": "wrong", "name": "New"}, "alice")

    assert status == 400
    assert mongo.updated == []


def test_changing_the_password_revokes_api_keys(mongo, monkeypatch):
    """Usually the reason for changing it."""
    revoked = []
    monkeypatch.setattr(
        "archihub.core.security.api_keys.revoke_all", lambda username, scope=None: revoked.append(username)
    )
    mongo.records["users"] = {"password": hashed(), "name": "Alice"}

    services.update_me(
        {"password": "correct-horse", "new_password": "brand-new", "new_password_confirmation": "brand-new"},
        "alice",
    )
    assert revoked == ["alice"]


def test_mismatched_confirmation_is_rejected(mongo):
    mongo.records["users"] = {"password": hashed(), "name": "Alice"}
    _payload, status = services.update_me(
        {"password": "correct-horse", "new_password": "a", "new_password_confirmation": "b"}, "alice"
    )
    assert status == 400


def test_a_no_op_update_is_reported(mongo):
    mongo.records["users"] = {"password": hashed(), "name": "Alice"}
    _payload, status = services.update_me({"password": "correct-horse", "name": "Alice"}, "alice")
    assert status == 400


# ---------------------------------------------------------------------------
# API-key issuance
# ---------------------------------------------------------------------------


def test_issuing_a_key_requires_the_current_password(mongo):
    """An API key outlives the session that created it, so minting one from a
    hijacked session would be a durable takeover."""
    mongo.records["users"] = {"password": hashed()}

    _payload, status = services.issue_api_key("alice", "wrong", "public")
    assert status == 400


def test_a_correct_password_issues_a_key(mongo, monkeypatch):
    mongo.records["users"] = {"password": hashed()}
    monkeypatch.setattr(
        "archihub.core.security.api_keys.create_key",
        lambda username, scope, name=None, expires_in=None: "ahk_abc_secret",
    )

    payload, status = services.issue_api_key("alice", "correct-horse", "public")

    assert status == 200
    assert payload["access_token"] == "ahk_abc_secret"
    # The response says outright that this is the only copy.
    assert "not be shown again" in payload["msg"] or "shown again" in payload["msg"]


def test_an_account_without_a_password_cannot_mint_keys(mongo):
    """Directory-backed accounts store no local hash."""
    mongo.records["users"] = {"password": ""}
    _payload, status = services.issue_api_key("ldapuser", "anything", "public")
    assert status == 400
