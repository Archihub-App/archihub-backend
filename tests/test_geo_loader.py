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
    """BACKEND_FINDINGS F47. `int(f.split('admin_')[1])` over `os.listdir`
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
