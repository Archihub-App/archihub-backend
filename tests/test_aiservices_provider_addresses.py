"""Where a provider may point, checked when it is used as well as when saved."""

from __future__ import annotations

import pytest

from archihub.api.aiservices import providers


def test_an_address_is_checked_again_each_time_the_provider_is_used(monkeypatch):
    """A public name saved by a non-administrator may later resolve inward."""
    from archihub.api.aiservices import catalogue, errors as ai_errors

    monkeypatch.setattr(providers, "_is_internal_host", lambda host: True)
    provider = {"dialect": "ollama", "base_url": "https://models.example.org", "internalAllowed": False}

    with pytest.raises(ai_errors.ProviderError):
        catalogue.build_adapter(provider)


def test_an_administrators_internal_provider_keeps_working(monkeypatch):
    monkeypatch.setattr(providers, "_is_internal_host", lambda host: True)

    assert providers.reaches_refused_address(
        {"base_url": "http://ollama:11434", "internalAllowed": True}
    ) is False


def test_a_provider_saved_before_the_flag_is_judged_by_its_saver(monkeypatch):
    monkeypatch.setattr(providers, "_is_internal_host", lambda host: True)
    monkeypatch.setattr(
        "archihub.api.users.services.has_role", lambda user, role: user == "root"
    )

    assert providers.reaches_refused_address(
        {"base_url": "http://ollama:11434", "updatedBy": "root"}
    ) is False
    assert providers.reaches_refused_address(
        {"base_url": "http://ollama:11434", "updatedBy": "processor"}
    ) is True
