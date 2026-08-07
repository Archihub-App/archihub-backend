"""Background task records.

PARTIAL PORT: ``add_task`` and ``has_task`` only, because the hook bus needs
them. The polling endpoints (``get_tasks`` and friends) land with the rest of the
domain in Phase 3.

``has_task`` IS A REWRITE, NOT A PORT. The legacy version
(``app/api/tasks/services.py:158``) cannot work as written:

1. It rebinds ``task`` from the Mongo document to a ``TaskUpdate`` Pydantic model
   (``task = TaskUpdate(**update)``) and then immediately does
   ``mongodb.update_record('tasks', {'taskId': task['taskId']}, task)``.
   A Pydantic model is not subscriptable, so this raises
   ``TypeError: 'TaskUpdate' object is not subscriptable`` - verified.
2. Three branches then assign to ``t['status']`` / ``t['result']``, but ``t`` is
   never defined in that function. That is a ``NameError``.
3. Both are caught by a bare ``except Exception`` whose handler
   ``return True`` - i.e. "this user already has a task running".
4. When the task really is still pending, no branch matches and the function
   falls off the end, returning ``None``.

So the result is inverted in both directions that matter: a FINISHED task
reports "still running" (blocking the user from starting another), and a
GENUINELY RUNNING task reports "nothing running" (allowing a duplicate). This is
the guard ``mqttHandler`` and ``mailLabeler`` use to avoid launching concurrent
jobs, so in practice it both blocks legitimate work and permits the duplicates
it exists to prevent.

The rewrite below keeps the intended contract: True when the named task is
genuinely still in flight for that user.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from celery.result import AsyncResult

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Celery states that mean "not finished".
IN_FLIGHT_STATES = {"PENDING", "STARTED", "RETRY", "PROGRESS", "RECEIVED"}

SYSTEM_USERS = {"automatic", "system"}


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def add_task(
    task_id: str,
    task_name: str,
    username: str,
    result_type: str,
    params: dict | None = None,
) -> None:
    """Record a queued task so it can be polled and reported.

    Unknown users are rejected, except the reserved ``automatic``/``system``
    identities used by hooks and the scheduler.
    """
    mongo = _mongo()

    user = mongo.get_record("users", {"username": username}, fields={"username": 1})
    if not user and username not in SYSTEM_USERS:
        # Legacy returned ({'msg': ...}, 404) from this void function, which no
        # caller inspected - so the task simply went unrecorded and invisible.
        logger.error("Refusing to record task %s for unknown user %r", task_id, username)
        raise ValueError(f"Unknown user: {username}")

    mongo.insert_record(
        "tasks",
        {
            "taskId": task_id,
            "user": user["username"] if user else "automatic",
            "status": STATUS_PENDING,
            "name": task_name,
            "resultType": result_type,
            "date": datetime.now(),
            "params": params or {},
        },
    )
    logger.debug("Recorded task %s (%s) for %s", task_id, task_name, username)


def _sync_finished_task(task_id: str, result: AsyncResult) -> None:
    """Write a terminal Celery state back to the task document."""
    if result.successful():
        value = result.result
        # Mongo cannot store arbitrary objects; keep dict/list/str as-is and
        # stringify anything else rather than raising on insert.
        if not isinstance(value, (str, dict, list, int, float, bool, type(None))):
            value = str(value)
        update = {"status": STATUS_COMPLETED, "result": value}
    else:
        # Do NOT persist result.result on failure: for an exception it is the
        # exception object, whose string form can carry connection strings,
        # file paths and other internals into a record the frontend renders.
        logger.warning("Task %s failed", task_id, exc_info=result.result if result.failed() else None)
        update = {"status": STATUS_FAILED, "result": ""}

    _mongo().update_record("tasks", {"taskId": task_id}, update)


def has_task(username: str, task_name: str) -> bool:
    """Whether ``username`` has ``task_name`` genuinely in flight.

    Returns True only when a pending record exists AND Celery agrees the task
    has not finished. Finished tasks are reconciled into the database on the way
    past, which is what stops a crashed or completed job from blocking the user
    forever.

    Errors resolve to False (allow the work) rather than True (block it). The
    legacy handler did the opposite, so any transient broker hiccup silently
    locked the user out of a feature with no indication why.
    """
    try:
        task = _mongo().get_record(
            "tasks", {"user": username, "name": task_name, "status": STATUS_PENDING}
        )
        if not task:
            return False

        task_id = task.get("taskId")
        if not task_id:
            logger.warning("Task record for %s/%s has no taskId", username, task_name)
            return False

        result = AsyncResult(task_id)

        if result.state in IN_FLIGHT_STATES and not result.ready():
            return True

        if result.ready():
            _sync_finished_task(task_id, result)
            return False

        # Unknown state: treat as not running so the user is never stuck.
        logger.info("Task %s in unexpected state %r", task_id, result.state)
        return False

    except Exception:
        logger.exception("Could not determine task state for %s/%s", username, task_name)
        return False


def get_task_status(task_id: str) -> dict[str, Any]:
    """Current state of one task, for polling endpoints."""
    result = AsyncResult(task_id)

    if result.state in IN_FLIGHT_STATES and not result.ready():
        payload: dict[str, Any] = {"status": STATUS_PENDING}
        if result.state == "PROGRESS" and isinstance(result.info, dict):
            payload["result"] = dict(result.info)
        return payload

    if result.successful():
        return {"status": STATUS_COMPLETED, "result": result.result}

    if result.failed():
        # Message withheld deliberately - see _sync_finished_task.
        return {"status": STATUS_FAILED, "result": ""}

    return {"status": result.state.lower()}
