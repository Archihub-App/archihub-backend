"""scheduleSystemTasks: validating and storing the schedule rows beat reads."""

from __future__ import annotations

import pytest


def build(slug: str):
    from archihub.plugins.framework.mounting import build_plugin

    return build_plugin(slug)


class FakeMongo:
    """Records the settings writes a plugin makes, and answers reads with one document."""

    def __init__(self, record=None):
        self.record = record
        self.operations: list[tuple[dict, dict]] = []

    def get_record(self, collection, filters=None, fields=None):
        return self.record

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.operations.append((filters, operator))
        return None


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: fake)
    return fake



def _schedule_plugin(monkeypatch, mongo, tasks=("system.index_resources",)):
    plugin = build("scheduleSystemTasks")
    monkeypatch.setattr(
        "archihub.plugins.scheduleSystemTasks.registered_task_names", lambda: list(tasks)
    )
    return plugin


def test_a_schedule_row_must_name_a_task_the_workers_have(monkeypatch, mongo):
    """Otherwise it is scheduled forever and fails every time, recorded only as
    a stream of failed jobs."""
    plugin = _schedule_plugin(monkeypatch, mongo)

    payload, status = plugin.save_settings(
        {"schedule_tasks": [{"task": "not.a.task", "periodicity": "once_a_day", "hour_execution": "03:00"}]}
    )

    assert status == 400
    assert mongo.operations == []


def test_an_unreachable_broker_does_not_block_saving_a_schedule(monkeypatch, mongo):
    """The check is only made when the list could actually be read."""
    plugin = _schedule_plugin(monkeypatch, mongo, tasks=())

    payload, status = plugin.save_settings(
        {"schedule_tasks": [{"task": "anything", "periodicity": "once_a_day", "hour_execution": "03:00"}]}
    )

    assert status == 200


@pytest.mark.parametrize(
    "row",
    [
        {"periodicity": "once_a_day"},
        {"task": "system.index_resources"},
        {"task": "system.index_resources", "periodicity": "once_a_day"},
        {"task": "system.index_resources", "periodicity": "every_x_hours", "interval_value": 0},
        {"task": "system.index_resources", "periodicity": "every_x_hours", "interval_value": "soon"},
    ],
)
def test_an_incomplete_schedule_row_is_refused(monkeypatch, mongo, row):
    plugin = _schedule_plugin(monkeypatch, mongo)

    assert plugin.save_settings({"schedule_tasks": [row]})[1] == 400
    assert mongo.operations == []


def test_an_interval_is_stored_as_a_number(monkeypatch, mongo):
    """`worker/schedule.py` compares it numerically."""
    plugin = _schedule_plugin(monkeypatch, mongo)

    plugin.save_settings(
        {"schedule_tasks": [{"task": "system.index_resources", "periodicity": "every_x_minutes", "interval_value": "30"}]}
    )

    _, operator = mongo.operations[0]
    stored = operator["$set"]["plugins_settings.scheduleSystemTasks"]
    assert stored["schedule_tasks"][0]["interval_value"] == 30


def test_the_task_picker_is_found_by_id_not_by_position(monkeypatch, mongo):
    """The legacy code wrote `resp['settings'][1]['fields']`, so inserting an
    entry above it filled in the wrong one."""
    from archihub.plugins import scheduleSystemTasks

    settings = {"settings": [{"id": "other"}, {"id": "schedule_tasks"}, {"id": "later"}]}

    assert scheduleSystemTasks._find_group(settings, "schedule_tasks") is settings["settings"][1]
    assert scheduleSystemTasks._find_group(settings, "absent") is None
