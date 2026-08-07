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

# Building the app runs the unported-plugin guard, which would otherwise refuse
# to start (correctly - the plugins genuinely are not ported yet) and would need
# a database to make that decision. The guard itself is tested directly in
# tests/test_plugin_guard.py.
os.environ.setdefault("ARCHIHUB_ALLOW_UNPORTED_PLUGINS", "true")


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate settings so each test can set its own environment."""
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def pinned_locale() -> Iterator[None]:
    """Pin the translation locale to English for every test.

    User-facing strings go through ``_()``, so assertions that compare against
    the English source text only hold while the active locale is English. The
    locale is an instance-wide setting read from MongoDB, which means without
    this the result depends on whether a database happens to be reachable from
    the test machine and what language that instance is configured for - tests
    that pass on a laptop with nothing running fail on one with a Spanish-
    configured stack up. (Exactly that happened during development.)

    ``tests/test_i18n.py`` clears this in its own autouse fixture, which runs
    after this one, so translation itself is still tested properly.
    """
    import time

    from archihub.core import i18n

    i18n._locale_cache = ("en", time.monotonic())
    yield
    i18n.reset_locale_cache()
