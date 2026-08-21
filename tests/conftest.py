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


#: Where `no_process_restarts` stashes the real terminator, so
#: `tests/test_runtime_restart.py` - which tests it - can put it back.
REAL_TERMINATE_ATTR = "archihub.core.runtime_restart._real_terminate_runtime"


@pytest.fixture(autouse=True)
def no_process_restarts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop the restart machinery from acting on the process running the tests.

    Two separate hazards, and the second one is not theoretical - it is how this
    fixture came to exist.

    ``start_runtime_restart_monitor`` starts a thread that polls the ``system``
    collection. With no database running every poll is a connection timeout, and
    the thread outlives the test that created it, so the suite would accumulate
    one poller per ``create_app()`` call.

    ``schedule_local_restart`` **sends SIGTERM to the current process** a second
    after it is called - which is correct under a supervisor whose whole job is
    to start the replacement, and fatal anywhere else. Any test touching plugin
    activation reaches it, so without this the run is killed part-way through
    with no failure reported: it presents as the suite hanging, and the exit
    code is the only evidence of what happened.

    The wiring is asserted in ``tests/test_runtime_restart.py`` by checking that
    the service calls these functions, not by letting them run.
    """
    from archihub.core import runtime_restart

    # Stashed where a test that needs the real thing can reach it - the same
    # arrangement `pinned_locale` uses below. Without it, a test exercising this
    # module would silently exercise the stub and pass whatever it asserted.
    monkeypatch.setattr(
        REAL_TERMINATE_ATTR, runtime_restart.terminate_runtime, raising=False
    )
    monkeypatch.setattr(runtime_restart, "start_runtime_restart_monitor", lambda: None)
    monkeypatch.setattr(runtime_restart, "schedule_local_restart", lambda *a, **k: None)
    monkeypatch.setattr(runtime_restart, "terminate_runtime", lambda: None)
    yield


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
