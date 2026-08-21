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

# The memoisation layer reaches Redis, and this suite must run with nothing
# running. Forced rather than `setdefault`: a developer with a local Redis would
# otherwise get a DIFFERENT suite from CI, and worse, cache keys are shared
# across processes - a run would read entries left by the application and leave
# its own behind for the next one. The cache is covered directly in
# tests/test_cache.py against an in-memory client.
os.environ["CACHE_ENABLED"] = "false"


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate settings so each test can set its own environment."""
    from archihub.core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


#: Where `pinned_locale` stashes the real resolver, so `tests/test_i18n.py` -
#: which tests that resolver itself, against a fake Mongo - can put it back.
REAL_GET_LOCALE_ATTR = "archihub.core.i18n._real_get_locale"


@pytest.fixture(autouse=True)
def pinned_locale(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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

    Pinning the *cache* is not enough on its own. Any code path that calls
    ``reset_locale_cache`` - ``system.services.update_settings`` does, through
    ``clear_system_cache`` - drops the pinned value, and the next ``_()`` then
    resolves the locale from MongoDB for real. With no database running that is
    a 10-second connection timeout per test; with one running it is a silent
    dependency on live infrastructure. So the resolver itself is replaced, and
    the cache is pinned as well for anything reading it directly.
    """
    import time

    from archihub.core import i18n

    i18n._locale_cache = ("en", time.monotonic())

    # The replacement still reads the pinned cache, so the several tests that
    # switch locale by assigning `_locale_cache` keep working unchanged. What it
    # does not do is fall through to MongoDB when the cache is empty.
    monkeypatch.setattr(REAL_GET_LOCALE_ATTR, i18n.get_locale, raising=False)
    monkeypatch.setattr(
        i18n, "get_locale", lambda: (i18n._locale_cache or (i18n.DEFAULT_LOCALE,))[0]
    )
    yield
    i18n.reset_locale_cache()
