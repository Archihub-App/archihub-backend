"""The ``/health/test-control/*`` gate.

This gate guards routes that wipe an entire instance, and ``ArchiHUBTestRunner``
distinguishes its three failure modes purely by status code during preflight:
404 means test mode is not reaching the container, 403 means the marker document
is missing, 401 means the secret is wrong. The ORDER matters as much as the
codes - a wrong secret must still yield 404 when test mode is off, so an
attacker cannot use the response to detect that the feature exists.

These tests pin that contract.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from archihub.core.security import test_control


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch):
    """Call the dependency with configurable gate state, without any database."""

    def _call(
        *,
        test_mode: bool,
        disposable: bool,
        configured_secret: str | None,
        provided_secret: str | None,
    ):
        from archihub.core.settings import Settings, get_settings

        get_settings.cache_clear()
        settings = Settings(
            SECRET_KEY="x",
            JWT_SECRET_KEY="y",
            FERNET_KEY="z",
            ARCHIHUB_TEST_MODE=test_mode,
            TEST_SECRET_HEADER_KEY=configured_secret,
        )
        monkeypatch.setattr(test_control, "get_settings", lambda: settings)
        monkeypatch.setattr(test_control, "is_disposable_instance", lambda: disposable)
        return test_control.test_control_authenticate(x_archihub_test_secret=provided_secret)

    return _call


def test_test_mode_off_returns_404(gate):
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=False, disposable=True, configured_secret="s", provided_secret="s")
    assert exc.value.status_code == 404
    assert exc.value.detail == {"msg": "Test-control API is not enabled on this instance"}


def test_missing_marker_document_returns_403(gate):
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=True, disposable=False, configured_secret="s", provided_secret="s")
    assert exc.value.status_code == 403
    assert exc.value.detail == {"msg": "This instance is not marked as a disposable test instance"}


def test_wrong_secret_returns_401(gate):
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=True, disposable=True, configured_secret="right", provided_secret="wrong")
    assert exc.value.status_code == 401
    assert exc.value.detail == {"msg": "Invalid or missing X-ArchiHUB-Test-Secret header"}


def test_missing_secret_header_returns_401(gate):
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=True, disposable=True, configured_secret="right", provided_secret=None)
    assert exc.value.status_code == 401


def test_unset_server_secret_can_never_authenticate(gate):
    """An unconfigured TEST_SECRET_HEADER_KEY must fail closed.

    The variable is optional-with-no-fallback on purpose: leaving it unset means
    test-control auth can never succeed, which is the safe default for a feature
    that must stay off outside disposable instances.
    """
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=True, disposable=True, configured_secret=None, provided_secret="anything")
    assert exc.value.status_code == 401


def test_valid_request_passes(gate):
    assert gate(
        test_mode=True, disposable=True, configured_secret="s3cret", provided_secret="s3cret"
    ) is None


def test_gate_order_test_mode_beats_everything(gate):
    """With test mode off, a missing marker AND a bad secret still yield 404.

    The instance must not reveal that the feature exists, let alone which
    precondition failed.
    """
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=False, disposable=False, configured_secret=None, provided_secret=None)
    assert exc.value.status_code == 404


def test_gate_order_marker_beats_secret(gate):
    """A missing marker outranks a bad secret."""
    with pytest.raises(HTTPException) as exc:
        gate(test_mode=True, disposable=False, configured_secret="right", provided_secret="wrong")
    assert exc.value.status_code == 403
