"""The generic `/providers/{id}/chat` route.

A provider the operator has disabled must refuse a chat request here too, not
only through the record assistant's `provider_and_model` - this is the other
place `providers.load(...)` feeds a live call.
"""

from __future__ import annotations

import json

import pytest

from archihub.api.aiservices import router
from archihub.core.security.jwt import CurrentUser


def _user():
    return CurrentUser(username="someone@test.com", claims={})


def test_send_chat_refuses_a_disabled_provider(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.aiservices.providers.load",
        lambda provider_id: {"_id": provider_id, "enabled": False},
    )

    response = router.send_chat("p1", body={"messages": [], "model": "m1"}, current_user=_user())

    assert response.status_code == 403


def test_send_chat_reaches_past_an_enabled_provider(monkeypatch):
    monkeypatch.setattr(
        "archihub.api.aiservices.providers.load",
        lambda provider_id: {"_id": provider_id, "enabled": True, "dialect": "openai-compatible"},
    )

    response = router.send_chat(
        "p1",
        body={"messages": [{"role": "user", "content": "hi"}], "model": "m1"},
        current_user=_user(),
    )

    # It gets past the enabled check onto the real request path, which then
    # fails for an unrelated reason (no real provider to call) - the point
    # here is only that it is not refused for being disabled.
    assert response.status_code != 403


def test_send_chat_refuses_a_missing_provider(monkeypatch):
    monkeypatch.setattr("archihub.api.aiservices.providers.load", lambda provider_id: None)

    response = router.send_chat("p1", body={"messages": [], "model": "m1"}, current_user=_user())

    assert response.status_code == 404
