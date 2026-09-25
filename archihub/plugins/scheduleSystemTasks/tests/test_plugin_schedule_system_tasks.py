"""scheduleSystemTasks: validating and storing the schedule rows beat reads."""

from __future__ import annotations

import json

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
    """Inserting an entry above it must not fill in the wrong one."""
    from archihub.plugins import scheduleSystemTasks

    settings = {"settings": [{"id": "other"}, {"id": "schedule_tasks"}, {"id": "later"}]}

    assert scheduleSystemTasks._find_group(settings, "schedule_tasks") is settings["settings"][1]
    assert scheduleSystemTasks._find_group(settings, "absent") is None


# ---------------------------------------------------------------------------
# Who may change the schedule, and what it may contain
# ---------------------------------------------------------------------------


@pytest.fixture
def client_as(monkeypatch):
    """A client for this plugin's routes, signed in as a user holding ``roles``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from archihub.core.errors import register_exception_handlers
    from archihub.core.security import tokens

    def make(*roles):
        monkeypatch.setattr(
            "archihub.api.users.services.has_role", lambda username, role: role in roles
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(build("scheduleSystemTasks").build())
        client = TestClient(app, raise_server_exceptions=False)
        client.headers["Authorization"] = f"Bearer {tokens.create_access_token('someone')}"
        return client

    return make


def test_only_an_administrator_may_read_or_change_the_schedule(client_as, mongo, monkeypatch):
    monkeypatch.setattr(
        "archihub.plugins.scheduleSystemTasks.registered_task_names",
        lambda: ["system.index_resources"],
    )
    row = {"task": "system.index_resources", "periodicity": "every_x_minutes", "interval_value": 1}
    data = {"data": json.dumps({"schedule_tasks": [row]})}

    processing = client_as("processing")
    assert processing.get("/scheduleSystemTasks/settings/all").status_code == 403
    assert processing.post("/scheduleSystemTasks/settings", data=data).status_code == 403
    assert mongo.operations == []

    admin = client_as("admin")
    assert admin.post("/scheduleSystemTasks/settings", data=data).status_code == 200


def test_other_plugins_keep_their_settings_open_to_processing():
    from archihub.plugins.framework.base import SETTINGS_ROLES

    assert build("filesProcessing").settings_roles == SETTINGS_ROLES
    assert build("scheduleSystemTasks").settings_roles == ("admin",)


@pytest.mark.parametrize("tasks", [("testcontrol.reset", "system.index_resources"), ()])
def test_a_test_runner_task_is_never_scheduled(monkeypatch, mongo, tasks):
    """Refused whether or not the workers could be asked what they run."""
    plugin = _schedule_plugin(monkeypatch, mongo, tasks=tasks)

    row = {"task": "testcontrol.reset", "periodicity": "every_x_minutes", "interval_value": 5}

    payload, status = plugin.save_settings({"schedule_tasks": [row]})

    assert status == 400
    assert mongo.operations == []


def test_the_picker_does_not_offer_test_runner_tasks(monkeypatch):
    from archihub.plugins import scheduleSystemTasks

    class Inspector:
        def registered(self):
            return {"worker@a": ["testcontrol.reset", "system.index_resources"]}

    class Control:
        def inspect(self, timeout):
            return Inspector()

    monkeypatch.setattr("archihub.worker.celery_app.celery_app.control", Control())

    assert scheduleSystemTasks.registered_task_names() == ["system.index_resources"]
