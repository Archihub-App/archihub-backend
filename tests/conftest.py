"""Shared pytest fixtures.

Per PLAN_FASTAPI.md decision 4, unit tests deliberately cover only the pieces
with no existing end-to-end coverage - auth dependencies, hooks, Celery context
acquisition and the plugin framework. Broad per-domain behaviour is verified by
``tools/diff_harness.py`` against the running legacy backend instead.

Every fixture here avoids real infrastructure: these tests must run in CI with
no MongoDB, Redis, Elasticsearch or Qdrant available.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Secrets must exist before archihub.core.settings is imported, because the
# model treats them as required. Values are dummies; nothing here talks to a
# real service.
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")
os.environ.setdefault("FERNET_KEY", "kC5s3s1ZQ0dGmZ6l8Xh9Yq2vN4bP7tR0uW1xA3cE5gI=")


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate settings so each test can set its own environment."""
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
