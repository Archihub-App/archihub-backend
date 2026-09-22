"""How a resource is annotated for display.

The detail response carries the raw ``metadata`` block alongside the rendered
``fields``. They are read by different screens - the viewer reads ``fields``,
the cataloguing form is populated from ``metadata`` - so a value that looks
equivalent in one is not necessarily equivalent in the other.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dates in the raw metadata block
# ---------------------------------------------------------------------------


def test_a_metadata_date_is_iso_not_an_http_date(monkeypatch):
    """The cataloguing form is populated from the raw `metadata` block, and a
    browser reads the two forms as different instants: an ISO string with no
    offset is LOCAL, an HTTP date is UTC. Re-saving an unedited resource would
    otherwise move the value by the viewer's offset.
    """
    import datetime

    from archihub.api.resources import presentation

    monkeypatch.setattr(presentation, "_icon_of", lambda post_type: None)
    monkeypatch.setattr(presentation, "describe_parents", lambda parents: [])
    monkeypatch.setattr(presentation, "child_types", lambda resource_id: [])
    monkeypatch.setattr(presentation, "build_fields", lambda *a, **k: [])

    resource = {
        "_id": "x",
        "post_type": "fondo",
        "metadata": {
            "firstLevel": {
                "title": "T",
                "fecha_registro": datetime.datetime(2025, 10, 24, 5, 0, 0),
            },
            "rows": [{"when": datetime.datetime(2024, 1, 2, 3, 4, 5)}],
        },
    }

    described = presentation.describe(resource, "alice")

    assert described["metadata"]["firstLevel"]["fecha_registro"] == "2025-10-24T05:00:00"
    assert described["metadata"]["rows"][0]["when"] == "2024-01-02T03:04:05"
    assert described["metadata"]["firstLevel"]["title"] == "T"


# ---------------------------------------------------------------------------
# Field kinds contributed by a plugin
# ---------------------------------------------------------------------------


def _plugin_kind_setup(monkeypatch, hook=None):
    from archihub.api.resources import presentation
    from archihub.api.types import services as types_services
    from archihub.core.hooks import get_hook_handler

    fields = [
        {"type": "text", "destiny": "metadata.firstLevel.title", "label": "Title"},
        {"type": "plugin_kind", "plugin": "p", "destiny": "metadata.firstLevel.extra", "label": "Extra"},
    ]
    monkeypatch.setattr(types_services, "get_metadata", lambda post_type: {"fields": fields})

    bus = get_hook_handler()
    monkeypatch.setattr(bus, "hooks", {})
    if hook is not None:
        bus.register("resource_field", hook)

    resource = {"_id": "r", "post_type": "t", "metadata": {"firstLevel": {"title": "T", "extra": [1]}}}
    return presentation.build_fields(resource, "alice")


def test_a_plugin_renders_the_field_kind_it_contributed(monkeypatch):
    def render(resource, field, rendered):
        if field["type"] == "plugin_kind":
            rendered.append({"label": field["label"], "value": [1], "type": "plugin_kind"})
        return rendered

    rendered = _plugin_kind_setup(monkeypatch, render)

    assert [entry["type"] for entry in rendered] == ["text", "plugin_kind"]


def test_a_kind_no_plugin_renders_is_left_out(monkeypatch):
    rendered = _plugin_kind_setup(monkeypatch)

    assert [entry["type"] for entry in rendered] == ["text"]


def test_a_failing_plugin_costs_only_its_own_field(monkeypatch):
    def broken(resource, field, rendered):
        raise RuntimeError("boom")

    rendered = _plugin_kind_setup(monkeypatch, broken)

    assert [entry["type"] for entry in rendered] == ["text"]


def test_a_built_in_kind_is_never_handed_to_a_plugin(monkeypatch):
    seen = []

    def spy(resource, field, rendered):
        seen.append(field["type"])
        return rendered

    _plugin_kind_setup(monkeypatch, spy)

    assert seen == ["plugin_kind"]
