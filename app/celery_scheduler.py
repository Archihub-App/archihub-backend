import logging
import os
import time

from celery.beat import Scheduler

from app.celery_schedule import DYNAMIC_SCHEDULE_PREFIX, build_plugin_beat_schedule
from app.utils import DatabaseHandler


logger = logging.getLogger(__name__)


def get_beat_refresh_interval():
    try:
        refresh_interval = float(os.environ.get('CELERY_BEAT_REFRESH_INTERVAL', 60))
    except (TypeError, ValueError):
        refresh_interval = 60.0

    return max(5.0, refresh_interval)


class MongoPluginScheduler(Scheduler):
    def __init__(self, *args, **kwargs):
        self.mongodb = DatabaseHandler.DatabaseHandler()
        self.refresh_interval = get_beat_refresh_interval()
        self._last_schedule_refresh = 0.0
        super().__init__(*args, **kwargs)

    def setup_schedule(self):
        super().setup_schedule()
        self.refresh_schedule(force=True)

    def tick(self, *args, **kwargs):
        self.refresh_schedule()
        return super().tick(*args, **kwargs)

    def refresh_schedule(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_schedule_refresh < self.refresh_interval:
            return

        self._last_schedule_refresh = now

        try:
            schedule = self._get_static_schedule_entries()
            schedule.update(build_plugin_beat_schedule(self.mongodb))
            self.merge_inplace(schedule)
        except Exception:
            logger.exception('Failed to refresh Celery beat schedule from MongoDB')

    def _get_static_schedule_entries(self):
        schedule = {}

        for name, entry in self.schedule.items():
            if name.startswith(DYNAMIC_SCHEDULE_PREFIX):
                continue

            schedule[name] = {
                'task': entry.task,
                'schedule': entry.schedule,
                'args': entry.args,
                'kwargs': entry.kwargs,
                'options': entry.options,
            }

        return schedule