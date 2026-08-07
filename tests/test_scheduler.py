"""The Mongo-backed beat scheduler.

Beat hot-reloads its schedule from the database so an admin can add a scheduled
task without restarting the process. The failure mode this guards against is
quiet: `refresh_schedule` swallows exceptions (deliberately - a database blip
must not kill beat), so anything that makes it fail *every* time leaves beat
running with an empty schedule and no scheduled task ever fires.
"""

from __future__ import annotations

import archihub.worker.scheduler as scheduler_module
from archihub.worker.schedule import DYNAMIC_SCHEDULE_PREFIX


def test_infra_imports_are_module_level_not_function_level():
    """Regression guard for a real beat startup failure.

    `refresh_schedule` originally did `from archihub.infra.mongo import
    get_mongo` inside the function. Beat calls setup_schedule() on its scheduler
    thread during Celery's bootstrap, while other threads are still importing,
    and that function-level import lost the race:

        ImportError: cannot import name 'get_mongo' from partially initialized
        module 'archihub.infra.mongo' (most likely due to a circular import)

    Because refresh_schedule catches everything, beat came up "successfully"
    with an empty schedule. Observed on a real `celery beat` start.

    Module-level imports resolve once, deterministically, before any tick.
    """
    assert hasattr(scheduler_module, "get_mongo"), (
        "get_mongo must be imported at module scope in worker/scheduler.py - "
        "a function-level import races beat's scheduler thread"
    )

    source = __import__("inspect").getsource(scheduler_module.MongoPluginScheduler.refresh_schedule)
    assert "import" not in source, (
        "refresh_schedule must not contain an import statement; it runs on beat's "
        "scheduler thread where imports can observe partially initialised modules"
    )


def test_refresh_interval_has_a_floor(monkeypatch):
    """Each refresh is a database round-trip plus plugin metadata imports."""
    from archihub.core.settings import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        scheduler_module,
        "get_settings" if hasattr(scheduler_module, "get_settings") else "MIN_REFRESH_INTERVAL",
        scheduler_module.MIN_REFRESH_INTERVAL,
        raising=False,
    )

    settings = Settings(
        SECRET_KEY="s", JWT_SECRET_KEY="j", FERNET_KEY="f", CELERY_BEAT_REFRESH_INTERVAL=1
    )
    monkeypatch.setattr("archihub.core.settings.get_settings", lambda: settings)

    assert scheduler_module.get_beat_refresh_interval() >= scheduler_module.MIN_REFRESH_INTERVAL
    get_settings.cache_clear()


class _Entry:
    def __init__(self, task):
        self.task = task
        self.schedule = "sched"
        self.args = ()
        self.kwargs = {}
        self.options = {}


class _FakeScheduler(scheduler_module.MongoPluginScheduler):
    """Bypass celery.beat.Scheduler.__init__, which wants a real app."""

    def __init__(self, existing):  # noqa: D107
        self.refresh_interval = 60.0
        self._last_schedule_refresh = 0.0
        self._schedule = existing

    @property
    def schedule(self):
        return self._schedule


def test_static_entries_survive_a_refresh():
    """Statically configured entries must not be wiped by a database refresh.

    Only the `plugin-schedule:` namespace is owned by MongoDB.
    """
    sched = _FakeScheduler(
        {
            "static-cleanup": _Entry("app.cleanup"),
            f"{DYNAMIC_SCHEDULE_PREFIX}p:t:once_a_day:01:00": _Entry("plugin.task"),
        }
    )

    preserved = sched._get_static_schedule_entries()

    assert set(preserved) == {"static-cleanup"}
    assert preserved["static-cleanup"]["task"] == "app.cleanup"


def test_refresh_survives_a_database_outage(monkeypatch):
    """Beat must keep running the schedule it already has.

    Dying on a transient outage would stop every background job on the instance.
    """
    sched = _FakeScheduler({})
    monkeypatch.setattr(
        scheduler_module,
        "build_plugin_beat_schedule",
        lambda mongo: (_ for _ in ()).throw(ConnectionError("mongo down")),
    )
    merged = []
    sched.merge_inplace = merged.append

    sched.refresh_schedule(force=True)  # must not raise

    assert merged == []


def test_refresh_is_rate_limited(monkeypatch):
    calls = []
    sched = _FakeScheduler({})
    monkeypatch.setattr(
        scheduler_module, "build_plugin_beat_schedule", lambda mongo: calls.append(1) or {}
    )
    sched.merge_inplace = lambda schedule: None

    sched.refresh_schedule(force=True)
    sched.refresh_schedule()  # inside the interval - must be skipped
    sched.refresh_schedule()

    assert len(calls) == 1
