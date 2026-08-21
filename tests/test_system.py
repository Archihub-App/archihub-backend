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


def test_set_first_time_answers_201(mongo, monkeypatch):
    """The route declares 201; the service tuple is what actually sets it.

    The account creation and the catalogue seeding are stubbed - this pins the
    status onboarding reports, not either of those paths, which have their own
    tests below and in `test_users_lifecycle`.
    """
    import archihub.api.users.services as users_services

    mongo.counts["users"] = 0
    monkeypatch.setattr(services, "set_system_setting", lambda: None)
    monkeypatch.setattr(services, "seed_starter_catalogue", lambda template, user: None)
    monkeypatch.setattr(
        users_services, "register_user", lambda payload: ({"msg": "ok"}, 201)
    )

    _payload, status = services.set_first_time(
        {
            "username": "admin@x.test",
            "password": "one",
            "confirmPassword": "one",
            "typeTemplate": "basic",
        }
    )
    assert status == 201


def test_onboarding_reports_a_catalogue_it_could_not_provision(mongo, monkeypatch):
    """An instance with an administrator and nothing to catalogue into is not
    configured. Onboarding cannot be retried once the account exists, so the
    failure has to say what is missing rather than answer success."""
    import archihub.api.users.services as users_services

    mongo.counts["users"] = 0
    monkeypatch.setattr(services, "set_system_setting", lambda: None)
    monkeypatch.setattr(
        users_services, "register_user", lambda payload: ({"msg": "ok"}, 201)
    )
    monkeypatch.setattr(
        services, "seed_starter_catalogue", lambda template, user: "the form could not be created"
    )

    payload, status = services.set_first_time(
        {
            "username": "admin@x.test",
            "password": "one",
            "confirmPassword": "one",
            "typeTemplate": "basic",
        }
    )

    assert status == 500
    assert "form" in payload["msg"]


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


def _settings_instance(mongo, *groups):
    """Stub an instance whose `system` collection holds these settings groups."""
    mongo.collections["system"] = list(groups)
    by_name = {g["name"]: g for g in groups}
    mongo.records["system"] = lambda filters: by_name.get(filters.get("name"))


def test_only_existing_entries_are_written(mongo):
    """A client must not be able to introduce new settings keys."""
    _settings_instance(mongo, {"name": "g", "data": [{"id": "known", "value": 1}]})

    services.update_settings({"g": {"known": 2, "injected": "x"}}, "admin")

    _collection, _filters, update = mongo.updated[0]
    assert [e["id"] for e in update["data"]] == ["known"]
    assert update["data"][0]["value"] == 2


def test_the_settings_screens_flat_payload_is_applied(mongo):
    """THE SETTINGS SCREEN SENDS NO GROUP NAMES.

    It builds one form from entries drawn out of several groups and submits
    them together as a flat object. Accepting only the grouped shape makes
    every value a scalar, so nothing matches and the route answers 200 having
    written nothing - the screen reports success and redisplays the old values.
    """
    _settings_instance(
        mongo,
        {"name": "api_activation", "data": [{"id": "api_activation_admin", "value": False}]},
        {"name": "index_management", "data": [{"id": "index_activation", "value": False}]},
    )

    services.update_settings(
        {"api_activation_admin": True, "index_activation": True}, "admin"
    )

    written = {
        filters["name"]: {e["id"]: e["value"] for e in update["data"]}
        for _collection, filters, update in mongo.updated
    }
    assert written["api_activation"]["api_activation_admin"] is True
    assert written["index_management"]["index_activation"] is True


def test_a_flat_key_this_instance_does_not_declare_is_not_written(mongo):
    _settings_instance(mongo, {"name": "g", "data": [{"id": "known", "value": 1}]})

    services.update_settings({"invented": "x"}, "admin")

    assert mongo.updated == []


def test_the_plugin_registry_is_not_editable_as_settings(mongo):
    """active_plugins drives what the application loads at startup; it has its
    own guarded route and must not be writable through the generic settings
    endpoint."""
    _settings_instance(mongo, {"name": "active_plugins", "data": ["a-plugin"]})

    services.update_settings({"active_plugins": {"data": ["anything"]}}, "admin")

    assert mongo.updated == []


def test_the_restart_counter_is_not_writable_through_settings(mongo):
    """Every process polls it, so writing it restarts the whole deployment.

    With entry ids resolved to their group, any id in any group would otherwise
    be reachable from the settings form - so the protected groups have to be
    excluded from the lookup itself, not only from the top-level keys.
    """
    _settings_instance(
        mongo,
        {"name": "runtime_control", "data": [{"id": "restart_revision", "value": 7}]},
        {"name": "g", "data": [{"id": "known", "value": 1}]},
    )

    services.update_settings({"restart_revision": 9999}, "admin")

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


# ---------------------------------------------------------------------------
# Onboarding provisions a usable instance, not just an account
# ---------------------------------------------------------------------------


class SeedMongo:
    """A store with just enough behaviour to run the seeder."""

    def __init__(self):
        self.collections: dict[str, list[dict]] = {}
        self._next = 0

    def get_record(self, collection, filters=None, fields=None):
        for row in self.collections.get(collection, []):
            if all(row.get(k) == v for k, v in (filters or {}).items()):
                return row
        return None

    def get_all_records(self, collection, filters=None, **kwargs):
        return list(self.collections.get(collection, []))

    def insert_record(self, collection, record):
        self._next += 1
        row = dict(record)
        row.setdefault("_id", f"id{self._next}")
        self.collections.setdefault(collection, []).append(row)
        return row["_id"]

    def update_record(self, collection, filters, update):
        row = self.get_record(collection, filters)
        if row:
            row.update(update if isinstance(update, dict) else update.model_dump())

    def count(self, collection, filters=None):
        return len(self.collections.get(collection, []))


@pytest.fixture
def seeded(monkeypatch):
    """The seeder over a fake store, with the create services stubbed.

    The services are stubbed rather than run so this stays a test of the
    SEQUENCE - what gets provisioned, and what gets wired to what - which is the
    part that was missing. Each service has its own tests.
    """
    from archihub.api.system import services

    store = SeedMongo()
    monkeypatch.setattr(services, "_mongo", lambda: store)
    services.set_system_setting()

    def make(collection):
        def create(body, user):
            store.insert_record(collection, body)
            return {"msg": "ok"}, 201

        return create

    monkeypatch.setattr("archihub.api.forms.services.create", make("forms"))
    monkeypatch.setattr("archihub.api.types.services.create", make("post_types"))
    monkeypatch.setattr("archihub.api.lists.services.create", make("lists"))
    return store


def test_onboarding_provisions_a_form_a_type_and_the_vocabularies(seeded):
    """An instance with settings and an administrator but none of this cannot
    be used: nothing to catalogue into, and no roles to grant."""
    from archihub.api.system import services

    assert services.seed_starter_catalogue("basic", "admin") is None

    assert [t["slug"] for t in seeded.collections["post_types"]] == ["carpeta"]
    assert [f["slug"] for f in seeded.collections["forms"]] == ["formulario"]
    assert {l["name"] for l in seeded.collections["lists"]} == {"Roles", "Niveles de acceso"}


def test_the_detailed_template_provisions_its_own_set(seeded):
    from archihub.api.system import services

    assert services.seed_starter_catalogue("detailed", "admin") is None

    assert [t["slug"] for t in seeded.collections["post_types"]] == [
        "archivo", "fondo", "unidad-documental"
    ]
    assert [f["slug"] for f in seeded.collections["forms"]] == ["isadg", "dublin-core"]


def test_the_access_rights_setting_is_pointed_at_the_vocabulary_LISTS(seeded):
    """THE STEP WHOSE ABSENCE IS HARDEST TO DIAGNOSE.

    Roles and access levels are resolved by looking up a list id stored in the
    `access_rights` setting. Creating the lists is not enough - without this
    wiring both vocabularies come back empty while the lists themselves sit in
    the database looking perfectly correct.
    """
    from archihub.api.system import services

    services.seed_starter_catalogue("basic", "admin")

    ids = {l["name"]: str(l["_id"]) for l in seeded.collections["lists"]}
    wiring = {
        entry["id"]: entry.get("value")
        for entry in seeded.get_record("system", {"name": "access_rights"})["data"]
        if entry.get("id") in ("user_roles_list", "access_rights_list")
    }

    assert wiring["user_roles_list"] == ids["Roles"]
    assert wiring["access_rights_list"] == ids["Niveles de acceso"]


def test_the_default_cataloguing_type_is_set(seeded):
    """The cataloguing screen routes to `/cataloging/<value>`, so an empty one
    sends it to `/cataloging/undefined`."""
    from archihub.api.system import services

    services.seed_starter_catalogue("basic", "admin")

    values = [
        entry.get("value")
        for entry in seeded.get_record("system", {"name": "post_types_settings"})["data"]
        if entry.get("id") == "tipo_defecto"
    ]
    assert values == ["carpeta"]


def test_forms_are_created_before_the_types_that_name_them(seeded, monkeypatch):
    """A content type names its metadata form by slug, so seeding the type
    first leaves it pointing at something that does not exist yet."""
    from archihub.api.system import services

    order = []
    monkeypatch.setattr(
        "archihub.api.forms.services.create",
        lambda body, user: (order.append("form"), seeded.insert_record("forms", body), ({}, 201))[-1],
    )
    monkeypatch.setattr(
        "archihub.api.types.services.create",
        lambda body, user: (order.append("type"), seeded.insert_record("post_types", body), ({}, 201))[-1],
    )

    services.seed_starter_catalogue("basic", "admin")

    assert order.index("form") < order.index("type")


def test_seeding_twice_does_not_duplicate(seeded):
    """A partially seeded instance must be completable, not doubled."""
    from archihub.api.system import services

    services.seed_starter_catalogue("basic", "admin")
    services.seed_starter_catalogue("basic", "admin")

    assert len(seeded.collections["post_types"]) == 1
    assert len(seeded.collections["forms"]) == 1
    assert len(seeded.collections["lists"]) == 2


def test_an_unknown_template_is_refused(seeded):
    from archihub.api.system import services

    assert services.seed_starter_catalogue("nonsense", "admin") is not None
