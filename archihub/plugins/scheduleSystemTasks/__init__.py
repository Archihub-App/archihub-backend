"""scheduleSystemTasks — periodic execution of registered Celery tasks.

An administrator picks a task from the list of everything the workers have
registered, chooses a periodicity, and the beat scheduler turns that into a
schedule (``archihub/worker/schedule.py``). The plugin itself only stores the
choice; it declares the ``scheduler`` capability,
which is how the schedule builder knows to read its settings.

THIS PLUGIN'S ROLE CHECKS CARRY MORE WEIGHT THAN MOST. Its settings ARE a task
scheduler: writing them makes the workers run something, repeatedly, forever.
The role is a dependency on the route, resolved before the handler body runs.
"""

from __future__ import annotations

import logging

from archihub.core.i18n import gettext as _
from archihub.plugins.framework.base import ArchiPlugin

logger = logging.getLogger(__name__)

SLUG = "scheduleSystemTasks"

PERIODICITIES = (
    ("every_x_minutes", "Every x minutes"),
    ("every_x_hours", "Every x hours"),
    ("once_a_day", "Once a day"),
    ("once_a_week", "Once a week"),
    ("once_a_month", "Once a month"),
    ("once_a_year", "Once a year"),
)

INTERVAL_PERIODICITIES = {"every_x_minutes", "every_x_hours"}


class ScheduleSystemTasks(ArchiPlugin):
    def settings_payload(self, kind: str):
        """The settings form, with the task picker filled from the live workers.

        The list of tasks is asked of the broker rather than hardcoded, so a
        plugin that registers a new task appears here without a code change.
        """
        settings = self.translated_settings()

        group = _find_group(settings, "schedule_tasks")
        if group is not None:
            group["fields"] = _schedule_fields(registered_task_names())
            group["default"] = self.get_plugin_settings().get("schedule_tasks") or []

        return self.select_settings(settings, kind)

    def save_settings(self, data: dict):
        """Validate the schedule before storing it.

        Every row is checked and the FIRST failure refuses the whole save.
        """
        rows = data.get("schedule_tasks")
        if rows is None:
            return {"msg": _("Missing required fields")}, 400
        if not isinstance(rows, list):
            return {"msg": _("Missing required fields")}, 400

        known = registered_task_names()
        normalised = []

        for row in rows:
            if not isinstance(row, dict):
                return {"msg": _("Missing required fields")}, 400

            task = row.get("task")
            periodicity = row.get("periodicity")
            if not task or not periodicity:
                return {"msg": _("Missing required fields")}, 400

            # A task name that no worker has registered would be scheduled
            # forever and fail every time, recorded only as a stream of failed
            # jobs. Checked only when the list could actually be read - an
            # unreachable broker must not block a settings save.
            if known and task not in known:
                return {"msg": _("Unknown task {task}", task=task)}, 400

            row = dict(row)

            if periodicity in INTERVAL_PERIODICITIES:
                try:
                    interval = int(row.get("interval_value"))
                except (TypeError, ValueError):
                    return {"msg": _("Interval value must be a positive integer")}, 400
                if interval <= 0:
                    return {"msg": _("Interval value must be a positive integer")}, 400
                row["interval_value"] = interval
            elif not row.get("hour_execution"):
                return {"msg": _("Missing required fields")}, 400

            normalised.append(row)

        self.set_plugin_settings({**data, "schedule_tasks": normalised})
        return {"msg": _("Settings updated")}, 200


def registered_task_names() -> list[str]:
    """Every task name the running workers report, sorted and de-duplicated.

    ``[]`` when no worker answers.

    An unreachable broker returns an empty list rather than raising: the
    settings screen must still open, showing an empty picker, instead of a 500
    that says nothing about which of its several moving parts is down.
    """
    try:
        from archihub.worker.celery_app import celery_app

        registered = celery_app.control.inspect(timeout=2).registered()
    except Exception:
        logger.warning("Could not ask the workers which tasks they have registered")
        return []

    if not registered:
        return []

    names: set[str] = set()
    for tasks in registered.values():
        names.update(tasks or [])
    return sorted(names)


def _find_group(settings: dict, group_id: str) -> dict | None:
    """The settings entry with this id.

    BY ID, NOT BY POSITION, so adding an entry to `plugin_info` cannot make the
    picker fill in the wrong one.
    """
    for entry in settings.get("settings") or []:
        if isinstance(entry, dict) and entry.get("id") == group_id:
            return entry
    return None


def _schedule_fields(task_names: list[str]) -> list[dict]:
    """The per-row form: which task, how often, and when."""
    return [
        {
            "type": "select",
            "id": "task",
            "default": "",
            "options": [{"value": name, "label": name} for name in task_names],
            "required": True,
        },
        {
            "type": "select",
            "id": "periodicity",
            "default": "",
            "options": [{"value": value, "label": _(label)} for value, label in PERIODICITIES],
            "required": True,
        },
        {
            "type": "number",
            "id": "interval_value",
            "default": 1,
            "required": False,
            "idCondition": "periodicity",
            "conditionValue": sorted(INTERVAL_PERIODICITIES),
        },
        {
            "type": "select",
            "id": "hour_execution",
            "default": "",
            "options": [{"value": f"{h:02d}:00", "label": f"{h:02d}:00"} for h in range(24)],
            "required": False,
            "idCondition": "periodicity",
            "conditionValue": ["once_a_day", "once_a_week", "once_a_month", "once_a_year"],
        },
    ]


plugin_info = {
    "name": "Programador de tareas",
    "description": "Plugin para la gestión de tareas programadas",
    "version": "0.1",
    "author": "",
    "type": ["settings"],
    "capabilities": ["scheduler"],
    "settings": {
        "settings": [
            {
                "type": "instructions",
                "title": "Instrucciones",
                "text": (
                    "Desde aquí puedes programar tareas del sistema para que se ejecuten "
                    "periódicamente. Puedes seleccionar la tarea, la periodicidad y la hora "
                    'de ejecución o un intervalo. Si la periodicidad es "Cada x minutos" o '
                    '"Cada x horas", debes indicar el intervalo. Si la periodicidad es "Una '
                    'vez a la semana", se ejecutará el día lunes a la hora seleccionada. Si '
                    'la periodicidad es "Una vez al mes", se ejecutará el primer día del mes '
                    'a la hora seleccionada. Si la periodicidad es "Una vez al año", se '
                    "ejecutará el primer día del año a la hora seleccionada."
                ),
            },
            {
                "type": "multiple",
                "title": "Tareas a programar",
                "id": "schedule_tasks",
                "fields": [],
            },
        ],
    },
    "actions": [],
}


def build() -> ScheduleSystemTasks:
    return ScheduleSystemTasks(SLUG, plugin_info, module_file=__file__)
