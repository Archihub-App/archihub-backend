"""A Mongo cursor is consumed once.

``get_all_records`` returns a pymongo cursor, not a list. Reading it twice
yields nothing the second time - no exception, no warning, just an empty
iteration - so whatever the second pass computes comes back empty and the route
still answers 200.

This shipped. Both ``describe_parents`` implementations built their id map from
the cursor and then derived the icon set from the same name, so every ancestor
came back with ``"icon": None``: a breadcrumb with no icons, on a 200, found by
the diff harness against the legacy backend.

**No unit test could have caught it**, which is the point of this file. Every
fake in the suite returns a `list` from ``get_all_records``, and a list is
re-iterable, so the second pass sees the data the real cursor would not. The
fake here deliberately returns a one-shot iterator, the way pymongo does.
"""

from __future__ import annotations

import pytest

from archihub.api.records import services as record_services
from archihub.api.resources import presentation


class OneShotMongo:
    """``get_all_records`` returns a generator, as the real client effectively does.

    Reading the result twice gives everything then nothing - the behaviour that
    makes this class of bug silent.
    """

    def __init__(self, resources: dict, types: list):
        self.resources = resources
        self.types = types

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        if collection == "post_types":
            slugs = set((filters or {}).get("slug", {}).get("$in") or [])
            return (row for row in self.types if row["slug"] in slugs)

        wanted = {str(o) for o in ((filters or {}).get("_id") or {}).get("$in", [])}
        return (row for key, row in self.resources.items() if key in wanted)

    def get_record(self, collection, filters=None, fields=None):
        return None


PARENT_ID = "6a70b833497d4440325c94b1"

RESOURCES = {
    PARENT_ID: {
        "_id": PARENT_ID,
        "post_type": "carpeta",
        "status": "published",
        "metadata": {"firstLevel": {"title": "A fonds"}},
    }
}
TYPES = [{"slug": "carpeta", "icon": "carpeta-icon"}]


@pytest.fixture
def one_shot(monkeypatch):
    fake = OneShotMongo(RESOURCES, TYPES)
    monkeypatch.setattr(record_services, "_mongo", lambda: fake)
    monkeypatch.setattr(presentation, "_mongo", lambda: fake)
    return fake


def test_a_records_ancestor_keeps_its_icon(one_shot):
    described = record_services._describe_parents([{"id": PARENT_ID, "post_type": "carpeta"}])

    assert described[0]["icon"] == "carpeta-icon"
    assert described[0]["name"] == "A fonds"


def test_a_resources_ancestor_keeps_its_icon(one_shot):
    described = presentation.describe_parents([{"id": PARENT_ID, "post_type": "carpeta"}])

    assert described[0]["icon"] == "carpeta-icon"
    assert described[0]["name"] == "A fonds"


def test_several_ancestors_of_the_same_type_all_keep_it(one_shot):
    """Guards the narrower mistake of materialising only the first row."""
    second = "6a70b833497d4440325c94c9"
    one_shot.resources[second] = {
        "_id": second,
        "post_type": "carpeta",
        "status": "published",
        "metadata": {"firstLevel": {"title": "Another fonds"}},
    }

    described = presentation.describe_parents(
        [{"id": PARENT_ID, "post_type": "carpeta"}, {"id": second, "post_type": "carpeta"}]
    )

    assert [entry["icon"] for entry in described] == ["carpeta-icon", "carpeta-icon"]


def test_the_fake_really_is_single_pass():
    """If this ever fails, every test above has stopped proving anything."""
    fake = OneShotMongo(RESOURCES, TYPES)
    from bson.objectid import ObjectId  # noqa: F401 - only for the filter shape

    rows = fake.get_all_records("resources", {"_id": {"$in": [PARENT_ID]}})

    assert len(list(rows)) == 1
    assert list(rows) == []
