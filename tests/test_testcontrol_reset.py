"""The reset task's own guard.

The route's gate answers "may this caller ask for a reset?". This one answers
"may this database be wiped, now?" - a different question, and the reason the
task re-checks rather than trusting the route.

A queued Celery message is a durable object. A reset queued against a disposable
instance, left unconsumed, and picked up later by a worker attached to a database
that is no longer disposable would otherwise run with no gate at all.
"""

from __future__ import annotations

import pytest

from archihub.worker.tasks import testcontrol


class FakeMongo:
    def __init__(self, marker=None, collections=None):
        self.marker = marker
        self.collections = list(collections or ["system", "resources", "records"])
        self.dropped: list[str] = []
        self.deleted: list[tuple[str, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        if (filters or {}).get("name") == "test_mode_active":
            return self.marker
        return None

    def get_collections(self):
        return list(self.collections)

    def delete_records(self, collection, filters):
        self.deleted.append((collection, filters))

    @property
    def db(self):
        return self

    def drop_collection(self, name):
        self.dropped.append(name)


@pytest.fixture
def disposable(monkeypatch):
    """An instance that passes both halves of the gate."""
    monkeypatch.setenv("ARCHIHUB_TEST_MODE", "true")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    mongo = FakeMongo(marker={"name": "test_mode_active", "value": True})
    monkeypatch.setattr(testcontrol, "_mongo", lambda: mongo)
    yield mongo
    get_settings.cache_clear()


def test_a_reset_refuses_when_test_mode_is_off(monkeypatch):
    monkeypatch.setenv("ARCHIHUB_TEST_MODE", "false")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        testcontrol, "_mongo", lambda: FakeMongo(marker={"name": "x", "value": True})
    )
    try:
        with pytest.raises(RuntimeError, match="ARCHIHUB_TEST_MODE"):
            testcontrol._assert_disposable()
    finally:
        get_settings.cache_clear()


def test_a_reset_refuses_without_the_hand_inserted_marker(monkeypatch):
    """The application never writes this document, by design: it is what stops a
    production instance from being one environment variable away from a wipe."""
    monkeypatch.setenv("ARCHIHUB_TEST_MODE", "true")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(testcontrol, "_mongo", lambda: FakeMongo(marker=None))
    try:
        with pytest.raises(RuntimeError, match="disposable"):
            testcontrol._assert_disposable()
    finally:
        get_settings.cache_clear()


def test_a_marker_present_but_switched_off_still_refuses(monkeypatch):
    monkeypatch.setenv("ARCHIHUB_TEST_MODE", "true")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        testcontrol, "_mongo", lambda: FakeMongo(marker={"name": "x", "value": False})
    )
    try:
        with pytest.raises(RuntimeError):
            testcontrol._assert_disposable()
    finally:
        get_settings.cache_clear()


def test_the_gate_is_checked_before_anything_is_destroyed(monkeypatch):
    monkeypatch.setenv("ARCHIHUB_TEST_MODE", "false")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    mongo = FakeMongo(marker=None)
    monkeypatch.setattr(testcontrol, "_mongo", lambda: mongo)

    try:
        with pytest.raises(RuntimeError):
            testcontrol.reset_task("run-1")
    finally:
        get_settings.cache_clear()

    assert mongo.dropped == [] and mongo.deleted == []


def test_the_system_collection_survives_a_wipe(disposable):
    """Losing it would take the disposability marker with it, after which no
    further reset could run."""
    testcontrol._wipe_mongo()

    assert "system" not in disposable.dropped
    assert set(disposable.dropped) == {"resources", "records"}


def test_the_wipe_keeps_the_marker_and_removes_every_other_setting(disposable):
    testcontrol._wipe_mongo()

    collection, filters = disposable.deleted[0]
    assert collection == "system"
    assert filters == {"name": {"$ne": "test_mode_active"}}


# ---------------------------------------------------------------------------
# Temporary files
# ---------------------------------------------------------------------------


def test_a_symlink_in_the_temporary_directory_is_unlinked_not_followed(monkeypatch, tmp_path):
    """`rmtree` on a link to a real directory deletes that directory's contents.
    The originals, the web derivatives and the users' uploads all live under the
    same root."""
    scratch = tmp_path / "temporal"
    scratch.mkdir()
    precious = tmp_path / "originals"
    precious.mkdir()
    (precious / "keep.tif").write_text("original")

    (scratch / "link").symlink_to(precious, target_is_directory=True)
    (scratch / "junk.tmp").write_text("x")
    (scratch / "subdir").mkdir()
    (scratch / "subdir" / "inner").write_text("x")

    monkeypatch.setenv("TEMPORAL_FILES_PATH", str(scratch))
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    try:
        testcontrol._wipe_temporal_files()
    finally:
        get_settings.cache_clear()

    assert (precious / "keep.tif").is_file()
    assert list(scratch.iterdir()) == []


def test_an_unset_temporary_path_is_a_no_op(monkeypatch):
    monkeypatch.setenv("TEMPORAL_FILES_PATH", "")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    try:
        testcontrol._wipe_temporal_files()  # must not raise
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_the_seeded_password_is_generated_per_run_and_returned(monkeypatch):
    seen = {}

    def fake_set_first_time(body):
        seen.update(body)
        return {"msg": "ok"}, 201

    monkeypatch.setattr("archihub.api.system.services.set_system_setting", lambda: None)
    monkeypatch.setattr("archihub.api.system.services.set_first_time", fake_set_first_time)

    first = testcontrol._seed_baseline("run-1")
    second = testcontrol._seed_baseline("run-2")

    assert first["admin_password"] != second["admin_password"]
    assert seen["confirmPassword"] == seen["password"]
    assert first["admin_username"] == testcontrol.SEED_ADMIN_USERNAME


def test_a_failed_seed_is_raised_not_swallowed(monkeypatch):
    """A reset that wiped the database and then failed to reseed must not report
    success - the suite that follows it would run against an empty instance."""
    monkeypatch.setattr("archihub.api.system.services.set_system_setting", lambda: None)
    monkeypatch.setattr(
        "archihub.api.system.services.set_first_time", lambda body: ({"msg": "nope"}, 400)
    )

    with pytest.raises(RuntimeError, match="Seeding failed"):
        testcontrol._seed_baseline("run-1")
