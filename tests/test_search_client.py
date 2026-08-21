"""The Elasticsearch HTTP client.

What is worth testing here is not that ``requests`` works. It is that an index
name can only be produced one way, that a failed write is reported as one, and
that a regeneration never leaves the alias resolving to two indices at once -
each of which was a real defect in ``app/utils/IndexHandler.py``.
"""

from __future__ import annotations

import json

import pytest

from archihub.infra import search as search_module


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Recorder:
    """Stands in for the transport, recording every call."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict | None] = []
        self.payloads: list[str | None] = []
        self.timeouts: list[int] = []
        self.responses = responses or {}

    def __call__(self, method, path, *, json_body=None, data=None, headers=None, timeout=30):
        self.calls.append((method, path))
        self.bodies.append(json_body)
        self.payloads.append(data)
        self.timeouts.append(timeout)
        response = self.responses.get((method, path))
        if callable(response):
            response = response()
        return response or FakeResponse(200, {})

    @property
    def paths(self):
        return [path for _, path in self.calls]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ELASTIC_INDEX_PREFIX", "test")
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    instance = search_module.SearchClient()
    get_settings.cache_clear()
    return instance


def wire(client, responses=None) -> Recorder:
    recorder = Recorder(responses)
    client._request = recorder
    return recorder


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_every_document_operation_resolves_the_instance_prefix(client):
    """the geometry indexer wrote to `<prefix>-shapes` and
    cleared `shapes`, because the name was pasted together at each call site."""
    recorder = wire(client)

    client.index_document("shapes", "1", {})
    client.delete_document("shapes", "1")
    client.delete_all_documents("shapes")
    client.search("shapes", {})

    for path in recorder.paths:
        assert path.startswith("/test-shapes")


def test_a_suffix_is_never_double_prefixed(client):
    assert client.resolve_index("resources") == "test-resources"


# ---------------------------------------------------------------------------
# Failures are failures
# ---------------------------------------------------------------------------


def test_a_rejected_write_raises_rather_than_returning_a_response(client):
    """The original returned the raw `requests.Response` and left the caller to
    remember `if r.status_code != 201 and != 200`. The shapes indexer did not."""
    wire(
        client,
        {
            ("PUT", "/test-resources/_doc/1"): FakeResponse(
                400, {"error": {"reason": "mapper_parsing_exception"}}
            )
        },
    )

    with pytest.raises(search_module.SearchUnavailable) as excinfo:
        client.index_document("resources", "1", {})

    assert "mapper_parsing_exception" in str(excinfo.value)


def test_deleting_a_document_that_is_not_there_is_not_a_failure(client):
    wire(client, {("DELETE", "/test-resources/_doc/1"): FakeResponse(404, {})})

    assert client.delete_document("resources", "1") == {"result": "not_found"}


def test_emptying_an_index_that_does_not_exist_yet_is_not_a_failure(client):
    """A first-ever indexing run reaches this before the index exists."""
    wire(client, {("POST", "/test-resources/_delete_by_query"): FakeResponse(404, {})})

    assert client.delete_all_documents("resources") == {"deleted": 0}


def test_long_running_operations_get_a_longer_timeout_not_none(client):
    recorder = wire(client)

    client.delete_all_documents("resources")
    client.reindex("a", "b")

    assert set(recorder.timeouts) == {search_module.REINDEX_TIMEOUT}


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


def test_a_bulk_write_sends_ndjson_with_a_trailing_newline(client):
    recorder = wire(client, {("POST", "/_bulk"): FakeResponse(200, {"errors": False})})

    client.bulk_index("resources", [("1", {"ident": "A"}), ("2", {"ident": "B"})])

    payload = recorder.payloads[0]
    assert payload.endswith("\n")
    lines = [json.loads(line) for line in payload.strip().split("\n")]
    assert lines[0] == {"index": {"_index": "test-resources", "_id": "1"}}
    assert lines[1] == {"ident": "A"}


def test_a_bulk_write_reports_the_ids_the_cluster_refused(client):
    """Returned rather than raised: one malformed resource must not abandon the
    other 999 in the batch, and the caller must still be able to find out."""
    body = {
        "errors": True,
        "items": [
            {"index": {"_id": "1", "status": 201}},
            {"index": {"_id": "2", "status": 400, "error": {"reason": "bad date"}}},
        ],
    }
    wire(client, {("POST", "/_bulk"): FakeResponse(200, body)})

    failures = client.bulk_index("resources", [("1", {}), ("2", {})])

    assert failures == [("2", "bad date")]


def test_an_empty_batch_makes_no_request(client):
    recorder = wire(client)

    assert client.bulk_index("resources", []) == []
    assert recorder.calls == []


def test_a_datetime_in_a_document_does_not_break_the_batch(client):
    """The indexer converts dates itself, but a plugin's `resource_index` hook
    can put anything into the document."""
    from datetime import datetime

    recorder = wire(client, {("POST", "/_bulk"): FakeResponse(200, {"errors": False})})

    client.bulk_index("resources", [("1", {"when": datetime(2020, 1, 1)})])

    assert "2020-01-01" in recorder.payloads[0]


# ---------------------------------------------------------------------------
# Regeneration
# ---------------------------------------------------------------------------


def test_regenerating_with_no_existing_alias_creates_the_first_index(client):
    wire(client, {("GET", "/_alias/test-resources"): FakeResponse(404, {})})

    name, created = client.regenerate_index("resources", {"properties": {}})

    assert (name, created) == ("test-resources_1", True)


def test_regenerating_swaps_the_alias_in_one_action_after_the_copy(client):
    """The original added the new index to the alias BEFORE reindexing into it,
    so for the duration of the copy the alias resolved to two indices - one
    full, one filling up - and every search served duplicate hits."""
    recorder = wire(client, {("GET", "/_alias/test-resources"): FakeResponse(200, {"test-resources_3": {}})})

    name, created = client.regenerate_index("resources", {"properties": {}})

    assert (name, created) == ("test-resources_4", False)

    order = recorder.paths
    assert order.index("/_reindex") < order.index("/_aliases")

    swap = recorder.bodies[order.index("/_aliases")]
    assert swap == {
        "actions": [
            {"remove": {"index": "test-resources_3", "alias": "test-resources"}},
            {"add": {"index": "test-resources_4", "alias": "test-resources"}},
        ]
    }


def test_an_alias_pointing_at_several_indices_is_refused_not_added_to(client):
    """That state means a previous regeneration was interrupted. The original
    created yet another index and left the mess growing."""
    wire(
        client,
        {("GET", "/_alias/test-resources"): FakeResponse(200, {"test-resources_1": {}, "test-resources_2": {}})},
    )

    with pytest.raises(search_module.SearchUnavailable):
        client.regenerate_index("resources", {})


@pytest.mark.parametrize(
    "current,alias,expected",
    [
        ("test-resources_3", "test-resources", 4),
        # An instance prefix containing an underscore. The original read the
        # version with `name.split('_')[1]`, which here is "archive-resources"
        # and raised ValueError inside a Celery task.
        ("my_archive-resources_7", "my_archive-resources", 8),
        ("test-resources", "test-resources", 1),
    ],
)
def test_the_version_number_is_read_from_the_end_not_the_first_underscore(current, alias, expected):
    assert search_module._next_version(current, alias) == expected
