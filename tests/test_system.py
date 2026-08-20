"""Instance settings, onboarding, plugin activation and role resolution."""

from __future__ import annotations

import pytest

from archihub.api.system import services
from archihub.core import roles as roles_module


class FakeMongo:
    def __init__(self):
        self.records: dict = {}
        self.collections: dict[str, list] = {}
        self.counts: dict[str, int] = {}
        self.inserted: list = []
        self.updated: list = []

    def get_record(self, collection, filters, fields=None):
        source = self.records.get(collection)
        return source(filters) if callable(source) else source

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        return list(self.collections.get(collection, []))

    def count(self, collection, filters=None):
        if collection in self.counts:
            return self.counts[collection]
        raise RuntimeError("count not stubbed")

    def insert_record(self, collection, record):
        self.inserted.append((collection, record))

    def update_record(self, collection, filters, update):
        self.updated.append((collection, filters, update))


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    monkeypatch.setattr(roles_module, "_mongo", lambda: fake)
    monkeypatch.setattr(services, "_register_log", lambda *a, **k: None)
    return fake


# ---------------------------------------------------------------------------
# Onboarding - the guard on an unauthenticated route that creates an admin
# ---------------------------------------------------------------------------


def test_onboarding_is_open_only_while_no_user_exists(mongo):
    mongo.counts["users"] = 0
    assert services.is_first_time() is True

    mongo.counts["users"] = 1
    assert services.is_first_time() is False


def test_onboarding_fails_closed_when_the_state_cannot_be_read(mongo):
    """If it cannot be established that the instance is unconfigured, do not
    offer to configure it.

    This gate is the only thing in front of an unauthenticated endpoint that
    creates an administrator.
    """
    # `count` raises because it is not stubbed.
    assert services.is_first_time() is False


def test_set_first_time_is_refused_once_configured(mongo):
    mongo.counts["users"] = 1

    payload, status = services.set_first_time(
        {"username": "a", "password": "p", "confirmPassword": "p", "typeTemplate": "basic"}
    )
    assert status == 400
    assert mongo.inserted == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": "a"},
        {"username": "a", "password": "p", "confirmPassword": "p"},
        {"username": "", "password": "p", "confirmPassword": "p", "typeTemplate": "basic"},
    ],
)
def test_set_first_time_requires_every_field(mongo, body):
    mongo.counts["users"] = 0
    _payload, status = services.set_first_time(body)
    assert status == 400


def test_set_first_time_checks_the_password_confirmation(mongo, monkeypatch):
    """The legacy version accepted the confirmation field and never compared it,
    so a mistyped confirmation silently created the account with the first
    value - on the one account that matters most."""
    mongo.counts["users"] = 0
    monkeypatch.setattr(services, "set_system_setting", lambda: None)

    payload, status = services.set_first_time(
        {"username": "a", "password": "one", "confirmPassword": "two", "typeTemplate": "basic"}
    )
    assert status == 400


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeding_adds_only_unseen_entries(mongo, monkeypatch):
    """An existing group keeps its configured values; only new entries appear.

    That is what lets a release introduce a setting without resetting the ones
    an operator has already chosen.
    """
    monkeypatch.setattr(
        "archihub.api.system.default_settings.settings",
        [{"name": "g", "data": [{"id": "old"}, {"id": "new"}]}],
        raising=False,
    )
    mongo.records["system"] = {"name": "g", "data": [{"id": "old", "value": "configured"}]}

    services.set_system_setting()

    _collection, _filters, update = mongo.updated[0]
    assert [e["id"] for e in update["data"]] == ["old", "new"]
    assert update["data"][0]["value"] == "configured"


def test_seeding_creates_a_missing_group(mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.api.system.default_settings.settings",
        [{"name": "brand_new", "data": [{"id": "a"}]}],
        raising=False,
    )
    mongo.records["system"] = None

    services.set_system_setting()
    assert mongo.inserted[0][1]["name"] == "brand_new"


# ---------------------------------------------------------------------------
# Settings updates
# ---------------------------------------------------------------------------


def test_only_existing_entries_are_written(mongo):
    """A client must not be able to introduce new settings keys."""
    mongo.records["system"] = {"name": "g", "data": [{"id": "known", "value": 1}]}

    services.update_settings({"g": {"known": 2, "injected": "x"}}, "admin")

    _collection, _filters, update = mongo.updated[0]
    assert [e["id"] for e in update["data"]] == ["known"]
    assert update["data"][0]["value"] == 2


def test_the_plugin_registry_is_not_editable_as_settings(mongo):
    """active_plugins drives what the application loads at startup; it has its
    own guarded route and must not be writable through the generic settings
    endpoint."""
    services.update_settings({"active_plugins": {"data": ["anything"]}}, "admin")
    assert mongo.updated == []


def test_settings_lookup_prefers_id_over_position(mongo):
    """The legacy code indexed these arrays positionally, which reads the wrong
    setting as soon as a document is reordered or extended."""
    mongo.records["system"] = {
        "name": "user_management",
        "data": [{"id": "user_languages", "value": "en"}, {"id": "other", "value": "x"}],
    }
    # Position 2 does not exist; the id lookup must still find it.
    assert services.get_setting_value("user_management", "user_languages", 2) == "en"


# ---------------------------------------------------------------------------
# Plugin activation
# ---------------------------------------------------------------------------


def test_activating_a_plugin_that_is_not_installed_is_refused(mongo):
    """The change would only take effect at the next restart, and the instance
    would then refuse to start."""
    payload, status = services.set_plugin_active("ocrProcessing", True, "admin")

    assert status == 404
    assert mongo.updated == []


def test_activating_an_installed_plugin_this_backend_cannot_build_is_refused(mongo, tmp_path, monkeypatch):
    """A different refusal from the one above, and deliberately so: one is
    solved by installing the plugin, the other by adapting it. The reply
    carries the specific reason beside the message the interface shows."""
    monkeypatch.setattr("archihub.plugins.framework.discovery.PLUGIN_ROOT", tmp_path)
    package = tmp_path / "someLegacyPlugin"
    package.mkdir()
    package.joinpath("__init__.py").write_text('plugin_info = {"name": "x"}\n')

    payload, status = services.set_plugin_active("someLegacyPlugin", True, "admin")

    assert status == 400
    assert "build()" in payload["detail"]
    assert mongo.updated == []


def test_activating_a_plugin_copied_into_the_directory_works(mongo, tmp_path, monkeypatch):
    """The reason the check is a directory scan: an operator installs a plugin
    and it becomes activatable, with no change to this backend's source."""
    monkeypatch.setattr("archihub.plugins.framework.discovery.PLUGIN_ROOT", tmp_path)
    package = tmp_path / "brandNewPlugin"
    package.mkdir()
    package.joinpath("__init__.py").write_text(
        'plugin_info = {"name": "x"}\n\n\ndef build():\n    return object()\n'
    )

    payload, status = services.set_plugin_active("brandNewPlugin", True, "admin")

    assert status == 200
    _collection, _filters, update = mongo.updated[0]
    assert update["data"] == ["brandNewPlugin"]


def test_deactivating_is_always_allowed(mongo):
    """Even for an unsupported plugin - that is how an operator unblocks a
    cutover."""
    mongo.records["system"] = {"name": "active_plugins", "data": ["ocrProcessing"]}

    payload, status = services.set_plugin_active("ocrProcessing", False, "admin")

    assert status == 200
    _collection, _filters, update = mongo.updated[0]
    assert update["data"] == []


def test_a_malformed_plugin_slug_is_rejected(mongo):
    payload, status = services.set_plugin_active("../../etc/passwd", True, "admin")
    assert status == 400


# ---------------------------------------------------------------------------
# toggle_plugin - `GET /system/plugins/{slug}`, the ONLY path the admin table
# uses. `PluginsResults.tsx`'s switch calls `SystemService.setActivePlugin`,
# which ignores the checkbox value and asks the backend to flip the state.
# ---------------------------------------------------------------------------


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A plugins directory the toggle route resolves names against."""
    monkeypatch.setattr("archihub.plugins.framework.discovery.PLUGIN_ROOT", tmp_path)

    def install(slug, mountable=True):
        package = tmp_path / slug
        package.mkdir(parents=True, exist_ok=True)
        source = 'plugin_info = {"name": "x"}\n'
        if mountable:
            source += "\n\ndef build():\n    return object()\n"
        package.joinpath("__init__.py").write_text(source)

    return install


def test_toggling_on_a_plugin_that_was_only_just_installed(mongo, installed):
    installed("brandNewPlugin")

    payload, status = services.toggle_plugin("brandNewPlugin", "admin")

    assert status == 200
    _collection, _filters, update = mongo.updated[0]
    assert update["data"] == ["brandNewPlugin"]


def test_toggling_an_unknown_plugin_on_is_a_404(mongo, installed):
    payload, status = services.toggle_plugin("neverHeardOfIt", "admin")

    assert status == 404
    assert mongo.updated == []


def test_toggling_an_active_plugin_OFF_works_even_when_it_is_not_installed(mongo, installed):
    """This is the remedy for an instance that will not start, so it must never
    depend on the plugin being loadable - the case where it is needed is exactly
    the case where it is not."""
    mongo.records["system"] = {"name": "active_plugins", "data": ["ocrProcessing"]}

    payload, status = services.toggle_plugin("ocrProcessing", "admin")

    assert status == 200
    _collection, _filters, update = mongo.updated[0]
    assert update["data"] == []


def test_toggling_on_a_plugin_this_backend_cannot_build_is_refused(mongo, installed):
    installed("someLegacyPlugin", mountable=False)

    payload, status = services.toggle_plugin("someLegacyPlugin", "admin")

    assert status == 400
    assert mongo.updated == []


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------


def test_builtin_roles_exist_without_any_configured_list(mongo, monkeypatch):
    """The application checks these role names in code.

    They are not configurable, and an instance with no roles list must still
    have them - otherwise role assignment rejects everything, including the
    roles onboarding gives the first administrator.
    """
    monkeypatch.setattr(roles_module, "get_roles_id", lambda: None)

    ids = {r["id"] for r in roles_module.get_roles()["options"]}
    assert {"admin", "editor", "user", "processing", "transcriber"} <= ids


def test_configured_roles_are_merged_with_the_builtins(mongo, monkeypatch):
    monkeypatch.setattr(roles_module, "get_roles_id", lambda: "list-id")
    monkeypatch.setattr(
        roles_module, "_list_options", lambda list_id: [{"id": "curator", "term": "Curator"}]
    )

    ids = [r["id"] for r in roles_module.get_roles()["options"]]
    assert "curator" in ids
    assert "admin" in ids


def test_a_configured_role_does_not_duplicate_a_builtin(mongo, monkeypatch):
    monkeypatch.setattr(roles_module, "get_roles_id", lambda: "list-id")
    monkeypatch.setattr(
        roles_module, "_list_options", lambda list_id: [{"id": "admin", "term": "Administrador"}]
    )

    ids = [r["id"] for r in roles_module.get_roles()["options"]]
    assert ids.count("admin") == 1


def test_the_onboarding_roles_all_validate(mongo, monkeypatch):
    """Regression guard: these are exactly the roles set_first_time assigns."""
    monkeypatch.setattr(roles_module, "get_roles_id", lambda: None)

    assert roles_module.verify_roles_exist(
        [{"id": r} for r in ("admin", "editor", "user", "super_editor", "publisher")]
    ) == ["admin", "editor", "user", "super_editor", "publisher"]


def test_unknown_roles_are_rejected_not_dropped(mongo, monkeypatch):
    """Silently dropping one would grant narrower access than the administrator
    believes they configured."""
    monkeypatch.setattr(roles_module, "get_roles_id", lambda: None)

    with pytest.raises(ValueError):
        roles_module.verify_roles_exist([{"id": "root"}])
