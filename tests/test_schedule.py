"""Beat schedule construction from plugin settings.

Every value here originates in an admin-facing settings form, so malformed input
is expected rather than exceptional. The rule throughout: one bad row is skipped,
never allowed to take down the whole schedule - otherwise a single typo silently
stops every scheduled job on the instance.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from celery.schedules import crontab

from archihub.worker import schedule as sched


# ---------------------------------------------------------------------------
# parse_execution_time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("14:30", (14, 30)),
        ("09:05", (9, 5)),
        ("7", (7, 0)),
        (7, (7, 0)),
        ("  14:30  ", (14, 30)),
        (None, (0, 0)),
        ("", (0, 0)),
        ("not-a-time", (0, 0)),
        ("14:xx", (0, 0)),
    ],
)
def test_parse_execution_time(value, expected):
    assert tuple(sched.parse_execution_time(value)) == expected


@pytest.mark.parametrize("value", ["25:00", "-1", "12:75", "99"])
def test_out_of_range_times_are_clamped_not_raised(value):
    """crontab() rejects out-of-range values by raising.

    Letting that propagate would abort the whole schedule build, so one
    nonsensical row would stop every other scheduled task on the instance.
    """
    assert tuple(sched.parse_execution_time(value)) == (0, 0)


# ---------------------------------------------------------------------------
# get_schedule
# ---------------------------------------------------------------------------


def test_every_x_minutes():
    assert sched.get_schedule({"periodicity": "every_x_minutes", "interval_value": 15}) == timedelta(
        minutes=15
    )


def test_every_x_hours():
    assert sched.get_schedule({"periodicity": "every_x_hours", "interval_value": 6}) == timedelta(
        hours=6
    )


def test_numeric_string_interval_is_accepted():
    """Settings arrive as JSON from a form, so numbers are often strings."""
    assert sched.get_schedule(
        {"periodicity": "every_x_minutes", "interval_value": "15"}
    ) == timedelta(minutes=15)


@pytest.mark.parametrize("interval", [0, -5, None, "", "abc"])
def test_invalid_intervals_are_rejected(interval):
    """A zero or negative interval would mean 'run continuously'."""
    assert sched.get_schedule({"periodicity": "every_x_minutes", "interval_value": interval}) is None


def test_absurdly_large_interval_is_rejected():
    assert sched.get_schedule({"periodicity": "every_x_hours", "interval_value": 10**6}) is None


def test_calendar_periodicities():
    assert sched.get_schedule({"periodicity": "once_a_day", "hour_execution": "03:00"}) == crontab(
        hour=3, minute=0
    )
    assert sched.get_schedule({"periodicity": "once_a_week", "hour_execution": "03:00"}) == crontab(
        hour=3, minute=0, day_of_week="0"
    )
    assert sched.get_schedule({"periodicity": "once_a_month", "hour_execution": "03:00"}) == crontab(
        hour=3, minute=0, day_of_month="1"
    )
    assert sched.get_schedule({"periodicity": "once_a_year", "hour_execution": "03:00"}) == crontab(
        hour=3, minute=0, day_of_month="1", month_of_year="1"
    )


def test_unknown_periodicity_is_rejected():
    assert sched.get_schedule({"periodicity": "every_full_moon"}) is None
    assert sched.get_schedule({}) is None


# ---------------------------------------------------------------------------
# entries and names
# ---------------------------------------------------------------------------


def test_entry_requires_a_task_name():
    assert sched.build_schedule_entry({"periodicity": "once_a_day", "hour_execution": "01:00"}) is None


def test_entry_shape():
    entry = sched.build_schedule_entry(
        {"task": "plugin.do_thing", "periodicity": "once_a_day", "hour_execution": "01:00"}
    )
    assert entry["task"] == "plugin.do_thing"
    assert set(entry) == {"task", "schedule", "args", "kwargs", "options"}


def test_schedule_names_are_prefixed_and_distinct():
    """The prefix separates Mongo-driven entries from static ones, so a refresh
    can replace the former without disturbing the latter."""
    a = sched.build_schedule_name(
        "scheduleSystemTasks", {"task": "t", "periodicity": "once_a_day", "hour_execution": "01:00"}
    )
    b = sched.build_schedule_name(
        "scheduleSystemTasks", {"task": "t", "periodicity": "once_a_day", "hour_execution": "02:00"}
    )
    assert a.startswith(sched.DYNAMIC_SCHEDULE_PREFIX)
    assert a != b  # same task at two different times must not collide


def test_interval_schedules_are_named_by_interval():
    name = sched.build_schedule_name(
        "p", {"task": "t", "periodicity": "every_x_minutes", "interval_value": 15}
    )
    assert name.endswith(":15")


# ---------------------------------------------------------------------------
# build_plugin_beat_schedule
# ---------------------------------------------------------------------------


class FakeMongo:
    def __init__(self, record):
        self._record = record

    def get_record(self, collection, filters, fields=None):
        return self._record


def test_only_scheduler_capable_plugins_contribute(monkeypatch):
    monkeypatch.setattr(
        "archihub.worker.schedule.get_active_plugin_slugs",
        lambda mongo=None: ["scheduleSystemTasks", "filesProcessing"],
    )
    monkeypatch.setattr(
        "archihub.worker.schedule.plugin_has_capability",
        lambda slug, capability: slug == "scheduleSystemTasks",
    )

    mongo = FakeMongo(
        {
            "plugins_settings": {
                "scheduleSystemTasks": {
                    "schedule_tasks": [
                        {"task": "a.task", "periodicity": "once_a_day", "hour_execution": "02:00"}
                    ]
                },
                # Present but the plugin lacks the capability - must be ignored.
                "filesProcessing": {
                    "schedule_tasks": [{"task": "b.task", "periodicity": "once_a_day"}]
                },
            }
        }
    )

    built = sched.build_plugin_beat_schedule(mongo)
    assert len(built) == 1
    assert next(iter(built)).startswith(f"{sched.DYNAMIC_SCHEDULE_PREFIX}scheduleSystemTasks:")


def test_invalid_rows_are_skipped_without_losing_valid_ones(monkeypatch):
    """The important property: one broken row must not stop the others."""
    monkeypatch.setattr(
        "archihub.worker.schedule.get_active_plugin_slugs",
        lambda mongo=None: ["scheduleSystemTasks"],
    )
    monkeypatch.setattr(
        "archihub.worker.schedule.plugin_has_capability", lambda slug, capability: True
    )

    mongo = FakeMongo(
        {
            "plugins_settings": {
                "scheduleSystemTasks": {
                    "schedule_tasks": [
                        {"task": "good.task", "periodicity": "once_a_day", "hour_execution": "02:00"},
                        {"periodicity": "once_a_day"},                       # no task name
                        {"task": "bad.task", "periodicity": "nonsense"},      # bad periodicity
                        {"task": "bad2.task", "periodicity": "every_x_minutes", "interval_value": 0},
                    ]
                }
            }
        }
    )

    built = sched.build_plugin_beat_schedule(mongo)
    assert len(built) == 1
    assert list(built.values())[0]["task"] == "good.task"


def test_no_settings_yields_an_empty_schedule(monkeypatch):
    monkeypatch.setattr(
        "archihub.worker.schedule.get_active_plugin_slugs", lambda mongo=None: []
    )
    assert sched.build_plugin_beat_schedule(FakeMongo({})) == {}
