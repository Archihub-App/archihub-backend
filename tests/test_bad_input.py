"""Ordinary malformed input is a 400, never a 500."""

from __future__ import annotations

import pytest

from archihub.core.pagination import page_number


@pytest.mark.parametrize("value,expected", [
    (None, 0), ("", 0), (0, 0), (3, 3), ("7", 7),
    ("x", None), (-1, None), (True, None), (1.5, None), ([1], None), (10**9, None),
])
def test_a_page_number_is_a_small_non_negative_integer(value, expected):
    assert page_number(value) == expected


@pytest.mark.parametrize("body", [
    {"post_type": ["x"], "page": "x"},
    {"post_type": 5},
    {"post_type": ["x"], "parents": "abc"},
])
def test_the_listing_refuses_malformed_input(body, monkeypatch):
    from archihub.api.resources import services

    monkeypatch.setattr(services, "can_view_type", lambda u, pt: True)
    monkeypatch.setattr(
        services.access, "field_visibility", lambda post_types, user, public: ([], set())
    )
    monkeypatch.setattr("archihub.api.users.services.has_role", lambda u, r: True)
    monkeypatch.setattr(services, "_mongo", lambda: pytest.fail("must refuse before a query"))

    _payload, status = services.get_all(body, "alice")

    assert status == 400


def test_the_log_listing_refuses_a_malformed_page(monkeypatch):
    from archihub.api.logs import services

    monkeypatch.setattr(services, "_mongo", lambda: pytest.fail("must refuse before a query"))
    _payload, status = services.get_logs_for_resource({"page": "x"}, "r1")

    assert status == 400
