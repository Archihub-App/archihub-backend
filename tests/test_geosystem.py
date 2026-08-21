"""Administrative boundary shapes.

Both routes are unauthenticated, so the tests concentrate on what an anonymous
caller can make the server do:  (request values became Mongo
operators, unbounded result sets, and a disk cache keyed on a client float.
"""

from __future__ import annotations

import pytest
from bson.objectid import ObjectId

from archihub.api.geosystem import services, simplify

SHAPE_ID = "6a70b833497d4440325c94b1"


def square(x=0.0, y=0.0, size=10.0):
    return {
        "type": "Polygon",
        "coordinates": [
            [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]
        ],
    }


class FakeMongo:
    def __init__(self):
        self.shapes: list[dict] = []
        self.queries: list[dict] = []
        self.limits: list[int] = []

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        self.queries.append(filters or {})
        self.limits.append(limit)
        rows = [dict(s) for s in self.shapes]
        return rows[:limit] if limit else rows

    def get_record(self, collection, filters=None, fields=None):
        self.queries.append(filters or {})
        return dict(self.shapes[0]) if self.shapes else None


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(services, "_mongo", lambda: fake)
    return fake


@pytest.fixture
def no_cache(monkeypatch):
    """Simplification without touching the filesystem."""
    monkeypatch.setattr(simplify, "_cache_directory", lambda: None)


def shape_document(**overrides):
    document = {
        "_id": ObjectId(SHAPE_ID),
        "geometry": square(),
        "properties": {"name": "Antioquia", "ident": "05", "admin_level": 1},
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# Nothing from the request becomes a query operator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"ident": {"$ne": None}, "level": 1},
        {"parent": {"$exists": True}, "level": 1},
        {"type": {"$ne": None}, "level": 1},
        {"ident": ["05"], "level": 1},
        {"parent": {"$regex": "(a+)+$"}, "level": 1},
    ],
)
def test_an_operator_in_place_of_an_identifier_is_refused(mongo, no_cache, payload):
    """The originals assigned these into the filter as-is, so a JSON object arrived
    as a Mongo operator - `{"ident": {"$ne": null}}` returns every shape in the
    collection and simplifies all of them, with no account needed.
    """
    result, status = services.get_shape(payload)

    assert status == 400
    assert mongo.queries == []


def test_an_operator_in_the_level_query_is_refused_too(mongo, no_cache):
    result, status = services.get_level({"level": 1, "parent": {"$ne": None}})

    assert status == 400
    assert mongo.queries == []


def test_a_plain_identifier_still_works(mongo, no_cache):
    mongo.shapes = [shape_document()]

    result, status = services.get_shape({"ident": "05", "level": 1})

    assert status == 200
    assert mongo.queries[0]["properties.ident"] == "05"


# ---------------------------------------------------------------------------
# Bounded work
# ---------------------------------------------------------------------------


def test_a_listing_is_capped(mongo, no_cache):
    """The original returned every match, simplified, to an anonymous caller."""
    mongo.shapes = [shape_document() for _ in range(50)]

    services.get_shape({"level": 1, "parent": "CO"})

    assert mongo.limits[0] == services.MAX_SHAPES


def test_the_level_query_is_capped_too(mongo, no_cache):
    mongo.shapes = [shape_document()]

    services.get_level({"level": 1})

    assert mongo.limits[0] == services.MAX_SHAPES


@pytest.mark.parametrize(
    "given,expected",
    [
        (0.1, 0.1),
        (0.100001, 0.1),
        (0.999, 1.0),
        (5, 1.0),
        (0, 0.01),
        (-3, 0.01),
        ("0.25", 0.25),
        ("nonsense", 0.1),
        (None, 0.1),
        (float("nan"), 0.1),
        (float("inf"), 0.1),
    ],
)
def test_retention_is_clamped_and_quantised(given, expected):
    """It keys a disk cache. Taking the float verbatim is one file per value."""
    assert simplify.normalise_retention(given) == expected


def test_a_walk_of_retention_values_collapses_to_few_cache_keys():
    values = {simplify.normalise_retention(0.1 + n * 1e-6) for n in range(1000)}

    assert len(values) == 1


# ---------------------------------------------------------------------------
# Levels and bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [-1, 99, "many", None, {"$gt": 0}, 1.5e400])
def test_an_unusable_level_is_a_400(mongo, no_cache, level):
    result, status = services.get_level({"level": level})

    assert status == 400


def test_level_zero_is_valid(mongo, no_cache):
    mongo.shapes = [shape_document()]

    _result, status = services.get_level({"level": 0})

    assert status == 200


@pytest.mark.parametrize(
    "bounds",
    [
        {"minLng": "a", "maxLng": 1, "minLat": 0, "maxLat": 1},
        {"minLng": 0, "maxLng": 1, "minLat": 0},
        {"minLng": 0, "maxLng": 1, "minLat": 0, "maxLat": 1000},
        {"minLng": 5, "maxLng": 1, "minLat": 0, "maxLat": 1},
        {"minLng": 0, "maxLng": 0, "minLat": 0, "maxLat": 1},
        "everything",
    ],
)
def test_unusable_bounds_are_a_400_not_a_driver_error(mongo, no_cache, bounds):
    """These become the coordinates of a $geoIntersects polygon."""
    result, status = services.get_level({"level": 1, "bounds": bounds})

    assert status == 400
    assert mongo.queries == []


def test_bounds_become_a_viewport_intersection(mongo, no_cache):
    mongo.shapes = [shape_document()]

    services.get_level(
        {"level": 1, "bounds": {"minLng": -75, "maxLng": -74, "minLat": 5, "maxLat": 6}}
    )

    assert "$geoIntersects" in mongo.queries[0]["geometry"]


def test_a_mid_zoom_viewport_bounds_the_level_range_at_both_ends(mongo, no_cache):
    """The original wrote `$gte` and then overwrote the same key with `$lt`.

    Only the upper bound survived, so levels *below* the requested one came back.
    """
    mongo.shapes = [shape_document()]

    services.get_level(
        {"level": 1, "bounds": {"minLng": 0, "maxLng": 10, "minLat": 0, "maxLat": 10}}
    )

    assert mongo.queries[0]["properties.admin_level"] == {"$gte": 1, "$lt": 3}


def test_a_close_viewport_drops_to_the_finest_level(mongo, no_cache):
    mongo.shapes = [shape_document()]

    services.get_level(
        {"level": 0, "bounds": {"minLng": 0, "maxLng": 2, "minLat": 0, "maxLat": 2}}
    )

    assert mongo.queries[0]["properties.admin_level"] == 2


def test_a_negative_area_threshold_is_refused(mongo, no_cache):
    result, status = services.get_level({"level": 1, "area_threshold": -5})

    assert status == 400


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


def test_slivers_below_the_threshold_are_dropped(mongo, no_cache):
    """At national zoom an offshore islet is cost with no visible result."""
    mongo.shapes = [shape_document(geometry=square(size=0.1))]

    result, status = services.get_level({"level": 1, "area_threshold": 5})

    assert (result, status) == ([], 200)


def test_a_shape_gets_a_centroid(mongo, no_cache):
    mongo.shapes = [shape_document()]

    result, _status = services.get_level({"level": 1})

    assert result[0]["centroid"]["type"] == "Point"
    assert "_id" not in result[0]


def test_a_malformed_stored_geometry_drops_out_rather_than_failing_the_map(mongo, no_cache):
    """The original wrapped everything in one try/except and 500'd the request."""
    mongo.shapes = [shape_document(geometry={"type": "Polygon", "coordinates": "broken"})]

    result, status = services.get_level({"level": 1})

    assert (result, status) == ([], 200)


def test_a_point_is_not_a_boundary_and_is_skipped(mongo, no_cache):
    mongo.shapes = [shape_document(geometry={"type": "Point", "coordinates": [0, 0]})]

    result, status = services.get_level({"level": 1})

    assert (result, status) == ([], 200)


def test_an_unknown_ident_is_a_404(mongo, no_cache):
    result, status = services.get_shape({"ident": "nope", "level": 1})

    assert status == 404


def test_a_shape_query_without_a_level_is_refused(mongo, no_cache):
    result, status = services.get_shape({"ident": "05"})

    assert status == 400


def test_administrative_turns_the_ident_into_the_parent(mongo, no_cache):
    """The special request meaning "the first-level divisions of this shape"."""
    mongo.shapes = [shape_document()]

    services.get_shape({"ident": "CO", "type": "administrative"})

    query = mongo.queries[0]
    assert query["properties.parent"] == "CO"
    assert query["properties.admin_level"] == 1
    assert "properties.ident" not in query
    assert query["properties.shape_type"] == {"$exists": False}


# ---------------------------------------------------------------------------
# Geometry coercion
# ---------------------------------------------------------------------------


def test_a_closed_line_becomes_a_polygon():
    """Boundary data arrives as lines as often as areas; the map draws regions."""
    feature = {
        "geometry": {
            "type": "LineString",
            "coordinates": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
        }
    }

    assert services.ensure_polygon(feature)["geometry"]["type"] == "Polygon"


def test_an_open_line_is_closed_before_polygonising():
    feature = {
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    }

    assert services.ensure_polygon(feature)["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_a_polygon_is_left_as_a_polygon():
    assert services.ensure_polygon({"geometry": square()})["geometry"]["type"] == "Polygon"


def test_a_feature_with_no_geometry_is_returned_untouched():
    assert services.ensure_polygon({"properties": {}}) == {"properties": {}}


def test_an_unusable_geometry_does_not_raise():
    feature = {"geometry": {"type": "Polygon", "coordinates": "broken"}}

    assert services.ensure_polygon(feature) == feature


# ---------------------------------------------------------------------------
# The simplification cache
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    directory = tmp_path / "geojson"
    directory.mkdir()
    monkeypatch.setattr(simplify, "_cache_directory", lambda: directory)
    return directory


def collection():
    return {"type": "FeatureCollection", "features": [{"geometry": square()}]}


def test_a_simplified_result_is_cached_and_reused(cache_dir):
    first = simplify.simplify(collection(), 0.5)
    written = list(cache_dir.iterdir())

    second = simplify.simplify(collection(), 0.5)

    assert len(written) == 1
    assert first == second


def test_two_retentions_are_two_cache_entries_not_two_thousand(cache_dir):
    for offset in range(20):
        simplify.simplify(collection(), 0.5 + offset * 1e-7)
    simplify.simplify(collection(), 0.9)

    assert len(list(cache_dir.iterdir())) == 2


def test_the_input_is_not_mutated(cache_dir):
    original = collection()
    before = len(original["features"][0]["geometry"]["coordinates"][0])

    simplify.simplify(original, 0.1)

    assert len(original["features"][0]["geometry"]["coordinates"][0]) == before


def test_an_unreadable_cache_entry_is_discarded_not_served(cache_dir):
    simplify.simplify(collection(), 0.5)
    entry = next(iter(cache_dir.iterdir()))
    entry.write_text("{ not json")

    result = simplify.simplify(collection(), 0.5)

    assert result["features"][0]["geometry"]["type"] == "Polygon"


def test_stale_cache_entries_are_swept(cache_dir):
    import os
    import time

    stale = cache_dir / f"{simplify.CACHE_PREFIX}old.geojson"
    other = cache_dir / "not-ours.geojson"
    stale.write_text("{}")
    other.write_text("{}")
    old = time.time() - simplify.STALE_CACHE_SECONDS - 60
    os.utime(stale, (old, old))
    os.utime(other, (old, old))

    assert simplify.sweep_stale_cache(cache_dir) == 1
    assert not stale.exists()
    assert other.exists()


def test_simplification_reduces_vertices():
    ring = [[i * 0.01, (i % 7) * 0.01] for i in range(200)]
    ring.append(ring[0])
    source = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Polygon", "coordinates": [ring]}}],
    }

    result = simplify.simplify(source, 0.1)

    assert len(result["features"][0]["geometry"]["coordinates"][0]) < len(ring)


def test_simplification_never_destroys_a_ring():
    """A minimum vertex count keeps a shape from vanishing entirely."""
    ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    source = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Polygon", "coordinates": [ring]}}],
    }

    result = simplify.simplify(source, 0.01)

    assert len(result["features"][0]["geometry"]["coordinates"][0]) >= 4
