"""`GET /system/get-settings` — what the interface is allowed to show.

The frontend reads this before anyone logs in and stores `capabilities` on
`window.APP_CONFIG`. Seven places gate a feature on it with `.includes()`, so a
name missing from the list is a button that is never rendered: no error, nothing
logged, just an interface quietly smaller than the instance it is connected to.

Four of the names come from the instance's own settings rather than from a
plugin, and an earlier revision of this port collected only the plugin half.
Against a normally configured instance that returned `[]`, which hid **all four
download buttons and both AI assistant entry points** — reported as "now I don't
see the Pregúntale a la IA button".

The route had no tests at all, which is the same gap that let F56 through in the
neighbouring `/system/plugins`.
"""

from __future__ import annotations

import pytest

from archihub.api.system import services


@pytest.fixture
def instance(monkeypatch):
    """A configured instance with every optional feature off and no plugins."""
    state = {
        "providers": 0,
        "settings": {
            ("index_management", "index_activation"): False,
            ("index_management", "vector_activation"): False,
            ("files_management", "files_download"): False,
            ("user_management", "user_languages"): "es",
        },
        "plugin_capabilities": [],
    }

    class FakeMongo:
        def count(self, collection, filters=None):
            return state["providers"]

    def fake_setting(name, key, index=None):
        return state["settings"].get((name, key))

    monkeypatch.setattr(services, "is_first_time", lambda: False)
    monkeypatch.setattr(services, "get_setting_value", fake_setting)
    monkeypatch.setattr(services, "_mongo", lambda: FakeMongo())
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_active_plugin_slugs", lambda: ["p"]
    )
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_plugin_info",
        lambda slug: {"capabilities": state["plugin_capabilities"]},
    )
    return state


def _capabilities(_instance) -> list[str]:
    payload, status = services.get_system_settings()
    assert status == 200
    return payload["capabilities"]


def test_nothing_configured_advertises_nothing(instance):
    assert _capabilities(instance) == []


def test_a_configured_provider_advertises_llm(instance):
    """Both "Pregúntale a la IA" entry points are gated on exactly this."""
    instance["providers"] = 1

    assert "llm" in _capabilities(instance)


def test_downloads_being_allowed_is_advertised(instance):
    """Four download buttons across explore, file, gallery and detail."""
    instance["settings"][("files_management", "files_download")] = True

    assert "files_download" in _capabilities(instance)


def test_indexing_and_vectors_are_advertised_separately(instance):
    instance["settings"][("index_management", "index_activation")] = True

    assert "indexing" in _capabilities(instance)
    assert "vector_db" not in _capabilities(instance)

    instance["settings"][("index_management", "vector_activation")] = True
    assert "vector_db" in _capabilities(instance)


def test_plugin_capabilities_and_system_ones_are_both_present(instance):
    """Neither source may shadow the other; the legacy list held both."""
    instance["plugin_capabilities"] = ["forms_data_viz"]
    instance["providers"] = 2

    assert set(_capabilities(instance)) == {"forms_data_viz", "llm"}


def test_a_normally_configured_instance_is_not_empty(instance):
    """The whole failure, stated as one assertion."""
    instance["providers"] = 1
    instance["settings"][("files_management", "files_download")] = True
    instance["settings"][("index_management", "index_activation")] = True

    assert _capabilities(instance) == ["files_download", "indexing", "llm"]


# ---------------------------------------------------------------------------
# One unreadable setting must not cost the others
# ---------------------------------------------------------------------------


def test_a_broken_provider_lookup_does_not_hide_the_download_buttons(monkeypatch, instance):
    """This route bootstraps the login screen; it degrades, it does not fail."""
    instance["settings"][("files_management", "files_download")] = True

    class Broken:
        def count(self, *a, **k):
            raise RuntimeError("mongo is down")

    monkeypatch.setattr(services, "_mongo", lambda: Broken())

    capabilities = _capabilities(instance)
    assert "files_download" in capabilities
    assert "llm" not in capabilities


def test_a_broken_settings_read_still_returns_a_usable_payload(monkeypatch, instance):
    def explode(name, key, index=None):
        if name == "index_management":
            raise RuntimeError("unreadable")
        return instance["settings"].get((name, key))

    monkeypatch.setattr(services, "get_setting_value", explode)
    instance["providers"] = 1

    payload, status = services.get_system_settings()
    assert status == 200
    assert payload["capabilities"] == ["llm"]


def test_broken_plugin_discovery_still_yields_the_system_capabilities(monkeypatch, instance):
    def explode():
        raise RuntimeError("cannot read active plugins")

    monkeypatch.setattr("archihub.plugins.framework.discovery.get_active_plugin_slugs", explode)
    instance["providers"] = 1

    assert _capabilities(instance) == ["llm"]


# ---------------------------------------------------------------------------
# The shape the login screen depends on
# ---------------------------------------------------------------------------


def test_the_payload_carries_only_what_the_login_screen_reads(instance):
    payload, _status = services.get_system_settings()

    assert set(payload) == {"version", "language", "capabilities"}


def test_a_fresh_instance_asks_for_onboarding_instead(monkeypatch, instance):
    monkeypatch.setattr(services, "is_first_time", lambda: True)

    payload, status = services.get_system_settings()
    assert payload == {"first_time": True}
    assert status == 200


def test_the_names_are_the_ones_the_frontend_matches(instance):
    """`upgrade_front` compares these literally; a rename is a silent removal."""
    instance["providers"] = 1
    instance["settings"][("files_management", "files_download")] = True
    instance["settings"][("index_management", "index_activation")] = True
    instance["settings"][("index_management", "vector_activation")] = True

    assert set(_capabilities(instance)) == {"llm", "files_download", "indexing", "vector_db"}
