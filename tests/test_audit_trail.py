"""What the audit log records, and the vocabulary it records it in.

Two kinds of test. The first half is structural: every action a call site
writes must be named in ``log_actions`` (otherwise it is stored under its raw
key, invisible to the action filter), and every named action must have a
sentence in the activity feed. The second half drives each audited operation
through its service and reads the entry the root ``audit_log`` fixture caught.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest
from bson.objectid import ObjectId

from archihub.core.log_actions import log_actions

ROOT = pathlib.Path(__file__).resolve().parent.parent / "archihub"
AUDIT_CALLS = {"register_log", "_register_log", "_audit"}
VALID_ID = "6a70b8c3497d4440325c94c3"


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def _literal_actions() -> list[tuple[str, int, str]]:
    """Every action passed as a string literal to an audit call under ``archihub/``."""
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" in path.relative_to(ROOT).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in AUDIT_CALLS or len(node.args) < 2:
                continue
            candidates = [node.args[1]]
            if isinstance(node.args[1], ast.IfExp):
                candidates = [node.args[1].body, node.args[1].orelse]
            for candidate in candidates:
                if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                    found.append((str(path.relative_to(ROOT)), node.lineno, candidate.value))
    return found


def test_the_scan_finds_the_audit_calls():
    """A scan that matches nothing would pass the test below vacuously."""
    actions = {action for _path, _line, action in _literal_actions()}
    assert {"resource_create", "user_login", "plugin_task", "api_key_revoke"} <= actions


def test_every_recorded_action_is_named_in_the_vocabulary():
    """An unnamed action is stored under its raw key, so the audit screen's
    action filter - built from the vocabulary - can never select it."""
    known = set(log_actions) | set(log_actions.values())
    unnamed = [
        f"{path}:{line} {action}"
        for path, line, action in _literal_actions()
        if action not in known
    ]
    assert not unnamed, f"actions missing from log_actions: {unnamed}"


def test_every_action_in_the_vocabulary_can_be_described():
    """The feed falls back to "performed the action X" for an action with no
    sentence: readable, but it names an internal constant to the user."""
    from archihub.api.logs import recent

    assert not [key for key in log_actions if key not in recent._DESCRIPTIONS]


@pytest.mark.parametrize(
    ("action", "category"),
    [
        ("USER_CREATE", "security"),
        ("USER_PASSWORD_CHANGE", "security"),
        ("API_KEY_REVOKE", "security"),
        ("user_update", "security"),
        ("PLUGIN_TASK", "processing"),
        ("TASK_CANCEL", "processing"),
        ("USERTASK_CREATE", "cataloging"),
        ("SYSTEM_RESTART", "system"),
        ("LLM_PROVIDER_CREATE", "system"),
    ],
)
def test_new_actions_land_in_their_category(action, category):
    from archihub.api.logs import recent

    assert recent.category_of(action) == category


def test_account_changes_are_hidden_from_editors():
    from archihub.api.logs import recent

    clause = recent.visibility_clause("ed", is_admin=False, is_editor=True)
    assert "USER_UPDATE" not in set(clause["action"]["$in"])
    assert "API_KEY_CREATE" not in set(clause["action"]["$in"])


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, records: dict | None = None):
        self.records = records or {}
        self.inserted: list = []
        self.updated: list = []

    def get_record(self, collection, filters=None, fields=None):
        return self.records.get(collection)

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))
        return SimpleNamespace(inserted_id=ObjectId(VALID_ID))

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))
        return SimpleNamespace(modified_count=1)


@pytest.fixture
def users(monkeypatch):
    from archihub.api.users import services

    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(
        "archihub.core.roles.get_roles",
        lambda: {"options": [{"id": "admin"}, {"id": "editor"}, {"id": "user"}]},
    )
    monkeypatch.setattr("archihub.core.roles.get_access_rights", lambda: {"options": []})
    return services, fake


def test_an_administrator_creating_an_account_is_recorded_with_the_roles_granted(users, audit_log):
    services, _fake = users

    _payload, status = services.register_user(
        {"username": "new@x.test", "password": "p", "roles": ["editor"]}, actor="root"
    )

    assert status == 201
    entry = audit_log[-1]
    assert (entry["username"], entry["action"]) == ("root", "USER_CREATE")
    assert entry["metadata"]["user"] == "new@x.test"
    assert entry["metadata"]["roles"] == ["editor"]
    assert "password" not in entry["metadata"]


def test_self_registration_is_recorded_as_the_new_user(users, audit_log, monkeypatch):
    services, _fake = users
    monkeypatch.setattr(services, "self_registration_enabled", lambda: True)

    services.register_me({"username": "me@x.test", "password": "p"})

    assert (audit_log[-1]["username"], audit_log[-1]["action"]) == ("me@x.test", "USER_REGISTER")


def test_a_system_provisioned_account_is_recorded_as_system(users, audit_log):
    services, _fake = users

    services.register_user({"username": "ldap@x.test", "roles": ["user"], "loginType": "ldap"})

    assert audit_log[-1]["username"] == "system"


def test_a_rejected_account_is_not_recorded(users, audit_log):
    """An entry is written once the action succeeded, never for a refusal."""
    services, fake = users
    fake.records["users"] = {"username": "taken@x.test"}

    _payload, status = services.register_user({"username": "taken@x.test"}, actor="root")

    assert status == 400
    assert audit_log == []


def test_a_role_change_records_the_roles_it_set(users, audit_log):
    services, fake = users
    fake.records["users"] = {"_id": ObjectId(VALID_ID), "username": "bob"}

    services.update_user({"_id": VALID_ID, "roles": ["admin"]}, "root")

    entry = audit_log[-1]
    assert entry["action"] == "USER_UPDATE"
    assert entry["metadata"] == {"user": "bob", "roles": ["admin"], "accessRights": []}


def _with_password(fake, password="right"):
    import bcrypt

    fake.records["users"] = {
        "username": "alice",
        "password": bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode(),
        "name": "Alice",
    }


def test_a_password_change_names_the_fields_and_never_their_values(users, audit_log):
    services, fake = users
    _with_password(fake)

    services.update_me({"password": "right", "new_password": "n3w", "phone": "555"}, "alice")

    entry = audit_log[-1]
    assert entry["action"] == "USER_PASSWORD_CHANGE"
    assert entry["metadata"] == {"user": "alice", "fields": ["phone"]}
    assert "555" not in str(entry) and "n3w" not in str(entry)


def test_a_profile_edit_without_a_new_password_is_a_profile_update(users, audit_log):
    services, fake = users
    _with_password(fake)

    services.update_me({"password": "right", "name": "Alicia"}, "alice")

    assert audit_log[-1]["action"] == "USER_PROFILE_UPDATE"


def test_accepting_the_terms_is_recorded(users, audit_log):
    services, _fake = users

    services.accept_compromise("alice")

    assert (audit_log[-1]["username"], audit_log[-1]["action"]) == ("alice", "USER_ACCEPT_TERMS")


def test_issuing_and_revoking_an_api_key_are_their_own_actions(users, audit_log, monkeypatch):
    from archihub.core.security import api_keys

    services, _fake = users
    monkeypatch.setattr(services, "_verify_current_password", lambda u, p: True)
    monkeypatch.setattr(api_keys, "revoke_all", lambda *a, **k: None)
    monkeypatch.setattr(api_keys, "create_key", lambda *a, **k: "ahk_secret")
    monkeypatch.setattr(api_keys, "revoke_key", lambda key_id, username=None: True)

    services.issue_api_key("alice", "pw", "public", name="sync")
    services.revoke_api_key("alice", "key-1")

    created, revoked = audit_log[-2:]
    assert created["action"] == "API_KEY_CREATE"
    assert created["metadata"]["api_key"] == {"scope": "public", "name": "sync"}
    assert "ahk_secret" not in str(created), "the key itself must never be logged"
    assert revoked["action"] == "API_KEY_REVOKE"
    assert revoked["metadata"]["api_key"] == {"id": "key-1"}


# ---------------------------------------------------------------------------
# System operations
# ---------------------------------------------------------------------------


def test_a_restart_records_who_asked_for_it(audit_log, monkeypatch):
    from archihub.api.system import services

    monkeypatch.setattr("archihub.core.runtime_restart.request_runtime_restart", lambda reason: 1)

    services.restart_system("root")

    assert (audit_log[-1]["username"], audit_log[-1]["action"]) == ("root", "SYSTEM_RESTART")


def test_clearing_the_cache_records_how_it_was_reached(audit_log, monkeypatch):
    from archihub.api.system import services

    monkeypatch.setattr(
        "archihub.infra.cache.get_cache", lambda: SimpleNamespace(clear_cache=lambda: None)
    )

    services.clear_cache("root")
    services.clear_cache("node-owner", via="node")

    assert [e["metadata"] for e in audit_log[-2:]] == [{"via": "admin"}, {"via": "node"}]


def test_a_queued_maintenance_job_is_recorded_with_its_task(audit_log, monkeypatch):
    from archihub.api.system import services

    monkeypatch.setattr("archihub.api.tasks.services.add_task", lambda *a, **k: None)
    task = SimpleNamespace(delay=lambda *a: SimpleNamespace(id="t-1"))

    services._queue(task, "root", "index_resources", "system.index_resources", "queued")

    entry = audit_log[-1]
    assert entry["action"] == "INDEX_RESOURCES"
    assert entry["metadata"] == {"task": "system.index_resources", "taskId": "t-1"}


def test_a_job_the_broker_refused_is_not_recorded(audit_log):
    from archihub.api.system import services

    def refuse(*_a):
        raise ConnectionError("broker down")

    _payload, status = services._queue(
        SimpleNamespace(delay=refuse), "root", "index_resources", "system.index_resources", "q"
    )

    assert status == 503
    assert audit_log == []


def test_emptying_a_generated_directory_records_how_many_files_went(audit_log, tmp_path, monkeypatch):
    from archihub.api.resources import files

    (tmp_path / "zipfiles").mkdir()
    (tmp_path / "zipfiles" / "a.zip").write_bytes(b"x")
    monkeypatch.setattr(
        "archihub.core.settings.get_settings", lambda: SimpleNamespace(web_files_path=str(tmp_path))
    )

    files.delete_generated("zipfiles", "root")

    assert audit_log[-1]["action"] == "GENERATED_FILES_DELETE"
    assert audit_log[-1]["metadata"] == {"directory": "zipfiles", "removed": 1}


def test_loading_the_boundaries_records_the_shape_count(audit_log, tmp_path, monkeypatch):
    from archihub.api.geosystem import services

    monkeypatch.setattr(services, "geo_data_directory", lambda: tmp_path)
    monkeypatch.setattr(services, "_boundary_levels", lambda directory: [])

    services.upload_shapes("root")

    assert (audit_log[-1]["action"], audit_log[-1]["metadata"]) == ("GEO_LOAD", {"shapes": 0})


# ---------------------------------------------------------------------------
# Background work
# ---------------------------------------------------------------------------


def test_every_plugin_job_a_person_launches_is_recorded(audit_log, monkeypatch):
    from archihub.plugins.framework import base

    monkeypatch.setattr("archihub.api.tasks.services.add_task", lambda *a, **k: None)
    task = SimpleNamespace(delay=lambda *a: SimpleNamespace(id="t-9"))

    base.queue(task, "liquidText.bulk", "alice", "file", {"form": "x" * 10_000})

    entry = audit_log[-1]
    assert (entry["username"], entry["action"]) == ("alice", "PLUGIN_TASK")
    assert entry["metadata"] == {"plugin": "liquidText", "task": "liquidText.bulk", "taskId": "t-9"}


def test_stopping_a_task_is_recorded(audit_log, monkeypatch):
    from archihub.api.tasks import services
    from archihub.worker.celery_app import celery_app

    monkeypatch.setattr(celery_app.control, "revoke", lambda *a, **k: None)
    monkeypatch.setattr(services, "_mongo", lambda: FakeMongo())

    services.stop_task("t-1", "root")

    assert (audit_log[-1]["action"], audit_log[-1]["metadata"]) == ("TASK_CANCEL", {"taskId": "t-1"})


def test_assigning_and_approving_a_review_task_are_recorded(audit_log, monkeypatch):
    from archihub.api.usertasks import services

    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)

    services.create_task({"resourceId": "r1", "user": "ed", "comment": "check it"}, "lead")
    created = audit_log[-1]
    assert created["action"] == "USERTASK_CREATE"
    assert created["metadata"] == {"resource": "r1", "task": VALID_ID, "assignee": "ed"}

    fake.records["usertasks"] = {"_id": ObjectId(VALID_ID), "resourceId": "r1", "status": "pending"}
    services.update_task(VALID_ID, {"status": "approved"}, "lead", is_team_lead=True)
    updated = audit_log[-1]
    assert updated["action"] == "USERTASK_UPDATE"
    assert updated["metadata"]["approved"] is True


# ---------------------------------------------------------------------------
# Corrections to a file's processing output
# ---------------------------------------------------------------------------


def test_editing_a_files_text_blocks_is_recorded(audit_log, monkeypatch):
    from archihub.api.records import blocks

    monkeypatch.setattr(blocks, "_mongo", lambda: FakeMongo())
    monkeypatch.setattr(blocks, "_call_hook", lambda *a, **k: None)

    blocks._write(VALID_ID, "processing.ocr.result.blocks", [], "alice")

    entry = audit_log[-1]
    assert entry["action"] == "RECORD_UPDATE"
    assert entry["metadata"]["record"] == VALID_ID, "the audit screen links the file by this id"
    assert entry["metadata"]["edit"] == "blocks"


def test_correcting_a_transcription_is_recorded(audit_log, monkeypatch):
    from archihub.api.records import transcription

    monkeypatch.setattr(transcription, "_mongo", lambda: FakeMongo())
    monkeypatch.setattr(transcription, "_call_hook", lambda *a, **k: None)
    processing = {"whisper": {"result": {"segments": []}}}

    transcription._save(VALID_ID, processing, "whisper", [], "alice")

    assert audit_log[-1]["metadata"] == {"record": VALID_ID, "edit": "transcription", "processing": "whisper"}
