"""The admin maintenance routes that queue the Phase 4 tasks.

These are thin, and what matters about them is the three things a thin route can
still get wrong: refusing when the feature is switched off, saying so when the
broker is down rather than reporting success, and not failing the request over
bookkeeping that happens after the work is already queued.
"""

from __future__ import annotations

import pytest

from archihub.api.system import services


class Queued:
    id = "task-id"


@pytest.fixture
def recorded(monkeypatch):
    """Capture what gets queued and recorded, without a broker or a database."""
    state = {"queued": [], "recorded": []}

    def record(task_id, name, user, result_type, params=None):
        state["recorded"].append((task_id, name, user))

    monkeypatch.setattr("archihub.api.tasks.services.add_task", record)
    return state


def _indexing(monkeypatch, *, present=True, enabled=True, schema=True):
    """Stub the two settings lookups the guards read."""
    def get_setting(name):
        if name == "index_management":
            return {"name": name, "data": []} if present else None
        if name == "resources-schema":
            return {"name": name, "data": {}} if schema else None
        return None

    monkeypatch.setattr(services, "get_setting", get_setting)
    monkeypatch.setattr(
        services, "get_setting_value", lambda name, entry, fallback=None: enabled
    )


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_regenerating_without_index_settings_is_a_404(monkeypatch, recorded):
    _indexing(monkeypatch, present=False)

    payload, status = services.regenerate_index("root")

    assert status == 404
    assert recorded["recorded"] == []


def test_regenerating_with_indexing_switched_off_is_a_400(monkeypatch, recorded):
    _indexing(monkeypatch, enabled=False)

    assert services.regenerate_index("root")[1] == 400


def test_regenerating_without_a_stored_schema_is_a_404_not_a_500(monkeypatch, recorded):
    """The original subscripted the record straight away, so a missing schema
    was a TypeError reported as a 500 with the raw message."""
    _indexing(monkeypatch, schema=False)

    assert services.regenerate_index("root")[1] == 404


def test_indexing_resources_with_indexing_switched_off_is_a_400(monkeypatch, recorded):
    _indexing(monkeypatch, enabled=False)

    assert services.index_resources("root")[1] == 400


def test_geometry_routes_are_not_gated_on_resource_indexing(monkeypatch, recorded):
    """Deliberate, and it matches the legacy routes: the explore map's boundary
    layer is drawn from Elasticsearch whether or not resource search is on."""
    _indexing(monkeypatch, enabled=False)
    monkeypatch.setattr(
        "archihub.worker.tasks.geometries.index_shapes.delay", lambda *a: Queued()
    )
    monkeypatch.setattr(
        "archihub.worker.tasks.geometries.regenerate_index_shapes.delay", lambda *a: Queued()
    )

    assert services.index_geometries("root")[1] == 200
    assert services.regenerate_index_geometries("root")[1] == 200


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


def test_a_queued_job_is_recorded_under_the_requesting_user(monkeypatch, recorded):
    _indexing(monkeypatch)
    monkeypatch.setattr(
        "archihub.worker.tasks.indexing.index_resources_task.delay", lambda *a: Queued()
    )

    payload, status = services.index_resources("archivist")

    assert status == 200
    assert recorded["recorded"] == [("task-id", "system.index_resources", "archivist")]


def test_a_broker_that_is_down_answers_503_rather_than_success(monkeypatch, recorded):
    """The original had no such branch: `.delay()` raised, the surrounding
    `except Exception` turned it into `{'msg': str(e)}, 500`, and an operator
    saw a connection error where a queue outage was the actual news."""
    _indexing(monkeypatch)

    def explode(*args):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("archihub.worker.tasks.indexing.index_resources_task.delay", explode)

    payload, status = services.index_resources("root")

    assert status == 503
    assert "redis" not in payload["msg"]


def test_bookkeeping_failure_does_not_fail_a_job_that_is_already_queued(monkeypatch):
    """Telling the operator it had not started, when it had, gets it started
    twice."""
    _indexing(monkeypatch)
    monkeypatch.setattr(
        "archihub.worker.tasks.indexing.index_resources_task.delay", lambda *a: Queued()
    )

    def explode(*args, **kwargs):
        raise RuntimeError("mongo down")

    monkeypatch.setattr("archihub.api.tasks.services.add_task", explode)

    assert services.index_resources("root")[1] == 200


def test_regenerating_passes_the_built_mapping_to_the_task(monkeypatch, recorded):
    _indexing(monkeypatch)
    sent = {}

    def capture(mapping, user):
        sent["mapping"] = mapping
        sent["user"] = user
        return Queued()

    monkeypatch.setattr("archihub.worker.tasks.indexing.regenerate_index_task.delay", capture)

    services.regenerate_index("root")

    assert "properties" in sent["mapping"]
    assert sent["user"] == "root"


# ---------------------------------------------------------------------------
# Generated-file cleanup
# ---------------------------------------------------------------------------


def test_only_the_two_known_directories_can_be_emptied():
    """The value reaches a filesystem path. The legacy routes took no argument
    precisely because there was nothing safe to pass."""
    from archihub.api.resources import files

    for rejected in ("../../originals", "userfiles", "", "."):
        with pytest.raises(ValueError):
            files.delete_generated(rejected)


def test_emptying_removes_files_but_not_subdirectories(monkeypatch, tmp_path):
    """The legacy version called os.remove on every entry, which raised on the
    first subdirectory and left the rest in place, reported as a 500."""
    from archihub.api.resources import files

    web = tmp_path / "web"
    zips = web / "zipfiles"
    zips.mkdir(parents=True)
    (zips / "a.zip").write_text("x")
    (zips / "b.zip").write_text("x")
    (zips / "nested").mkdir()

    monkeypatch.setenv("WEB_FILES_PATH", str(web))
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    try:
        payload, status = files.delete_generated("zipfiles")
    finally:
        get_settings.cache_clear()

    assert status == 200
    assert not (zips / "a.zip").exists()
    assert (zips / "nested").is_dir()


def test_a_symlink_is_unlinked_rather_than_followed(monkeypatch, tmp_path):
    from archihub.api.resources import files

    web = tmp_path / "web"
    zips = web / "zipfiles"
    zips.mkdir(parents=True)
    outside = tmp_path / "originals"
    outside.mkdir()
    (outside / "keep.tif").write_text("original")
    (zips / "link.zip").symlink_to(outside / "keep.tif")

    monkeypatch.setenv("WEB_FILES_PATH", str(web))
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    try:
        files.delete_generated("zipfiles")
    finally:
        get_settings.cache_clear()

    assert (outside / "keep.tif").is_file()
    assert not (zips / "link.zip").exists()


def test_emptying_a_directory_that_does_not_exist_yet_succeeds(monkeypatch, tmp_path):
    from archihub.api.resources import files

    monkeypatch.setenv("WEB_FILES_PATH", str(tmp_path))
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    try:
        assert files.delete_generated("inventoryMaker")[1] == 200
    finally:
        get_settings.cache_clear()
