"""`GET /system/plugins` — the payload two screens depend on.

This route had **no test at all**, and it has now broken the frontend twice in
the same way: the payload is a hand-picked subset of each plugin's `plugin_info`,
where the legacy route returned the whole dict, so a field the port did not think
to pick simply vanished.

* the listing must be an object, not a bare array: both callers read
  `response.plugins`.
* **The `type` omission** — `/processing` renders one button per entry of
  `plugin.type` and routes to `/processing/{type}/{slug}`. With the field gone,
  the page died on `undefined.map` before rendering anything.

So the fields are pinned here by consumer, not by taste. Adding a field is free;
removing one needs this file changed, which is the point.
"""

from __future__ import annotations

import pytest

from archihub.api.system import services

#: Read by `/processing` (`src/app/processing/page.tsx`).
PROCESSING_FIELDS = {"slug", "name", "description", "active", "type"}

#: Read by the admin table (`components/organisms/Results/PluginsResults.tsx`).
ADMIN_FIELDS = {"slug", "name", "description", "active"}


#: The two fields added when the plugin set became a directory scan: an
#: operator who installs something has to be told what is wrong with it.
INSTALLATION_FIELDS = {"supported", "installed", "problem"}


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    """A plugins directory with two plugins: one offering screens, one none."""
    info = {
        "filesProcessing": {
            "name": "File processing",
            "description": "Derivatives",
            "version": "0.1",
            "author": "someone",
            "type": ["settings", "bulk"],
        },
        # A real case: `liquidText` declares no screens.
        "liquidText": {"name": "Liquid text", "description": "", "type": []},
    }

    monkeypatch.setattr("archihub.plugins.framework.discovery.PLUGIN_ROOT", tmp_path)
    for slug, declared in info.items():
        package = tmp_path / slug
        package.mkdir()
        (package / "__init__.py").write_text(
            f"plugin_info = {declared!r}\n\n\ndef build():\n    return object()\n"
        )

    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_active_plugin_slugs",
        lambda: ["filesProcessing"],
    )
    return info


def _listing(payload):
    return {entry["slug"]: entry for entry in payload["plugins"]}


def test_the_payload_is_wrapped_not_a_bare_array(plugins):
    """Both callers read `response.plugins`."""
    payload, status = services.get_plugins()

    assert status == 200
    assert isinstance(payload, dict)
    assert isinstance(payload["plugins"], list)


def test_every_field_the_processing_screen_reads_is_present(plugins):
    payload, _status = services.get_plugins()

    for entry in payload["plugins"]:
        assert PROCESSING_FIELDS <= set(entry), (
            f"{entry.get('slug')} is missing {PROCESSING_FIELDS - set(entry)}"
        )


def test_every_field_the_admin_table_reads_is_present(plugins):
    payload, _status = services.get_plugins()

    for entry in payload["plugins"]:
        assert ADMIN_FIELDS <= set(entry)


def test_type_carries_the_screens_the_plugin_declares(plugins):
    payload, _status = services.get_plugins()

    assert _listing(payload)["filesProcessing"]["type"] == ["settings", "bulk"]


def test_a_plugin_with_no_screens_gets_an_empty_list_not_a_missing_field(plugins):
    """The distinction that broke the page: `[]` renders a card with no buttons,
    absent crashes on `undefined.map`."""
    entry = _listing(payload=services.get_plugins()[0])["liquidText"]

    assert entry["type"] == []


def test_type_is_a_list_even_when_the_plugin_declares_something_else(plugins, tmp_path):
    """`plugin_info` is authored per plugin, including by third parties, so its
    shape is not guaranteed. The listing must not hand the frontend a value it
    cannot `.map` over."""
    (tmp_path / "liquidText" / "__init__.py").write_text(
        'plugin_info = {"name": "Liquid text", "type": None}\n\n\ndef build():\n    return object()\n'
    )

    payload, _status = services.get_plugins()

    for entry in payload["plugins"]:
        assert isinstance(entry["type"], list)


def test_a_plugin_copied_into_the_directory_is_listed(plugins, tmp_path):
    """The reason the listing scans rather than reads a list: installing a
    plugin is documented as copying a directory in, and this is the only screen
    it can be activated from."""
    package = tmp_path / "brandNewPlugin"
    package.mkdir()
    package.joinpath("__init__.py").write_text(
        'plugin_info = {"name": "Brand new"}\n\n\ndef build():\n    return object()\n'
    )

    listing = _listing(services.get_plugins()[0])

    assert listing["brandNewPlugin"]["name"] == "Brand new"
    assert listing["brandNewPlugin"]["supported"] is True
    assert listing["brandNewPlugin"]["active"] is False


def test_a_plugin_this_backend_cannot_build_is_listed_with_the_reason(plugins, tmp_path):
    """Reported, not hidden. Someone who has just installed a plugin needs to
    be told what is wrong with it - its absence from the table says nothing."""
    package = tmp_path / "legacyPlugin"
    package.mkdir()
    package.joinpath("__init__.py").write_text(
        'plugin_info = {"name": "Legacy"}\n\n\nclass ExtendedPluginClass:\n    pass\n'
    )

    entry = _listing(services.get_plugins()[0])["legacyPlugin"]

    assert entry["installed"] is True
    assert entry["supported"] is False
    assert "build()" in entry["problem"]


def test_an_active_plugin_removed_from_disk_stays_listed(plugins, monkeypatch):
    """Otherwise it cannot be switched off from the screen that switched it on."""
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_active_plugin_slugs",
        lambda: ["filesProcessing", "deletedFromDisk"],
    )

    entry = _listing(services.get_plugins()[0])["deletedFromDisk"]

    assert entry["active"] is True
    assert entry["installed"] is False
    assert entry["supported"] is False


def test_every_field_the_installation_state_needs_is_present(plugins):
    payload, _status = services.get_plugins()

    for entry in payload["plugins"]:
        assert INSTALLATION_FIELDS <= set(entry)


def test_activation_state_is_reported_per_plugin(plugins):
    listing = _listing(services.get_plugins()[0])

    assert listing["filesProcessing"]["active"] is True
    assert listing["liquidText"]["active"] is False


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (["settings", "bulk"], ["settings", "bulk"]),
        ((), []),
        (None, []),
        # A bare string must NOT be iterated: list("bulk") is
        # ["b","u","l","k"], which renders four buttons pointing at routes
        # that do not exist.
        ("bulk", ["bulk"]),
        ({"settings": True}, []),
        (42, []),
        (["bulk", None, 7], ["bulk"]),
    ],
)
def test_screen_list_never_hands_the_frontend_something_it_cannot_render(declared, expected):
    assert services._screen_list(declared) == expected
