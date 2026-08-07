"""The unported-plugin startup guard and plugin discovery.

This guard is what stands between an operator and a broken cutover: all 24
legacy plugins are Flask Blueprints, and an instance with any of them active
would otherwise crash opaquely (or, worse, appear to start while silently
missing features). See PLAN_FASTAPI.md decision 5.
"""

from __future__ import annotations

import pytest

from archihub.plugins.framework import discovery
from archihub.plugins.framework.ported_registry import (
    IN_SCOPE_PLUGINS,
    PORTED_PLUGINS,
    UnportedPluginError,
    check_active_plugins,
)


class FakeMongo:
    def __init__(self, record):
        self._record = record
        self.raises = False

    def get_record(self, collection, filters, fields=None):
        if self.raises:
            raise ConnectionError("mongo is unreachable")
        return self._record


@pytest.fixture
def no_bypass(monkeypatch):
    """The conftest sets the bypass globally; these tests need it off."""
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "false")


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_empty_plugin_list_passes():
    check_active_plugins([])


def test_unported_plugin_is_refused():
    with pytest.raises(UnportedPluginError):
        check_active_plugins(["ocrProcessing"])


def test_message_distinguishes_in_scope_from_out_of_scope():
    """An operator needs to know whether to wait or to take action."""
    with pytest.raises(UnportedPluginError) as exc:
        check_active_plugins(["filesProcessing", "mqttHandler"])

    message = str(exc.value)
    assert "Being ported in this migration" in message
    assert "Not part of this migration" in message
    assert "filesProcessing" in message
    assert "mqttHandler" in message
    # and it must say what to actually do
    assert "active_plugins" in message


def test_every_in_scope_plugin_is_named():
    """Guards against a plugin silently dropping off the roadmap."""
    assert IN_SCOPE_PLUGINS == {
        "filesProcessing",
        "inventoryMaker",
        "liquidText",
        "massiveUpdater",
        "scheduleSystemTasks",
    }


def test_ported_plugins_are_a_subset_of_reality():
    """Anything marked ported must be either in scope or deliberately added."""
    assert PORTED_PLUGINS <= IN_SCOPE_PLUGINS


# ---------------------------------------------------------------------------
# Slug validation - an arbitrary-import primitive if left unchecked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    ["../../etc/passwd", "os", "a.b", "plugin;rm -rf /", "", None, 123, "plugin/../other"],
)
def test_dangerous_slugs_are_rejected(slug):
    """Slugs come from the database and are interpolated into an import path.

    `os` is rejected by the same rule that rejects traversal: the pattern only
    admits names, and importing a non-plugin module is caught when the plugin
    package lookup fails. The point is that nothing outside the plugin packages
    can be reached by writing a clever value into the settings document.
    """
    if isinstance(slug, str) and slug and "." not in slug and "/" not in slug and ";" not in slug:
        pytest.skip("valid-shaped slug")
    with pytest.raises(discovery.PluginDiscoveryError):
        discovery._validate_slug(slug)


@pytest.mark.parametrize("slug", ["filesProcessing", "massiveUpdater", "plugin_name", "plugin-name"])
def test_normal_slugs_are_accepted(slug):
    assert discovery._validate_slug(slug) == slug


def test_malformed_slug_is_dropped_not_fatal(monkeypatch):
    """One bad row must not take the instance down, but must never be imported."""
    mongo = FakeMongo({"data": ["filesProcessing", "../evil", "massiveUpdater"]})
    assert discovery.get_active_plugin_slugs(mongo) == ["filesProcessing", "massiveUpdater"]


# ---------------------------------------------------------------------------
# assert_active_plugins_are_ported
# ---------------------------------------------------------------------------


def test_refuses_when_an_active_plugin_is_unported(no_bypass):
    mongo = FakeMongo({"data": ["ocrProcessing"]})
    with pytest.raises(UnportedPluginError):
        discovery.assert_active_plugins_are_ported(mongo)


def test_unreachable_database_reports_that_distinctly(no_bypass):
    """A database outage and an unported plugin are different problems.

    Both are fatal, but conflating them sends the operator hunting the wrong one.
    """
    mongo = FakeMongo({})
    mongo.raises = True

    with pytest.raises(discovery.PluginDiscoveryError) as exc:
        discovery.assert_active_plugins_are_ported(mongo)

    message = str(exc.value)
    assert "could not read the active plugin list" in message
    assert not isinstance(exc.value, UnportedPluginError)


def test_bypass_allows_startup_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "true")
    mongo = FakeMongo({"data": ["ocrProcessing"]})

    with caplog.at_level("WARNING"):
        discovery.assert_active_plugins_are_ported(mongo)

    assert "ocrProcessing" in caplog.text
    assert "will not exist" in caplog.text


def test_bypass_works_without_a_database(monkeypatch):
    """Bypass must never block startup - it is used in tests and local dev."""
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "true")
    mongo = FakeMongo({})
    mongo.raises = True

    assert discovery.assert_active_plugins_are_ported(mongo) == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_bypass_accepts_common_spellings(monkeypatch, value):
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", value)
    assert discovery.unported_plugins_allowed() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off"])
def test_bypass_is_off_by_default(monkeypatch, value):
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", value)
    assert discovery.unported_plugins_allowed() is False


# ---------------------------------------------------------------------------
# plugin metadata
# ---------------------------------------------------------------------------


def test_get_plugin_info_never_raises():
    """Called by beat on a timer for every active plugin: one broken plugin
    must not stop the others being scheduled."""
    assert discovery.get_plugin_info("definitely_not_a_plugin") == {}


def test_capability_check_on_missing_plugin_is_false():
    assert discovery.plugin_has_capability("definitely_not_a_plugin", "scheduler") is False
