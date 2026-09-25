"""The signing and encryption keys: refused when blank, reported when weak."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from archihub.core.settings import Settings, secret_warnings

STRONG_JWT = "a" * 64
VALID_FERNET = "kC5s3s1ZQ0dGmZ6l8Xh9Yq2vN4bP7tR0uW1xA3cE5gI="


def _settings(**overrides):
    base = {"jwt_secret_key": STRONG_JWT, "fernet_key": VALID_FERNET, "FASTAPI_ENV": None}
    return Settings(_env_file=None, **{**base, **overrides})


@pytest.mark.parametrize("field", ["jwt_secret_key", "fernet_key"])
@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_key_stops_the_application_starting(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_strong_keys_produce_no_warning():
    assert secret_warnings(_settings()) == []


def test_a_short_signing_key_is_reported_but_accepted():
    """Refusing it would stop an installation that has not rotated yet."""
    warnings = secret_warnings(_settings(jwt_secret_key="short"))

    assert len(warnings) == 1
    assert "JWT_SECRET_KEY" in warnings[0]


def test_an_invalid_fernet_key_is_reported_but_accepted():
    warnings = secret_warnings(_settings(fernet_key="not-a-fernet-key"))

    assert len(warnings) == 1
    assert "FERNET_KEY" in warnings[0]


def test_the_warnings_are_logged_when_the_application_starts(monkeypatch, caplog):
    from archihub.core import app_factory

    monkeypatch.setattr(app_factory, "configure_logging", lambda **kwargs: None)
    with caplog.at_level("WARNING", logger=app_factory.logger.name):
        app_factory.create_app(_settings(jwt_secret_key="short"))

    assert any("JWT_SECRET_KEY" in record.getMessage() for record in caplog.records)
