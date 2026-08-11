"""Celery task bodies (Phase 4).

Six tasks, all long-running, all triggered from the admin screens. What is worth
testing about them is not the Elasticsearch call - it is everything around it:
which resources a run walks, what happens to the ones that fail, and whether a
task that destroys data re-checks that it is allowed to.

No infrastructure: Mongo, Elasticsearch and the broker are all stubbed.
"""

from __future__ import annotations

import pytest

from archihub.worker.tasks import geometries, indexing


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMongo:
    """Enough of the wrapper to page through a collection."""

    def __init__(self, **collections):
        self.collections = {name: list(rows) for name, rows in collections.items()}
        self.queries: list[tuple[str, dict]] = []
        self.dropped: list[str] = []

    def get_all_records(self, collection, filters=None, sort=None, limit=0, skip=0, fields=None):
        filters = filters or {}
        self.queries.append((collection, filters))
        rows = [row for row in self.collections.get(collection, []) if _matches(row, filters)]
        rows.sort(key=lambda row: row["_id"])
        return rows[skip:][:limit] if limit else rows[skip:]

    def get_record(self, collection, filters=None, fields=None):
        for row in self.collections.get(collection, []):
            if _matches(row, filters or {}):
                return dict(row)
        return None

    def get_collections(self):
        return list(self.collections)

    def delete_records(self, collection, filters):
        rows = self.collections.get(collection, [])
        self.collections[collection] = [row for row in rows if not _matches(row, filters)]

    @property
    def db(self):
        return self

    def drop_collection(self, name):
        self.dropped.append(name)
        self.collections.pop(name, None)


def _matches(row: dict, filters: dict) -> bool:
    for key, condition in filters.items():
        if key == "$and":
            if not all(_matches(row, clause) for clause in condition):
                return False
            continue
        value = row
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(condition, dict):
            if "$gt" in condition and not (value is not None and value > condition["$gt"]):
                return False
            if "$ne" in condition and value == condition["$ne"]:
                return False
            if "$in" in condition and value not in condition["$in"]:
                return False
        elif value != condition:
            return False
    return True


class FakeSearch:
    def __init__(self, *, reject: set[str] | None = None, raise_on_bulk: bool = False):
        self.index_prefix = "test"
        self.written: list[tuple[str, dict]] = []
        self.cleared: list[str] = []
        self.deleted: list[str] = []
        self.reject = reject or set()
        self.raise_on_bulk = raise_on_bulk

    def resolve_index(self, suffix):
        return f"{self.index_prefix}-{suffix}"

    def delete_all_documents(self, suffix, query=None):
        self.cleared.append(suffix)
        return {"deleted": 0}

    def delete_document(self, suffix, doc_id):
        self.deleted.append(doc_id)
        return {"result": "deleted"}

    def bulk_index(self, suffix, documents):
        if self.raise_on_bulk:
            raise ConnectionError("cluster unreachable")
        failures = []
        for doc_id, document in documents:
            if doc_id in self.reject:
                failures.append((doc_id, "mapping conflict"))
            else:
                self.written.append((doc_id, document))
        return failures

    def regenerate_index(self, suffix, mapping):
        return f"{self.resolve_index(suffix)}_2", False

    def get_aliases(self):
        return {}


@pytest.fixture
def stubbed(monkeypatch):
    """Point both task modules at fakes, and neutralise the type lookups."""

    def install(mongo, client, *, types=None, article=False):
        for module in (indexing, geometries):
            monkeypatch.setattr(module, "_mongo", lambda: mongo)
            monkeypatch.setattr(module, "_client", lambda: client)
        monkeypatch.setattr(
            indexing._TypeCache, "for_type", lambda self, slug: (types or [], article)
        )
        monkeypatch.setattr(indexing, "_centroid", lambda ident, parent, level: None)
        return mongo, client

    return install


def _resources(count: int, **extra) -> list[dict]:
    return [
        {"_id": f"{i:04d}", "post_type": "fondo", "status": "published", **extra}
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Which resources a run walks
# ---------------------------------------------------------------------------


def test_a_full_run_empties_the_index_first(stubbed):
    mongo, client = stubbed(FakeMongo(resources=_resources(3)), FakeSearch())

    indexing.index_resources_task()

    assert client.cleared == ["resources"]
    assert len(client.written) == 3


def test_a_filtered_run_does_not_empty_the_index(stubbed):
    """It would delete everything it was not asked to touch."""
    mongo, client = stubbed(FakeMongo(resources=_resources(3)), FakeSearch())

    indexing.index_resources_task({"post_type": "fondo"})

    assert client.cleared == []


def test_a_filtered_run_keeps_its_filter_on_every_page(stubbed, monkeypatch):
    """BACKEND_FINDINGS F44. The original applied the caller's filter to the
    first page and queried `{}` for every page after it, so a filter matching
    more than one page walked the entire collection from page two onward."""
    monkeypatch.setattr(indexing, "PAGE_SIZE", 2)

    rows = _resources(4, post_type="fondo") + [
        {"_id": "9000", "post_type": "serie", "status": "published"}
    ]
    mongo, client = stubbed(FakeMongo(resources=rows), FakeSearch())

    indexing.index_resources_task({"post_type": "fondo"})

    assert {doc_id for doc_id, _ in client.written} == {"0000", "0001", "0002", "0003"}
    # Every query the run issued still carried the filter.
    resource_queries = [f for collection, f in mongo.queries if collection == "resources"]
    assert len(resource_queries) > 1
    for query in resource_queries:
        assert "fondo" in repr(query)


def test_paging_covers_every_resource_exactly_once(stubbed, monkeypatch):
    monkeypatch.setattr(indexing, "PAGE_SIZE", 3)
    mongo, client = stubbed(FakeMongo(resources=_resources(10)), FakeSearch())

    indexing.index_resources_task()

    written = [doc_id for doc_id, _ in client.written]
    assert written == sorted(written)
    assert len(written) == len(set(written)) == 10


def test_a_single_id_run_indexes_only_that_resource(stubbed, monkeypatch):
    from bson.objectid import ObjectId

    oid = ObjectId()
    rows = [{"_id": oid, "post_type": "fondo", "status": "published"}]
    rows += _resources(2)
    mongo, client = stubbed(FakeMongo(resources=rows), FakeSearch())

    # `sort` in the fake compares ids directly, so keep the collection to the
    # one type of id this case cares about.
    monkeypatch.setattr(
        mongo, "get_all_records", lambda *a, **k: [rows[0]] if k.get("limit") else []
    )

    indexing.index_resources_task({"_id": str(oid)})

    assert [doc_id for doc_id, _ in client.written] == [str(oid)]


# ---------------------------------------------------------------------------
# What happens to what fails
# ---------------------------------------------------------------------------


def test_a_rejected_document_is_reported_not_counted_as_indexed(stubbed):
    """The original incremented the counter BEFORE the try block, so a run in
    which every document was rejected still reported them all as indexed."""
    mongo, client = stubbed(FakeMongo(resources=_resources(3)), FakeSearch(reject={"0001"}))

    result = indexing.index_resources_task()

    assert "2 resources" in result
    assert "1 failed" in result


def test_a_resource_with_no_status_is_reported_as_skipped(stubbed):
    rows = _resources(2) + [{"_id": "0009", "post_type": "fondo"}]
    mongo, client = stubbed(FakeMongo(resources=rows), FakeSearch())

    result = indexing.index_resources_task()

    assert "2 resources" in result
    assert "1 skipped" in result


def test_a_clean_run_says_nothing_about_failures(stubbed):
    mongo, client = stubbed(FakeMongo(resources=_resources(2)), FakeSearch())

    assert indexing.index_resources_task() == "Indexing finished for 2 resources"


def test_a_page_the_cluster_never_received_does_not_abandon_the_run(stubbed, monkeypatch):
    monkeypatch.setattr(indexing, "PAGE_SIZE", 2)
    mongo, client = stubbed(FakeMongo(resources=_resources(4)), FakeSearch(raise_on_bulk=True))

    result = indexing.index_resources_task()

    assert "0 resources" in result and "4 failed" in result


def test_a_resource_whose_document_cannot_be_built_is_counted_as_failed(stubbed, monkeypatch):
    mongo, client = stubbed(FakeMongo(resources=_resources(2)), FakeSearch())

    def explode(resource, types, record_types):
        raise ValueError("unusable")

    monkeypatch.setattr(indexing, "_document_for", explode)

    assert "2 failed" in indexing.index_resources_task()


# ---------------------------------------------------------------------------
# Files are resolved per page, not per resource
# ---------------------------------------------------------------------------


def test_record_types_are_fetched_once_per_page(stubbed, monkeypatch):
    from bson.objectid import ObjectId

    a, b = ObjectId(), ObjectId()
    rows = [
        {"_id": "0001", "post_type": "f", "status": "published", "filesObj": [{"id": str(a)}]},
        {"_id": "0002", "post_type": "f", "status": "published", "filesObj": [{"id": str(b)}]},
    ]
    records = [
        {"_id": a, "processing": {"fileProcessing": {"type": "image"}}},
        {"_id": b, "processing": {"fileProcessing": {"type": "audio"}}},
    ]
    mongo, client = stubbed(FakeMongo(resources=rows, records=records), FakeSearch())

    indexing.index_resources_task()

    record_queries = [q for collection, q in mongo.queries if collection == "records"]
    assert len(record_queries) == 1
    written = dict(client.written)
    assert written["0001"]["records"][0]["type"] == "image"
    assert written["0002"]["records"][0]["type"] == "audio"


def test_an_unusable_record_id_does_not_stop_the_page(stubbed):
    rows = [
        {
            "_id": "0001",
            "post_type": "f",
            "status": "published",
            "filesObj": [{"id": "not-an-objectid"}],
        }
    ]
    mongo, client = stubbed(FakeMongo(resources=rows, records=[]), FakeSearch())

    indexing.index_resources_task()

    assert dict(client.written)["0001"]["records"] == []


# ---------------------------------------------------------------------------
# Deleting one resource from the index
# ---------------------------------------------------------------------------


def test_deleting_requires_an_id(stubbed):
    """The original subscripted body['_id'], so a caller that forgot it got a
    KeyError recorded as a failed task with no message."""
    stubbed(FakeMongo(), FakeSearch())

    with pytest.raises(ValueError):
        indexing.index_resources_delete_task({})


def test_deleting_a_resource_that_is_not_indexed_is_not_an_error(stubbed):
    class NotFound(FakeSearch):
        def delete_document(self, suffix, doc_id):
            return {"result": "not_found"}

    stubbed(FakeMongo(), NotFound())

    assert "deleted from index" in indexing.index_resources_delete_task({"_id": "abc"})


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _shapes(count: int) -> list[dict]:
    return [
        {
            "_id": f"{i:04d}",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"admin_level": 1, "ident": f"X{i}", "name": f"Place {i}"},
        }
        for i in range(count)
    ]


def test_indexing_shapes_clears_the_index_it_then_writes_to(stubbed):
    """BACKEND_FINDINGS F45. The original wrote to `<prefix>-shapes` and cleared
    `shapes` - an unprefixed name that exists on no real instance - so every
    rerun added a second copy of every boundary instead of replacing it."""
    mongo, client = stubbed(FakeMongo(shapes=_shapes(3)), FakeSearch())

    geometries.index_shapes()

    assert client.cleared == ["shapes"]
    assert len(client.written) == 3


def test_a_shape_document_carries_only_what_the_map_queries(stubbed):
    rows = _shapes(1)
    rows[0]["properties"]["simplified_cache"] = {"0.1": "..."}
    rows[0]["properties"]["parent"] = "CO"
    mongo, client = stubbed(FakeMongo(shapes=rows), FakeSearch())

    geometries.index_shapes()

    _, document = client.written[0]
    assert set(document["properties"]) == {"admin_level", "ident", "name", "parent"}


def test_a_rejected_shape_is_reported(stubbed):
    mongo, client = stubbed(FakeMongo(shapes=_shapes(2)), FakeSearch(reject={"0000"}))

    result = geometries.index_shapes()

    assert "1 resources" in result and "1 failed" in result


def test_shape_paging_covers_everything_once(stubbed, monkeypatch):
    monkeypatch.setattr(geometries, "PAGE_SIZE", 2)
    mongo, client = stubbed(FakeMongo(shapes=_shapes(7)), FakeSearch())

    geometries.index_shapes()

    written = [doc_id for doc_id, _ in client.written]
    assert len(written) == len(set(written)) == 7


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_every_expected_task_name_is_registered():
    """A task module that stops being imported registers nothing, and a worker
    receiving one of its messages answers NotRegistered and drops the job -
    which looks like the feature quietly not working, not like an error."""
    from archihub.worker.celery_app import celery_app
    from archihub.worker.tasks import REGISTERED_TASK_NAMES

    assert REGISTERED_TASK_NAMES <= set(celery_app.tasks)


def test_the_registered_names_are_the_legacy_ones():
    """These strings key queued Redis messages and every row already in the
    `tasks` collection. Changing one silently orphans both."""
    from archihub.worker.tasks import REGISTERED_TASK_NAMES

    assert REGISTERED_TASK_NAMES == {
        "system.regenerate_index",
        "system.index_resources",
        "system.index_resources_delete",
        "geosystem.regenerate_index_shapes",
        "geosystem.index_shapes",
        "testcontrol.reset",
    }
