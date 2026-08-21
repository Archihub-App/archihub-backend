"""Loading the bundled administrative boundaries.

Two of these tests exist because the legacy loader could never have worked
against the data the application ships with, and one because whether it worked
depended on the order the filesystem happened to return directories in. Neither
is visible from a route inventory or a status-code assertion.
"""

from __future__ import annotations

import json

import pytest

from archihub.api.geosystem import services


@pytest.fixture
def boundary_tree(tmp_path):
    """A directory shaped like the real one - INCLUDING ``world.json``."""
    for level in (0, 1, 2):
        folder = tmp_path / f"admin_{level}"
        folder.mkdir()
        (folder / "data.json").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    # The file that broke the original loader.
    (tmp_path / "world.json").write_text("{}")
    return tmp_path


def test_a_plain_file_beside_the_level_directories_does_not_stop_the_load(boundary_tree):
    """`int(f.split('admin_')[1])` over `os.listdir`
    raised IndexError on `world.json` before reading anything, so
    `/system/geo-load` answered 500 against the shipped data."""
    levels = services._boundary_levels(boundary_tree)

    assert [level for level, _ in levels] == [0, 1, 2]


def test_levels_are_loaded_lowest_first(tmp_path):
    """Each level is matched to a parent at the level above, which must already
    be in the database. `os.listdir` order made that luck."""
    for level in (2, 0, 1):
        (tmp_path / f"admin_{level}").mkdir()

    assert [level for level, _ in services._boundary_levels(tmp_path)] == [0, 1, 2]


def test_a_stray_directory_is_ignored(tmp_path):
    (tmp_path / "admin_0").mkdir()
    (tmp_path / "backup_of_admin_1").mkdir()
    (tmp_path / "notes").mkdir()

    assert [name.name for _, name in services._boundary_levels(tmp_path)] == ["admin_0"]


def test_a_missing_data_directory_is_a_500_that_does_not_name_the_path(monkeypatch, tmp_path):
    monkeypatch.setattr(services, "geo_data_directory", lambda: tmp_path / "nowhere")

    payload, status = services.upload_shapes()

    assert status == 500
    assert "nowhere" not in payload["msg"]


def test_the_data_directory_is_resolved_from_the_package_not_the_cwd(monkeypatch):
    """The original did `os.path.abspath('app/utils/geo')`, so under gunicorn
    with any other working directory it resolved somewhere that does not exist
    and the route answered 500 with a bare FileNotFoundError."""
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    resolved = services.geo_data_directory()
    get_settings.cache_clear()

    assert resolved.is_absolute()
    assert resolved.name == "geo"


def test_an_operator_may_point_the_loader_at_their_own_boundary_set(monkeypatch, tmp_path):
    monkeypatch.setenv("GEO_DATA_PATH", str(tmp_path))
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    try:
        assert services.geo_data_directory() == tmp_path
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Centroids
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, shapes=()):
        self.shapes = list(shapes)

    def get_record(self, collection, filters=None, fields=None):
        for shape in self.shapes:
            if all(_get(shape, k) == v for k, v in (filters or {}).items()):
                return shape
        return None


def _get(document, path):
    value = document
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def test_a_boundary_this_instance_has_not_loaded_returns_none(monkeypatch):
    """The original raised for any failure including a simple miss, and that
    exception reached the indexer's swallow-everything handler - so a resource
    referring to an unknown boundary silently vanished from the search index."""
    monkeypatch.setattr(services, "_mongo", lambda: FakeMongo())

    assert services.get_shape_centroid("XX", None, 1) is None


def test_a_multipolygon_yields_one_centroid_per_part(monkeypatch):
    """An archipelago's overall centroid can fall in open water, hundreds of
    kilometres from any of its land."""
    shape = {
        "properties": {"admin_level": 1, "ident": "IS"},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
                [[[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]],
            ],
        },
    }
    monkeypatch.setattr(services, "_mongo", lambda: FakeMongo([shape]))

    centroids = services.get_shape_centroid("IS", None, 1)

    assert len(centroids) == 2
    assert centroids[0]["coordinates"] == (0.5, 0.5)


def test_an_empty_identifier_is_answered_without_a_query(monkeypatch):
    def explode():
        raise AssertionError("should not have queried")

    monkeypatch.setattr(services, "_mongo", explode)

    assert services.get_shape_centroid("", None, 1) is None


# ---------------------------------------------------------------------------
# Repairing invalid geometry
#
# Published boundary sets contain self-intersecting rings. One such polygon
# disables the whole map layer twice over: MongoDB will not build the 2dsphere
# index while any document holds geometry it cannot index, so every viewport
# query becomes a collection scan; and Elasticsearch rejects the document, so
# that boundary is absent from the map while the indexing task still reports
# success.
# ---------------------------------------------------------------------------


def _frame(*geometries):
    """A GeoDataFrame shaped like one built from a boundary file."""
    gpd = pytest.importorskip("geopandas")
    return gpd.GeoDataFrame(
        {"name": [f"shape-{i}" for i in range(len(geometries))]},
        geometry=list(geometries),
        crs="EPSG:4326",
    ), gpd


def _bowtie():
    """A self-intersecting ring - the shape published boundary data contains."""
    from shapely.geometry import Polygon

    return Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])


def _square(offset=0):
    from shapely.geometry import Polygon

    return Polygon([(offset, 0), (offset + 1, 0), (offset + 1, 1), (offset, 1)])


def test_an_invalid_geometry_is_actually_repaired_in_the_returned_frame():
    """The repair must reach the data, not only the log line.

    Writing through `.geometry.iloc[i]` updates a copy under pandas'
    copy-on-write: every repair is reported as done and nothing changes. That
    failure is silent in both directions - the loader says it repaired the
    boundary and the database still holds the broken one.
    """
    features, gpd = _frame(_bowtie())
    assert not features.geometry.iloc[0].is_valid

    result = services._repair_invalid_geometries(features, "world.json", gpd)

    assert len(result) == 1
    assert result.geometry.iloc[0].is_valid, "the repaired geometry was not written back"


def test_a_valid_geometry_is_left_alone():
    features, gpd = _frame(_square())
    original = features.geometry.iloc[0]

    result = services._repair_invalid_geometries(features, "world.json", gpd)

    assert result.geometry.iloc[0].equals(original)


def _spiked_square():
    """A square with a zero-width protrusion.

    Chosen because ``make_valid`` really does answer this one with a
    GeometryCollection holding the square AND the spike as lines - a bowtie
    repairs straight to a MultiPolygon and never exercises that path.
    """
    from shapely.geometry import Polygon

    return Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0), (4, 4), (0, 0)])


def _collinear_ring():
    """A ring whose points are all on one line. Repairs to a MultiLineString."""
    from shapely.geometry import Polygon

    return Polygon([(0, 0), (1, 1), (2, 2), (0, 0)])


def test_only_the_area_parts_of_a_repair_are_kept():
    """A boundary is an area.

    Repairing a self-intersection can yield a collection that also holds the
    offending lines; storing one would mean something other than what was
    published.
    """
    features, gpd = _frame(_spiked_square())

    result = services._repair_invalid_geometries(features, "world.json", gpd)

    assert len(result) == 1
    assert result.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon")


def test_a_repair_that_yields_a_line_is_not_stored_as_a_boundary():
    """Validity alone is the wrong test, and this is why.

    A ring of collinear points repairs to a MultiLineString, which is a
    perfectly valid geometry - so a check that only asks "is it valid now?"
    accepts it, and a line is stored as though it were a region. Both databases
    then index it happily and every area query over that region is wrong.
    """
    features, gpd = _frame(_collinear_ring())

    result = services._repair_invalid_geometries(features, "world.json", gpd)

    assert len(result) == 0, "a line was kept as a boundary"


def test_a_geometry_that_cannot_be_repaired_is_dropped_not_stored(caplog):
    """Storing it would block the 2dsphere index for every other boundary."""
    from shapely.geometry import Polygon

    features, gpd = _frame(_square(), Polygon())

    result = services._repair_invalid_geometries(features, "world.json", gpd)

    assert len(result) == 1
    assert all(geometry.is_valid and not geometry.is_empty for geometry in result.geometry)


def test_the_repair_names_what_it_changed(caplog):
    """A silent edit to published source data is not checkable."""
    import logging

    features, gpd = _frame(_bowtie())
    with caplog.at_level(logging.WARNING):
        services._repair_invalid_geometries(features, "world.json", gpd)

    assert "shape-0" in caplog.text
    assert "world.json" in caplog.text


def test_the_shapes_collection_is_indexed_for_the_queries_the_map_runs():
    """Both are needed and they serve different halves of the same request.

    The collection is empty until the boundaries are loaded, which is why its
    indexes were missing without anything appearing slow.
    """
    from archihub.infra.indexes import INDEXES

    shapes = [spec for spec in INDEXES if spec.collection == "shapes"]
    keys = {spec.name: spec.keys for spec in shapes}

    assert ("geometry", "2dsphere") in keys["ix_shapes_geometry"], "$geoIntersects needs a 2dsphere index"
    assert keys["ix_shapes_level_parent_name"][0] == ("properties.admin_level", 1)


def test_a_boundary_name_is_stripped_before_it_is_capitalised(tmp_path, monkeypatch):
    """Published names carry stray leading spaces.

    `capitalize` uppercases the first character, which is the space - so the
    name stays lowercase and sorts ahead of every properly-cased one wherever
    the map lists boundaries.
    """
    pytest.importorskip("geopandas")
    captured = []

    folder = tmp_path / "admin_1"
    folder.mkdir()
    (folder / "data.json").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"ident": "X1", "name": " silvania"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                        },
                    }
                ],
            }
        )
    )

    class _Mongo:
        def delete_records(self, *a, **k):
            return None

        def insert_records(self, _collection, documents):
            captured.extend(documents)

        def get_all_records(self, *a, **k):
            return []

    monkeypatch.setattr(services, "_mongo", lambda: _Mongo())
    services._load_boundary_file(folder / "data.json", 1)

    assert captured, "nothing was stored"
    stored = captured[0]
    name = (stored.properties if hasattr(stored, "properties") else stored["properties"])["name"]
    assert name == "Silvania"
