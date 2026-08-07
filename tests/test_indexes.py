"""MongoDB index definitions.

Before this work the database had no indexes beyond the automatic `_id` one, so
every lookup by username, slug, parent or task id was a full collection scan.
These tests guard the definitions themselves; the query-plan improvement
(COLLSCAN -> IXSCAN) was verified against a live instance.
"""

from __future__ import annotations

import pytest

from archihub.infra import indexes
from archihub.infra.indexes import INDEXES, IndexSpec, ensure_indexes


class FakeCollection:
    def __init__(self, existing=(), fail_with=None):
        self._existing = [{"name": n} for n in existing]
        self.created: list[dict] = []
        self._fail_with = fail_with

    def list_indexes(self):
        return iter(self._existing)

    def create_index(self, keys, **kwargs):
        if self._fail_with:
            raise self._fail_with
        self.created.append({"keys": keys, **kwargs})
        return kwargs.get("name")


class FakeDb:
    def __init__(self, collections=None):
        self._collections = collections or {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())


class FakeMongo:
    def __init__(self, collections=None):
        self.db = FakeDb(collections)


# ---------------------------------------------------------------------------
# The definitions
# ---------------------------------------------------------------------------


def test_index_names_are_unique():
    names = [f"{spec.collection}.{spec.name}" for spec in INDEXES]
    assert len(names) == len(set(names))


def test_every_index_documents_why_it_exists():
    """An index nobody can justify is one nobody can safely remove later."""
    for spec in INDEXES:
        assert spec.reason, f"{spec.collection}.{spec.name} has no stated reason"


def test_hot_path_collections_are_covered():
    """The lookups on essentially every request must be indexed."""
    covered = {(spec.collection, spec.keys[0][0]) for spec in INDEXES}

    assert ("users", "username") in covered      # every authenticated request
    assert ("system", "name") in covered         # settings, every request + beat tick
    assert ("tasks", "taskId") in covered        # polled while any job runs
    assert ("records", "parent.id") in covered   # every resource detail view
    assert ("resources", "post_type") in covered # every catalogue listing


def test_sorted_listings_use_compound_indexes_in_esr_order():
    """Equality fields must precede the sort field.

    Otherwise MongoDB can use the index for filtering but must still sort the
    results in memory - which is both slow and subject to a hard 32MB limit that
    makes the query fail outright once a collection is large enough. Verified on
    a live instance: these plans contain no SORT stage.
    """
    by_name = {spec.name: spec for spec in INDEXES}

    listing = by_name["ix_resources_type_status_title"]
    assert [k for k, _ in listing.keys] == ["post_type", "status", "metadata.firstLevel.title"]

    tasks = by_name["ix_tasks_user_date"]
    assert [k for k, _ in tasks.keys] == ["user", "date"]
    assert tasks.keys[1][1] == indexes.DESC  # newest first


def test_lists_slug_is_sparse_and_not_unique():
    """Regression guard for a spec that did not match the data.

    `lists` documents carry no `slug` field, so a unique index treats every one
    of them as the same null value and fails to build. Confirmed against a live
    instance when this index was the only one of 23 to fail.
    """
    spec = next(s for s in INDEXES if s.name == "ix_lists_slug")
    assert spec.unique is False
    assert spec.sparse is True


def test_unique_indexes_tolerate_pre_existing_duplicates():
    """A unique index cannot be built over data that already violates it.

    These are applied to instances that already hold data, so such a failure
    must be reported and survivable, not fatal.
    """
    for spec in INDEXES:
        if spec.unique:
            assert spec.tolerate_failure, f"{spec.name} is unique but not marked tolerate_failure"


# ---------------------------------------------------------------------------
# ensure_indexes
# ---------------------------------------------------------------------------


def test_creates_missing_indexes():
    mongo = FakeMongo()
    result = ensure_indexes(mongo)

    assert len(result["created"]) == len(INDEXES)
    assert result["failed"] == []


def test_existing_indexes_are_left_alone():
    """Idempotent - this runs on every startup."""
    collections = {
        spec.collection: FakeCollection(existing=[s.name for s in INDEXES if s.collection == spec.collection])
        for spec in INDEXES
    }
    mongo = FakeMongo(collections)

    result = ensure_indexes(mongo)

    assert result["created"] == []
    assert len(result["existing"]) == len(INDEXES)
    for collection in collections.values():
        assert collection.created == []


def test_all_builds_are_backgrounded():
    """A foreground build holds a write lock for its duration.

    On a populated production collection that is an outage, and these indexes
    are specifically being added to instances that already hold data.
    """
    mongo = FakeMongo()
    ensure_indexes(mongo)

    for spec in INDEXES:
        for created in mongo.db[spec.collection].created:
            assert created["background"] is True


def test_dry_run_creates_nothing():
    mongo = FakeMongo()
    result = ensure_indexes(mongo, dry_run=True)

    assert len(result["created"]) == len(INDEXES)
    for spec in INDEXES:
        assert mongo.db[spec.collection].created == []


def test_one_failure_does_not_stop_the_others():
    """A single bad index must not deny the rest of the database its indexes."""
    import pymongo

    collections = {
        "users": FakeCollection(fail_with=pymongo.errors.DuplicateKeyError("dup")),
    }
    mongo = FakeMongo(collections)

    result = ensure_indexes(mongo)

    assert any("users" in name for name in result["failed"])
    assert len(result["created"]) > 0


def test_never_raises_even_when_everything_fails():
    """A missing index is slow; a backend that will not start is down."""

    class ExplodingDb:
        def __getitem__(self, name):
            raise ConnectionError("mongo unreachable")

    class ExplodingMongo:
        db = ExplodingDb()

    result = ensure_indexes(ExplodingMongo())
    assert len(result["failed"]) == len(INDEXES)


def test_spec_is_immutable():
    """Definitions are shared process-wide; accidental mutation would be subtle."""
    with pytest.raises(Exception):
        INDEXES[0].name = "changed"  # type: ignore[misc]


def test_index_spec_defaults_are_conservative():
    spec = IndexSpec(collection="c", keys=[("f", 1)], name="n")
    assert spec.unique is False
    assert spec.sparse is False
    assert spec.tolerate_failure is False
