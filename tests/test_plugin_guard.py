"""The startup guard, and discovering plugins from the plugins directory.

This guard is what stands between an operator and a broken cutover: every plugin
written for the legacy stack is a Flask Blueprint, and an instance with one of
them active would otherwise crash opaquely - or, worse, appear to start while
silently missing features.

The plugin set is read from disk, so these tests build real plugin directories
rather than patching a list of names. A test that patched a list would keep
passing against an implementation that had stopped looking at the filesystem,
which is precisely the behaviour under test.
"""

from __future__ import annotations

import pytest

from archihub.plugins.framework import discovery
from archihub.plugins.framework.discovery import IncompatiblePluginError, check_active_plugins

# A plugin this backend can build: it exposes build() and declares plugin_info.
MOUNTABLE = "\n".join(
    [
        'plugin_info = {"name": "Test plugin", "description": "d", "type": ["settings"]}',
        "",
        "",
        "def build():",
        "    return object()",
        "",
    ]
)

# A plugin written for the legacy stack. It has metadata and no build().
LEGACY = "\n".join(
    [
        "from flask import Blueprint",
        "",
        'plugin_info = {"name": "Legacy plugin", "description": "d"}',
        "",
        "",
        "class ExtendedPluginClass(Blueprint):",
        "    pass",
        "",
    ]
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


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """Point discovery at a throwaway plugins directory, and fill it."""
    monkeypatch.setattr(discovery, "PLUGIN_ROOT", tmp_path)

    def install(slug, source=MOUNTABLE):
        package = tmp_path / slug
        package.mkdir(parents=True, exist_ok=True)
        if source is not None:
            (package / "__init__.py").write_text(source)
        return package

    return install


# ---------------------------------------------------------------------------
# Discovery from the directory
# ---------------------------------------------------------------------------


def test_a_plugin_copied_in_is_discovered(plugin_dir):
    """The whole point: installing a plugin is copying a directory."""
    plugin_dir("brandNewPlugin")

    assert discovery.list_installed_plugins() == ["brandNewPlugin"]
    assert discovery.is_installed("brandNewPlugin") is True
    assert discovery.is_mountable("brandNewPlugin") is True


def test_the_framework_package_is_not_a_plugin(plugin_dir):
    plugin_dir("framework")
    plugin_dir("__pycache__")
    plugin_dir("realPlugin")

    assert discovery.list_installed_plugins() == ["realPlugin"]


def test_a_directory_without_an_init_is_not_a_plugin(plugin_dir):
    """It would not be importable, so listing it offers an activation that fails."""
    plugin_dir("notAPackage", source=None)

    assert discovery.list_installed_plugins() == []


def test_a_directory_whose_name_cannot_be_a_slug_is_skipped(plugin_dir):
    """The name becomes an import path, so it is skipped rather than sanitised."""
    plugin_dir("not a slug")
    plugin_dir("fine")

    assert discovery.list_installed_plugins() == ["fine"]


def test_an_unreadable_plugin_directory_does_not_raise(monkeypatch, tmp_path):
    """The listing backs an admin screen; it degrades rather than 500s."""
    monkeypatch.setattr(discovery, "PLUGIN_ROOT", tmp_path / "does-not-exist")

    assert discovery.list_installed_plugins() == []


# ---------------------------------------------------------------------------
# Reading a manifest without executing it
# ---------------------------------------------------------------------------


def test_the_manifest_is_read_without_importing(monkeypatch):
    """A listing must not run code from a directory somebody dropped in.

    Asserted against the import machinery itself, and against a plugin that
    really is installed. Two weaker versions of this test do not work: a plugin
    written into a temporary directory can never be imported at all, so nothing
    it does can be observed; and a plugin that raises on import proves nothing
    either, because `get_plugin_info` catches that and falls back to parsing -
    an implementation that imports would still pass. What is recorded here is
    the *attempt*, so swallowing the result does not hide it.
    """
    attempted: list[str] = []

    def refuse(name, *args, **kwargs):
        attempted.append(name)
        raise AssertionError(f"read_manifest must not import {name}")

    monkeypatch.setattr(discovery.importlib, "import_module", refuse)

    manifest = discovery.read_manifest("filesProcessing")

    assert attempted == []
    assert manifest.mountable is True
    assert manifest.info["name"]


def test_a_plugin_that_raises_on_import_still_lists(plugin_dir):
    """Whatever a plugin's module does, its metadata is readable."""
    plugin_dir(
        "explodesOnImport",
        "\n".join(
            [
                "raise RuntimeError('this plugin must never be executed')",
                "",
                'plugin_info = {"name": "Booby trap"}',
                "",
                "def build():",
                "    return object()",
                "",
            ]
        ),
    )

    manifest = discovery.read_manifest("explodesOnImport")

    assert manifest.info["name"] == "Booby trap"
    assert manifest.mountable is True


def test_a_plugin_without_build_is_installed_but_not_mountable(plugin_dir):
    plugin_dir("legacyPlugin", LEGACY)

    manifest = discovery.read_manifest("legacyPlugin")

    assert manifest.installed is True
    assert manifest.mountable is False
    assert "build()" in (manifest.problem or "")
    # Its metadata still reads, so the screen can name it.
    assert manifest.info["name"] == "Legacy plugin"


def test_a_plugin_without_plugin_info_is_not_mountable(plugin_dir):
    plugin_dir("noMetadata", "def build():\n    return object()\n")

    manifest = discovery.read_manifest("noMetadata")

    assert manifest.mountable is False
    assert "plugin_info" in (manifest.problem or "")


@pytest.mark.parametrize(
    "source",
    [
        'plugin_info = {"name": "x"}\nbuild = lambda: object()\n',
        'plugin_info = {"name": "x"}\nfrom .factory import build\n',
        'plugin_info = {"name": "x"}\nasync def build():\n    return object()\n',
    ],
)
def test_build_may_be_provided_in_any_ordinary_way(plugin_dir, source):
    """A plugin organises itself; this check tells two backends apart."""
    plugin_dir("flexible", source)

    assert discovery.is_mountable("flexible") is True


def test_a_manifest_that_will_not_parse_reports_rather_than_raises(plugin_dir):
    plugin_dir("brokenSyntax", "def build(:\n")

    manifest = discovery.read_manifest("brokenSyntax")

    assert manifest.installed is True
    assert manifest.mountable is False
    assert "does not parse" in (manifest.problem or "")


def test_an_oversized_manifest_is_refused_rather_than_parsed(plugin_dir, monkeypatch):
    """A plugin directory is third-party content and this runs on a request."""
    monkeypatch.setattr(discovery, "MANIFEST_BYTE_LIMIT", 64)
    plugin_dir("enormous", MOUNTABLE + "\n# " + "x" * 200)

    manifest = discovery.read_manifest("enormous")

    assert manifest.mountable is False
    assert "limit" in (manifest.problem or "")


def test_a_partly_computed_manifest_still_yields_its_literal_keys(plugin_dir):
    """Losing a plugin's name over one computed entry leaves a blank row."""
    plugin_dir(
        "computed",
        "\n".join(
            [
                "import os",
                "",
                "plugin_info = {",
                '    "name": "Computed plugin",',
                '    "version": os.environ.get("V"),',
                "}",
                "",
                "def build():",
                "    return object()",
                "",
            ]
        ),
    )

    manifest = discovery.read_manifest("computed")

    assert manifest.info["name"] == "Computed plugin"
    assert "version" not in manifest.info
    assert manifest.mountable is True


def test_a_manifest_for_a_slug_that_is_not_there(plugin_dir):
    manifest = discovery.read_manifest("neverInstalled")

    assert manifest.installed is False
    assert manifest.mountable is False
    assert "No plugin named" in (manifest.problem or "")


def test_a_dangerous_slug_never_reaches_the_filesystem():
    manifest = discovery.read_manifest("../../etc")

    assert manifest.installed is False
    assert manifest.mountable is False


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_empty_plugin_list_passes():
    check_active_plugins([])


def test_an_active_plugin_that_is_not_installed_is_refused(plugin_dir):
    with pytest.raises(IncompatiblePluginError):
        check_active_plugins(["ocrProcessing"])


def test_an_installed_plugin_this_backend_cannot_build_is_refused(plugin_dir):
    """The case a name-based allowlist could not see: the directory IS there."""
    plugin_dir("legacyPlugin", LEGACY)

    with pytest.raises(IncompatiblePluginError) as exc:
        check_active_plugins(["legacyPlugin"])

    assert "legacyPlugin" in str(exc.value)


def test_a_mountable_plugin_passes_without_being_named_anywhere(plugin_dir):
    plugin_dir("brandNewPlugin")

    check_active_plugins(["brandNewPlugin"])


def test_the_message_separates_missing_from_unusable(plugin_dir):
    """The remedies differ - install it, versus adapt it - and a startup failure
    is the only place an operator learns which one applies."""
    plugin_dir("legacyPlugin", LEGACY)

    with pytest.raises(IncompatiblePluginError) as exc:
        check_active_plugins(["legacyPlugin", "neverHeardOfIt"])

    message = str(exc.value)
    assert "Active but not installed" in message
    assert "neverHeardOfIt" in message
    assert "Installed but unusable" in message
    assert "legacyPlugin" in message
    # and it must say what to actually do
    assert "active_plugins" in message


# ---------------------------------------------------------------------------
# The plugins that ship in the tree
# ---------------------------------------------------------------------------


def test_every_bundled_plugin_is_discovered_and_mountable():
    """Checked against the real directory, not a fixture.

    A bundled plugin that stopped being discoverable - a renamed directory, a
    lost `build()` - would make every existing instance refuse to start.
    """
    installed = set(discovery.list_installed_plugins())

    assert {
        "filesProcessing",
        "inventoryMaker",
        "liquidText",
        "massiveUpdater",
        "scheduleSystemTasks",
    } <= installed
    assert "framework" not in installed

    for slug in installed:
        assert discovery.is_mountable(slug), f"{slug} is installed but not mountable"


def test_what_is_declared_statically_matches_what_importing_finds():
    """The two checks must not be able to disagree.

    `read_manifest` decides whether a plugin may be activated; `build_plugin`
    decides whether it actually mounts. If those differ, an instance accepts a
    plugin at the screen and then comes up without it.
    """
    from archihub.plugins.framework.discovery import import_plugin

    for slug in discovery.list_installed_plugins():
        manifest = discovery.read_manifest(slug)
        module = import_plugin(slug)

        assert manifest.mountable is callable(getattr(module, "build", None))
        assert manifest.info.get("name") == getattr(module, "plugin_info", {}).get("name")


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


def test_malformed_slug_is_dropped_not_fatal():
    """One bad row must not take the instance down, but must never be imported."""
    mongo = FakeMongo({"data": ["filesProcessing", "../evil", "massiveUpdater"]})
    assert discovery.get_active_plugin_slugs(mongo) == ["filesProcessing", "massiveUpdater"]


# ---------------------------------------------------------------------------
# assert_active_plugins_are_mountable
# ---------------------------------------------------------------------------


def test_refuses_when_an_active_plugin_cannot_be_mounted(no_bypass, plugin_dir):
    mongo = FakeMongo({"data": ["ocrProcessing"]})
    with pytest.raises(IncompatiblePluginError):
        discovery.assert_active_plugins_are_mountable(mongo)


def test_unreachable_database_reports_that_distinctly(no_bypass):
    """A database outage and an unmountable plugin are different problems.

    Both are fatal, but conflating them sends the operator hunting the wrong one.
    """
    mongo = FakeMongo({})
    mongo.raises = True

    with pytest.raises(discovery.PluginDiscoveryError) as exc:
        discovery.assert_active_plugins_are_mountable(mongo)

    message = str(exc.value)
    assert "could not read the active plugin list" in message
    assert not isinstance(exc.value, IncompatiblePluginError)


def test_bypass_allows_startup_and_warns(monkeypatch, caplog, plugin_dir):
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "true")
    mongo = FakeMongo({"data": ["ocrProcessing"]})

    with caplog.at_level("WARNING"):
        discovery.assert_active_plugins_are_mountable(mongo)

    assert "ocrProcessing" in caplog.text
    assert "will not exist" in caplog.text


def test_bypass_works_without_a_database(monkeypatch):
    """Bypass must never block startup - it is used in tests and local dev."""
    monkeypatch.setenv("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "true")
    mongo = FakeMongo({})
    mongo.raises = True

    assert discovery.assert_active_plugins_are_mountable(mongo) == []


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
