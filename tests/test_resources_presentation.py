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
