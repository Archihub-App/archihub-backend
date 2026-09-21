"""Celery beat scheduler backed by MongoDB.

Standard Celery beat reads its schedule once at startup, so adding a scheduled
task through the admin UI would require restarting the beat process. This
scheduler re-reads the ``system`` collection on a timer and merges the result
into the live schedule, so settings changes take effect without a restart.

Statically configured entries (anything whose key does not start with
``plugin-schedule:``) are preserved untouched across refreshes.
"""

from __future__ import annotations

import logging
import time

from celery.beat import Scheduler

# Imported at module scope, NOT inside refresh_schedule().
#
# Beat runs its scheduler on a separate thread and calls setup_schedule() during
# Celery's own bootstrap, while other threads may still be importing. A
# function-level `from archihub.infra.mongo import get_mongo` there loses that
# race and raises "cannot import name ... from partially initialized module",
# which the surrounding try/except then swallows as a failed refresh - so beat
# comes up with an empty schedule and every scheduled task silently never runs.
# Observed on a real beat start; see tests/test_scheduler.py.
#
# Importing here is cheap: the client is constructed lazily on first use, so
# nothing connects to MongoDB at import time.
from archihub.infra.mongo import get_mongo
from archihub.worker.schedule import DYNAMIC_SCHEDULE_PREFIX, build_plugin_beat_schedule

logger = logging.getLogger(__name__)

# Never poll faster than this, regardless of configuration: each refresh is a
# database round-trip plus a plugin-metadata import per scheduler-capable plugin.
MIN_REFRESH_INTERVAL = 5.0


def get_beat_refresh_interval() -> float:
    from archihub.core.settings import get_settings

    try:
        interval = float(get_settings().celery_beat_refresh_interval)
    except (TypeError, ValueError):
        interval = 60.0
    return max(MIN_REFRESH_INTERVAL, interval)


class MongoPluginScheduler(Scheduler):
    """Beat scheduler that hot-reloads plugin schedules from MongoDB."""

    def __init__(self, *args, **kwargs):
        self.refresh_interval = get_beat_refresh_interval()
        self._last_schedule_refresh = 0.0
        super().__init__(*args, **kwargs)

    def setup_schedule(self) -> None:
        super().setup_schedule()
        self.refresh_schedule(force=True)

    def tick(self, *args, **kwargs):
        self.refresh_schedule()
        return super().tick(*args, **kwargs)

    def refresh_schedule(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_schedule_refresh < self.refresh_interval:
            return

        self._last_schedule_refresh = now

        try:
            schedule = self._get_static_schedule_entries()
            schedule.update(build_plugin_beat_schedule(get_mongo()))
            self.merge_inplace(schedule)
        except Exception:
            # Beat must survive a database blip: keep running the schedule
            # already in memory rather than dying and stopping every job.
            logger.exception("Failed to refresh the Celery beat schedule from MongoDB")

    def _get_static_schedule_entries(self) -> dict:
        """Snapshot the non-dynamic entries so a refresh does not drop them."""
        return {
            name: {
                "task": entry.task,
                "schedule": entry.schedule,
                "args": entry.args,
                "kwargs": entry.kwargs,
                "options": entry.options,
            }
            for name, entry in self.schedule.items()
            if not name.startswith(DYNAMIC_SCHEDULE_PREFIX)
        }
