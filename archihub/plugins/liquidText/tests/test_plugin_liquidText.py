"""Exporting a record's liquid text needs the right to open that record."""

from __future__ import annotations

import pytest

liquid = pytest.importorskip("archihub.plugins.liquidText")

RECORD_ID = "6a70b833497d4440325c94b1"


def test_a_record_the_caller_may_not_open_is_not_exported(monkeypatch):
    import archihub.api.records.services as records

    monkeypatch.setattr(records, "load_visible", lambda rid, user: (None, ({"msg": "no"}, 404)))

    assert liquid._refuse_unseen_records([RECORD_ID], "alice") == ({"msg": "no"}, 404)


def test_a_visible_record_may_be_exported(monkeypatch):
    import archihub.api.records.services as records

    monkeypatch.setattr(records, "load_visible", lambda rid, user: ({"_id": rid}, None))

    assert liquid._refuse_unseen_records([RECORD_ID], "alice") is None


@pytest.mark.parametrize("records", [None, [], [RECORD_ID, RECORD_ID], [{"$ne": None}]])
def test_exactly_one_record_id_is_required(records):
    assert liquid._refuse_unseen_records(records, "alice")[1] == 400
