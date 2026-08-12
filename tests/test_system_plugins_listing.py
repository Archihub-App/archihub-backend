"""`GET /system/plugins` — the payload two screens depend on.

This route had **no test at all**, and it has now broken the frontend twice in
the same way: the payload is a hand-picked subset of each plugin's `plugin_info`,
where the legacy route returned the whole dict, so a field the port did not think
to pick simply vanished.

* **F38** — the port returned a bare array where both callers read
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


@pytest.fixture
def plugins(monkeypatch):
    """Two plugins: one offering screens, one offering none."""
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
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_active_plugin_slugs",
        lambda: ["filesProcessing"],
    )
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_plugin_info",
        lambda slug: info.get(slug, {}),
    )
    monkeypatch.setattr(
        "archihub.plugins.framework.ported_registry.PORTED_PLUGINS",
        frozenset(info),
    )
    return info


def _listing(payload):
    return {entry["slug"]: entry for entry in payload["plugins"]}


def test_the_payload_is_wrapped_not_a_bare_array(plugins):
    """F38. Both callers read `response.plugins`."""
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


def test_type_is_a_list_even_when_the_plugin_declares_something_else(plugins, monkeypatch):
    """`plugin_info` is authored per plugin, including by third parties, so its
    shape is not guaranteed. The listing must not hand the frontend a value it
    cannot `.map` over."""
    monkeypatch.setattr(
        "archihub.plugins.framework.discovery.get_plugin_info",
        lambda slug: {"name": slug, "type": None} if slug == "liquidText" else {"name": slug},
    )

    payload, _status = services.get_plugins()

    for entry in payload["plugins"]:
        assert isinstance(entry["type"], list)


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
