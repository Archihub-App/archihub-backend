"""Building Celery beat entries from plugin settings.

Port of ``app/celery_schedule.py``. Pure functions - no Celery app, no Flask, no
database connection of its own - so the scheduling rules can be tested without
any infrastructure.

An administrator picks a registered Celery task and a periodicity in the
``scheduleSystemTasks`` settings UI; that is stored in the ``system`` collection
and translated here into ``timedelta``/``crontab`` beat entries.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery.schedules import crontab

# Module-scope for the same reason as in scheduler.py: this runs on beat's
# scheduler thread, where a function-level import can observe a partially
# initialised module and fail.
from archihub.plugins.framework.discovery import get_active_plugin_slugs, plugin_has_capability

logger = logging.getLogger(__name__)

DYNAMIC_SCHEDULE_PREFIX = "plugin-schedule:"

INTERVAL_PERIODICITIES = {"every_x_minutes", "every_x_hours"}

# Guard against a settings typo turning into a task that runs every minute
# forever. The UI offers minutes and hours, so an interval beyond a year is
# certainly a mistake.
MAX_INTERVAL_MINUTES = 366 * 24 * 60


def parse_execution_time(hour_execution: object) -> tuple[int, int]:
    """Parse ``"HH:MM"`` or a bare hour into ``(hour, minute)``.

    Out-of-range values are clamped rather than passed to ``crontab``, which
    would raise and take the whole beat schedule down with it - one bad settings
    row must not stop every other scheduled task.
    """
    if hour_execution is None:
        return 0, 0

    hour: object
    minute: object = 0

    if isinstance(hour_execution, str):
        text = hour_execution.strip()
        if ":" in text:
            head, _, tail = text.partition(":")
            hour, minute = head, tail
        else:
            hour = text
    else:
        hour = hour_execution

    try:
        hour_int = int(hour)
        minute_int = int(minute)
    except (TypeError, ValueError):
        logger.warning("Unparseable hour_execution %r; defaulting to 00:00", hour_execution)
        return 0, 0

    if not 0 <= hour_int <= 23 or not 0 <= minute_int <= 59:
        logger.warning("hour_execution %r out of range; defaulting to 00:00", hour_execution)
        return 0, 0

    return hour_int, minute_int


def get_schedule(task_config: dict) -> timedelta | crontab | None:
    """Translate one settings row into a Celery schedule, or None if invalid."""
    periodicity = task_config.get("periodicity")

    if periodicity in INTERVAL_PERIODICITIES:
        try:
            interval_value = int(task_config.get("interval_value"))
        except (TypeError, ValueError):
            logger.warning("Non-numeric interval_value in %r", task_config)
            return None

        if interval_value <= 0:
            logger.warning("Non-positive interval_value in %r", task_config)
            return None

        minutes = interval_value if periodicity == "every_x_minutes" else interval_value * 60
        if minutes > MAX_INTERVAL_MINUTES:
            logger.warning("interval_value %r exceeds the maximum; ignoring", interval_value)
            return None

        return timedelta(minutes=minutes)

    hour, minute = parse_execution_time(task_config.get("hour_execution"))

    if periodicity == "once_a_day":
        return crontab(hour=hour, minute=minute)
    if periodicity == "once_a_week":
        return crontab(hour=hour, minute=minute, day_of_week="0")
    if periodicity == "once_a_month":
        return crontab(hour=hour, minute=minute, day_of_month="1")
    if periodicity == "once_a_year":
        return crontab(hour=hour, minute=minute, day_of_month="1", month_of_year="1")

    logger.warning("Unknown periodicity %r", periodicity)
    return None


def build_schedule_name(plugin_name: str, task_config: dict) -> str:
    """A stable, unique key for a scheduled entry.

    The prefix distinguishes Mongo-driven entries from statically configured
    ones, so a refresh can replace the former without disturbing the latter.
    """
    periodicity = task_config.get("periodicity", "")
    if periodicity in INTERVAL_PERIODICITIES:
        execution_label = task_config.get("interval_value", "")
    else:
        execution_label = task_config.get("hour_execution", "")

    task = task_config.get("task", "")
    return f"{DYNAMIC_SCHEDULE_PREFIX}{plugin_name}:{task}:{periodicity}:{execution_label}"


def build_schedule_entry(task_config: dict) -> dict | None:
    task_name = task_config.get("task")
    if not task_name:
        return None

    schedule = get_schedule(task_config)
    if schedule is None:
        return None

    return {
        "task": task_name,
        "schedule": schedule,
        "args": (),
        "kwargs": {},
        "options": {},
    }


def build_plugin_beat_schedule(mongo: object | None = None) -> dict:
    """Assemble every dynamic beat entry from plugin settings.

    Only plugins declaring the ``scheduler`` capability contribute - today just
    ``scheduleSystemTasks``, which acts as a scheduling front-end for tasks
    belonging to *any* plugin.
    """
    if mongo is None:
        from archihub.infra.mongo import get_mongo

        mongo = get_mongo()

    record = (
        mongo.get_record(
            "system", {"name": "active_plugins"}, fields={"data": 1, "plugins_settings": 1}
        )
        or {}
    )
    plugin_settings = record.get("plugins_settings") or {}

    schedule: dict[str, dict] = {}

    for slug in get_active_plugin_slugs(mongo):
        if not plugin_has_capability(slug, "scheduler"):
            continue

        for task_config in (plugin_settings.get(slug) or {}).get("schedule_tasks", []) or []:
            entry = build_schedule_entry(task_config)
            if entry is None:
                logger.warning("Skipping invalid scheduled task config for %s: %r", slug, task_config)
                continue
            schedule[build_schedule_name(slug, task_config)] = entry

    return schedule
