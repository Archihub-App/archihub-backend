"""What happens to a value ``json.dumps`` refuses.

Starlette's ``JSONResponse`` renders with a bare ``json.dumps``, so a single
``datetime`` left in a payload turns a working route into a 500. That is a
nasty shape of bug: it fires only for the documents that happen to carry the
field, so it survives the unit tests, survives a live smoke test against a
fresh instance, and appears later against real data.

It did happen. ``GET /users/me`` and ``GET /users/{id}`` both 500'd against the
real database on a user whose ``lastRequest`` had been set, while every test
passed - found by the diff harness, which is the only thing that fires at real
documents.
"""

from __future__ import annotations

import datetime
import json

import pytest
from bson.objectid import ObjectId

from archihub.core.responses import json_response


def _rendered(payload):
    return json.loads(json_response(payload).body)


# ---------------------------------------------------------------------------
# The values that used to 500
# ---------------------------------------------------------------------------


def test_a_datetime_renders_as_an_http_date_like_flask_did():
    """`GET /users/{id}` returns the raw document, as the legacy route did, and
    Flask's `jsonify` rendered a datetime as an HTTP date. That string is the
    wire contract."""
    payload = {"lastRequest": datetime.datetime(2026, 8, 10, 14, 12, 34)}

    assert _rendered(payload) == {"lastRequest": "Mon, 10 Aug 2026 14:12:34 GMT"}


def test_a_timezone_aware_datetime_is_not_shifted_twice():
    aware = datetime.datetime(2026, 8, 10, 14, 12, 34, tzinfo=datetime.timezone.utc)

    assert _rendered({"at": aware})["at"] == "Mon, 10 Aug 2026 14:12:34 GMT"


def test_a_naive_datetime_is_read_as_utc():
    """The reading Werkzeug's `http_date` applies, so the two stacks agree."""
    naive = datetime.datetime(2026, 8, 10, 14, 12, 34)
    aware = naive.replace(tzinfo=datetime.timezone.utc)

    assert _rendered({"at": naive}) == _rendered({"at": aware})


def test_an_objectid_renders_the_way_every_other_endpoint_returns_one():
    payload = {"_id": ObjectId("6a70b833497d4440325c94b1")}

    assert _rendered(payload) == {"_id": {"$oid": "6a70b833497d4440325c94b1"}}


def test_a_date_renders_as_an_iso_string():
    assert _rendered({"on": datetime.date(2026, 8, 10)}) == {"on": "2026-08-10"}


def test_values_nested_anywhere_are_reached():
    """The failing document had its datetime at the top level. The next one
    will not."""
    payload = {"users": [{"profile": {"lastRequest": datetime.datetime(2026, 8, 10)}}]}

    rendered = _rendered(payload)

    assert rendered["users"][0]["profile"]["lastRequest"] == "Mon, 10 Aug 2026 00:00:00 GMT"


# ---------------------------------------------------------------------------
# The ordinary cases still behave
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"msg": "plain"},
        {"total": 0, "resources": []},
        [1, 2, 3],
        {"nested": {"a": [{"b": None}]}},
        {"unicode": "café, año, 文字"},
    ],
)
def test_an_ordinary_payload_round_trips_unchanged(payload):
    assert _rendered(payload) == payload


def test_the_status_code_is_carried_through():
    assert json_response({"msg": "nope"}, 404).status_code == 404


def test_a_value_nothing_can_encode_still_raises_rather_than_inventing_one():
    """A silent `str(value)` fallback would let an unintended object serialise
    into a response looking plausible. Better to fail loudly."""

    class Unknown:
        pass

    with pytest.raises(TypeError):
        json_response({"x": Unknown()}).body


def test_nan_is_refused_rather_than_emitted_as_invalid_json():
    """`json.dumps` emits a bare `NaN` by default, which is not JSON and which
    `JSON.parse` in the browser rejects outright."""
    with pytest.raises(ValueError):
        json_response({"x": float("nan")}).body
