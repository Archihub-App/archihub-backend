"""Background task routes.

Port of ``app/api/tasks/routes.py``.

ACCESS CONTROL ON THE TWO PER-USER ROUTES IS STATED POSITIVELY, in
``services.may_read_tasks_of``: your own tasks, or anyone's if you are an
administrator. An allow-rule is checkable at a glance; the equivalent written as
exclusions is not, and a guard that refuses only one special username lets every
other person's task list through.

That is a genuine behaviour change rather than a port, and it is the correct
direction - the frontend only ever requests the signed-in user's own tasks (or
``automatic`` from an admin screen), so nothing legitimate depended on the gap.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.tasks import services
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Tasks"])

require_admin = require_role_any("admin")

_ROLE_RESPONSES = {401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"}}
MSG_UNAUTHORIZED = "You don't have the required authorization"


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


def _authorize(current_user: CurrentUser, requested_user: str) -> JSONResponse | None:
    from archihub.api.users.services import has_role

    if services.may_read_tasks_of(
        current_user.username, requested_user, has_role(current_user.username, "admin")
    ):
        return None

    logger.info(
        "Denied %s access to the task list of %s", current_user.username, requested_user
    )
    return JSONResponse(
        status_code=ROLE_FAILURE_STATUS, content={"msg": _(MSG_UNAUTHORIZED)}
    )


@router.get(
    "",
    responses={200: {"description": "Tasks currently executing across workers"}, **_ROLE_RESPONSES},
)
def get_active(current_user: CurrentUser = Depends(require_admin)) -> JSONResponse:
    """What every worker is executing right now.

    Asks the workers directly over the Celery control plane, so it reflects the
    live cluster rather than the database's view of it. Returns an empty object
    when no worker answers, rather than failing - "nothing is running" and "no
    worker is up" look the same from here, and /health/ready is what
    distinguishes them.
    """
    try:
        from archihub.worker.celery_app import celery_app

        active = celery_app.control.inspect().active()
        return JSONResponse(status_code=200, content=active or {})
    except Exception as exc:
        logger.exception("Could not inspect active tasks")
        return JSONResponse(status_code=500, content={"msg": str(exc)})


@router.post(
    "/{user}",
    responses={200: {"description": "That user's tasks, newest first"}, **_ROLE_RESPONSES},
)
def get_tasks(
    user: str,
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """List a user's tasks, reconciling any that have finished since."""
    denied = _authorize(current_user, user)
    if denied is not None:
        return denied
    return _respond(services.get_tasks(user, body))


@router.get(
    "/total/{user}",
    responses={200: {"description": "How many tasks that user has"}, **_ROLE_RESPONSES},
)
def get_tasks_total(
    user: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Count a user's tasks, for pagination."""
    denied = _authorize(current_user, user)
    if denied is not None:
        return denied
    return JSONResponse(status_code=200, content=services.get_tasks_total(user))


@router.delete(
    "/{task_id}",
    responses={200: {"description": "Task stopped"}, **_ROLE_RESPONSES},
)
def delete_task(
    task_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> JSONResponse:
    """Stop a running task.

    Terminates the worker process handling it - the only thing that reliably
    stops the long-running media and AI jobs this exists for.
    """
    return _respond(services.stop_task(task_id, current_user.username))
