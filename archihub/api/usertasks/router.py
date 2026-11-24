"""Editorial review task routes.

Permission levels enforced:

* Users with ``editor`` or ``transcriber`` role may assign a task to themselves.
* Assigning work to another user, listing assignable editors, listing all tasks,
  approving tasks, and returning tasks to pending require ``admin``,
  ``team_lead``, or the ``usertasks.manage`` permission.
* Task participants may view their own tasks, comment on them, and submit them
  for review.

A role failure answers 403; 401 is reserved for "I do not know who you are".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.usertasks import services
from archihub.core.errors import PermissionDeniedError
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    MSG_INSUFFICIENT_PERMISSIONS,
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_permission,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usertasks", tags=["Review tasks"])

require_lead = require_permission("usertasks.manage")


def require_task_creator(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Allow task managers (who assign others) and assignable roles (who self-assign)."""
    from archihub.api.users.services import has_role

    if not (
        services.is_task_manager(current_user.username)
        or any(has_role(current_user.username, role) for role in services.ASSIGNABLE_ROLES)
    ):
        raise PermissionDeniedError(_(MSG_INSUFFICIENT_PERMISSIONS), status_code=ROLE_FAILURE_STATUS)
    return current_user

_ROLE_RESPONSES = {401: {"description": "Missing or invalid token"},
        403: {"description": "Insufficient role"}}


def _respond(result) -> JSONResponse:
    """Render a service's ``(payload, status)`` result.

    Through ``core.responses`` rather than ``JSONResponse`` directly: a
    payload carrying a ``datetime`` or an ``ObjectId`` must not 500.
    """
    payload, status_code = result
    return json_response(payload, status_code)


@router.post(
    "/tasks",
    responses={200: {"description": "Paginated review tasks"}, 400: {"description": "Missing status"}, **_ROLE_RESPONSES},
)
def get_tasks(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """List review tasks by status.

    Admins and team leads may view all tasks or filter by user.
    Other users can view tasks they created or that are assigned to them.
    """
    if "status" not in body:
        return JSONResponse(
            status_code=400, content={"error": _("You must specify the status of the tasks")}
        )

    is_lead = services.is_task_manager(current_user.username)
    requested_user = body.get("user")
    include_created = False

    if not is_lead:
        if requested_user and requested_user != current_user.username:
            return JSONResponse(
                status_code=403,
                content={"error": _("You do not have sufficient permissions")},
            )
        requested_user = current_user.username
        include_created = True

    return _respond(
        services.get_all_tasks(
            {
                "status": body["status"],
                "user": requested_user,
                "include_created": include_created,
                "page": body.get("page", 0),
            }
        )
    )


@router.get(
    "/editors",
    responses={200: {"description": "Users who can be assigned review work"}, **_ROLE_RESPONSES},
)
def get_editors(current_user: CurrentUser = Depends(require_lead)) -> JSONResponse:
    """List assignable editors."""
    return _respond(services.get_editors())


@router.get(
    "/notifications",
    responses={200: {"description": "Active review-task notifications for the current user"}, **_ROLE_RESPONSES},
)
def get_notifications(
    page: int = 0,
    limit: int = services.NOTIFICATIONS_PAGE_SIZE,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """List active review-task notifications for the authenticated user."""
    return _respond(services.get_notifications(current_user.username, page=page, limit=limit))


@router.put(
    "/notifications/read",
    responses={200: {"description": "Notifications deactivated"}, **_ROLE_RESPONSES},
)
def deactivate_notifications(
    body: dict = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Deactivate all or selected active task notifications for the current user."""
    return _respond(services.deactivate_notifications(current_user.username, body))


@router.put(
    "/notifications/{notification_id}/read",
    responses={
        200: {"description": "Notification deactivated"},
        404: {"description": "Notification not found"},
        **_ROLE_RESPONSES,
    },
)
def deactivate_notification(
    notification_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Deactivate one active task notification belonging to the current user."""
    return _respond(services.deactivate_notification(notification_id, current_user.username))


@router.get(
    "/record/{record_id}",
    responses={200: {"description": "The open task for that record"}, 404: {"description": "No task"}, **_ROLE_RESPONSES},
)
def get_record_tasks(
    record_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The open review task for one record."""
    return _respond(services.get_record_tasks(record_id, current_user.username))


@router.post(
    "",
    status_code=201,
    responses={201: {"description": "Task assigned"}, 400: {"description": "Invalid, or one is already open"}, **_ROLE_RESPONSES},
)
def create_task(
    body: dict = Body(...),
    current_user: CurrentUser = Depends(require_task_creator),
) -> JSONResponse:
    """Assign review work on a resource or record."""
    is_lead = services.is_task_manager(current_user.username)
    return _respond(services.create_task(body, current_user.username, is_team_lead=is_lead))


@router.put(
    "/{task_id}",
    responses={
        200: {"description": "Task updated"},
        400: {"description": "Already approved, or nothing to change"},
        404: {"description": "Task not found"},
        **_ROLE_RESPONSES,
    },
)
def update_task(
    task_id: str,
    body: dict = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Comment on a task, or update its status.

    Approval and returning tasks to pending are restricted to admins and team leads.
    """
    is_team_lead = services.is_task_manager(current_user.username)
    return _respond(services.update_task(task_id, body, current_user.username, is_team_lead))


@router.delete(
    "/{task_id}",
    responses={
        200: {"description": "Task deleted"},
        404: {"description": "Task not found"},
        **_ROLE_RESPONSES,
    },
)
def delete_task(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Delete a review task.

    Only the user who created or assigned the task may delete it.
    """
    return _respond(services.delete_task(task_id, current_user.username))


# Declared last: a literal path must not be captured by the parameterised one.
@router.get(
    "/{resource_id}",
    responses={200: {"description": "The open task for that resource"}, 404: {"description": "No task"}, **_ROLE_RESPONSES},
)
def get_resource_tasks(
    resource_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """The open review task for one resource."""
    return _respond(services.get_resource_tasks(resource_id, current_user.username))

