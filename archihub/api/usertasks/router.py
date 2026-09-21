"""Editorial review task routes.

TWO DISTINCT PERMISSION LEVELS, and the difference matters:

* Reading and assigning work requires ``admin`` or ``team_lead``.
* Updating a task additionally admits ``editor`` - the person doing the work
  needs to comment on it - but **only a team lead may approve**. An editor
  signing off their own review would defeat the review step, so that check lives
  in the service, which is the only place that sees the requested status.

A role failure answers 403; 401 is reserved for "I do not know who you are".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from archihub.api.usertasks import services
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import (
    ROLE_FAILURE_STATUS,
    CurrentUser,
    get_current_user,
    require_role_any,
)
from archihub.core.responses import json_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usertasks", tags=["Review tasks"])

require_lead = require_role_any("admin", "team_lead")
require_editor_or_lead = require_role_any(
    "admin", "team_lead", "editor"
)

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
    current_user: CurrentUser = Depends(require_lead),
) -> JSONResponse:
    """List review tasks by status.

    A caller may always list their own; requesting someone else's requires
    admin or team_lead - which the dependency has already established, so the
    only extra check needed is the self case.
    """
    if "status" not in body:
        return JSONResponse(
            status_code=400, content={"error": _("You must specify the status of the tasks")}
        )

    return _respond(
        services.get_all_tasks(
            {
                "status": body["status"],
                "user": body.get("user"),
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
    "/record/{record_id}",
    responses={200: {"description": "The open task for that record"}, 404: {"description": "No task"}, **_ROLE_RESPONSES},
)
def get_record_tasks(
    record_id: str,
    current_user: CurrentUser = Depends(require_lead),
) -> JSONResponse:
    """The open review task for one record."""
    return _respond(services.get_record_tasks(record_id))


@router.post(
    "",
    status_code=201,
    responses={201: {"description": "Task assigned"}, 400: {"description": "Invalid, or one is already open"}, **_ROLE_RESPONSES},
)
def create_task(
    body: dict = Body(...),
    current_user: CurrentUser = Depends(require_lead),
) -> JSONResponse:
    """Assign review work on a resource or record."""
    return _respond(services.create_task(body, current_user.username))


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
    current_user: CurrentUser = Depends(require_editor_or_lead),
) -> JSONResponse:
    """Comment on a task, or approve it.

    Approval is restricted to team leads - see the service.
    """
    from archihub.api.users.services import has_role

    is_team_lead = has_role(current_user.username, "team_lead") or has_role(
        current_user.username, "admin"
    )
    return _respond(services.update_task(task_id, body, current_user.username, is_team_lead))


# Declared last: a literal path must not be captured by the parameterised one.
@router.get(
    "/{resource_id}",
    responses={200: {"description": "The open task for that resource"}, 404: {"description": "No task"}, **_ROLE_RESPONSES},
)
def get_resource_tasks(
    resource_id: str,
    current_user: CurrentUser = Depends(require_lead),
) -> JSONResponse:
    """The open review task for one resource."""
    return _respond(services.get_resource_tasks(resource_id))
