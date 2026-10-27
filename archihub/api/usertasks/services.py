"""Editorial review tasks.

A user task assigns review work on one resource or record to one editor. Exactly
one may be open per target at a time, which is what stops two editors being
asked to review the same thing.

ERROR KEY: this domain answers with ``{"error": ...}`` rather than the
``{"msg": ...}`` every other domain uses - **except** the two "there are no
tasks" 404s, which use ``msg``. That inconsistency is wire contract: clients read
one key or the other per route. Do not tidy it without a paired frontend change.
"""

from __future__ import annotations

import logging

from bson.objectid import ObjectId

from archihub.core.clock import utcnow
from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "usertasks"
PAGE_SIZE = 10

STATUS_PENDING = "pending"
STATUS_REVIEW = "review"
STATUS_REJECTED = "rejected"
STATUS_APPROVED = "approved"

VALID_STATUSES = {STATUS_PENDING, STATUS_REVIEW, STATUS_REJECTED, STATUS_APPROVED}
OPEN_STATUSES = [STATUS_PENDING, STATUS_REVIEW, STATUS_REJECTED]

_LIST_PROJECTION = {
    "user": 1,
    "createdBy": 1,
    "status": 1,
    "createdAt": 1,
    "resourceId": 1,
    "recordId": 1,
}


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _iso(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else value


def _to_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _audit(user: str | None, action: str, details: dict) -> None:
    try:
        from archihub.api.logs.services import register_log

        register_log(user, action, details)
    except Exception:
        logger.exception("Could not record audit log for %s", action)


def _call_hook(name: str, payload: dict) -> None:
    try:
        from archihub.core.hooks import get_hook_handler

        get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("Hook %s failed", name)


def process_comments(comments) -> str:
    """Flatten a task's comment thread into readable text."""
    if isinstance(comments, str):
        return comments
    lines = []
    for comment in comments or []:
        if isinstance(comment, dict):
            lines.append(f"{comment.get('user', '')}: {comment.get('comment', '')}")
        else:
            lines.append(str(comment))
    return "\n---------------\n".join(lines)


#: The roles review work can be assigned to; ``get_editors`` lists them.
ASSIGNABLE_ROLES = ["editor", "transcriber"]


def may_open_target(target_field: str, target_value: str, user: str) -> bool:
    """Whether ``user`` may open the resource or record a task is about.

    The resources and records routes' own rules; a target that does not exist
    cannot be opened.
    """
    if target_field == "recordId":
        from archihub.api.records.services import load_visible

        _record, error = load_visible(target_value, user)
        return error is None

    from archihub.api.resources.access import may_view_resource
    from archihub.api.resources.services import can_view_type
    from archihub.api.users.services import has_role

    object_id = _to_object_id(target_value)
    if object_id is None:
        return False
    resource = _mongo().get_record(
        "resources",
        {"_id": object_id},
        fields={"post_type": 1, "status": 1, "accessRights": 1, "parents": 1},
    )
    if not resource:
        return False
    is_admin = has_role(user, "admin")
    if resource.get("status") == "deleted" and not is_admin:
        return False
    return may_view_resource(user, resource, is_admin) and can_view_type(
        user, resource.get("post_type")
    )


def _is_assignable(username: str) -> bool:
    if not username:
        return False
    return bool(
        _mongo().get_record(
            "users", {"username": username, "roles": {"$in": ASSIGNABLE_ROLES}}, fields={"_id": 1}
        )
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_resource_tasks(resource_id: str, user: str) -> tuple[dict, int]:
    return _open_task("resourceId", resource_id, user, _("There are no tasks for this resource"))


def get_record_tasks(record_id: str, user: str) -> tuple[dict, int]:
    return _open_task("recordId", record_id, user, _("There are no tasks for this record"))


def _open_task(target_field: str, target_value: str, user: str, missing: str) -> tuple[dict, int]:
    """The open task on a target, for someone taking part in it or managing tasks.

    Anyone else - and anyone who may not open the target - is told there is no
    task, as when there is none.
    """
    task = _mongo().get_record(
        COLLECTION,
        {target_field: target_value, "status": {"$in": OPEN_STATUSES}},
    )
    if not task or not _may_read_task(task, user) or not may_open_target(
        target_field, target_value, user
    ):
        # `msg`, not `error` - see the module docstring.
        return {"msg": missing}, 404

    task["_id"] = str(task["_id"])
    task["createdAt"] = _iso(task.get("createdAt"))
    task["comment"] = process_comments(task.get("comment"))
    return task, 200


def _may_read_task(task: dict, user: str) -> bool:
    """The assignee, the creator, a commenter, or a task manager."""
    from archihub.api.users.services import has_permission, has_role

    participants = {task.get("user"), task.get("createdBy")}
    participants.update(
        c.get("user") for c in task.get("comment") or [] if isinstance(c, dict)
    )
    if user in participants:
        return True
    return (
        has_role(user, "admin")
        or has_role(user, "team_lead")
        or has_permission(user, "usertasks.manage")
    )


def get_all_tasks(filters: dict) -> tuple[dict, int]:
    """Paginated task list, newest first."""
    try:
        statuses = filters.get("status") or []
        if isinstance(statuses, str):
            statuses = [statuses]

        query: dict = {"status": {"$in": statuses}}
        username = filters.get("user")
        if username:
            if filters.get("include_created"):
                query["$or"] = [
                    {"user": username},
                    {"createdBy": username},
                    {"comment.0.user": username},
                ]
            else:
                query["user"] = username
        else:
            query["user"] = {"$exists": True}

        page = int(filters.get("page") or 0)
        mongo = _mongo()

        tasks = list(
            mongo.get_all_records(
                COLLECTION, query, sort=[("createdAt", -1)],
                limit=PAGE_SIZE, skip=page * PAGE_SIZE, fields=_LIST_PROJECTION,
            )
        )

        for task in tasks:
            task["_id"] = str(task["_id"])
            task["createdAt"] = _iso(task.get("createdAt"))
            if task.get("recordId"):
                task["recordId"] = str(task["recordId"])
            if task.get("resourceId"):
                task["resourceType"] = _resource_type(task["resourceId"])

        total = mongo.count(COLLECTION, query) or 0
        return {
            "results": tasks or [],
            "total": total,
        }, 200
    except Exception as exc:
        logger.exception("Could not list review tasks")
        return {"error": _("Error while processing the request")}, 500


def get_editors() -> tuple[list | dict, int]:
    """Users who can be assigned review work, as select options.

    ``{"label": name, "value": username}``, which is the shape the picker in
    `CustomAlert.tsx` is built for. Returning ``{"name", "username"}`` instead
    looks harmless and is not: `normalizeSelectOption` falls back through
    ``value ?? id ?? term ?? name``, so it would submit the display *name* as
    the assignee where the assignment needs the username. Identical for an
    account whose name is its username, wrong for every other one.
    """
    try:
        editors = list(
            _mongo().get_all_records(
                "users",
                {"roles": {"$in": ASSIGNABLE_ROLES}},
                fields={"name": 1, "username": 1, "_id": 0},
            )
        )
        options = [
            {"label": editor.get("name", editor.get("username")), "value": editor.get("username")}
            for editor in editors
        ]
        return options, 200
    except Exception:
        logger.exception("Could not list editors")
        return {"error": _("Error while processing the request")}, 500


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create_task(body: dict, user: str) -> tuple[dict, int]:
    """Assign review work.

    Refuses when the target already has an open task: two editors asked to
    review the same thing would produce conflicting outcomes.
    """
    try:
        resource_id = (body.get("resourceId") or "").strip()
        record_id = (body.get("recordId") or "").strip()

        if not resource_id and not record_id:
            return {"error": _("resourceId or recordId is required")}, 400
        if not (body.get("user") or "").strip():
            return {"error": _("user is required")}, 400
        if not (body.get("comment") or "").strip():
            return {"error": _("comment is required")}, 400

        mongo = _mongo()
        target_field = "resourceId" if resource_id else "recordId"
        target_value = resource_id or record_id

        if not may_open_target(target_field, target_value, user):
            return {"error": _("Resource does not exist")}, 404
        if not _is_assignable(body["user"].strip()):
            return {"error": _("user is required")}, 400

        if mongo.get_record(COLLECTION, {target_field: target_value, "status": {"$in": OPEN_STATUSES}}):
            message = (
                _("There is already a task for this resource")
                if resource_id
                else _("There is already a task for this record")
            )
            return {"error": message}, 400

        now = utcnow()
        record = {
            target_field: target_value,
            "user": body["user"].strip(),
            "createdBy": user,
            "status": STATUS_PENDING,
            "createdAt": now,
            "comment": [{"user": user, "comment": body["comment"], "createdAt": now}],
        }
        result = mongo.insert_record(COLLECTION, record)

        created = dict(record)
        created["_id"] = str(result.inserted_id)
        created["createdAt"] = _iso(now)
        for comment in created["comment"]:
            comment["createdAt"] = _iso(comment["createdAt"])

        target_name = "resource" if resource_id else "record"
        _audit(user, "usertask_create", {
            target_name: target_value,
            "task": created["_id"],
            "assignee": body["user"],
        })
        _call_hook("usertask_create", created)

        return created, 201
    except Exception:
        logger.exception("Could not create a review task")
        return {"error": _("Error while processing the request")}, 500


def update_task(task_id: str, body: dict, user: str, is_team_lead: bool) -> tuple[dict, int]:
    """Comment on a task, or update its status.

    ONLY A TEAM LEAD (OR ADMIN) MAY APPROVE OR REJECT. An editor can update their
    own task and advance it to 'review', but cannot sign off their own work.
    """
    object_id = _to_object_id(task_id)
    if object_id is None:
        return {"error": _("Task not found")}, 404

    try:
        mongo = _mongo()
        task = mongo.get_record(COLLECTION, {"_id": object_id})
        if not task:
            return {"error": _("Task not found")}, 404

        if task.get("status") == STATUS_APPROVED:
            return {"error": _("Task already approved")}, 400

        creator = task.get("createdBy") or (
            task.get("comment") and isinstance(task["comment"], list) and task["comment"][0].get("user")
        )
        has_owner = bool(task.get("user") or creator)
        is_owner = (task.get("user") == user) or (creator == user)
        if not is_team_lead and has_owner and not is_owner:
            return {"error": _("You don't have the required authorization")}, ROLE_FAILURE_STATUS

        update: dict = {}

        comment = (body.get("comment") or "").strip()
        if comment:
            update["comment"] = (task.get("comment") or []) + [
                {"user": user, "comment": comment, "createdAt": utcnow()}
            ]

        new_status = body.get("status")
        if new_status:
            if new_status not in VALID_STATUSES:
                return {"error": _("Invalid status")}, 400

            if new_status == STATUS_APPROVED:
                if not is_team_lead:
                    return {"error": _("You don't have the required authorization")}, ROLE_FAILURE_STATUS
                update["status"] = STATUS_APPROVED
                update["approvedBy"] = user
                update["approvedAt"] = utcnow()
            elif new_status == STATUS_REJECTED:
                if not is_team_lead:
                    return {"error": _("You don't have the required authorization")}, ROLE_FAILURE_STATUS
                update["status"] = STATUS_REJECTED
            elif new_status == STATUS_REVIEW:
                update["status"] = STATUS_REVIEW
            elif new_status == STATUS_PENDING:
                if is_team_lead:
                    update["status"] = STATUS_PENDING

        if not update:
            return {"error": _("No changes were made")}, 400

        mongo.update_record(COLLECTION, {"_id": object_id}, update)

        updated = mongo.get_record(COLLECTION, {"_id": object_id}) or {}
        updated["_id"] = str(updated.get("_id"))
        updated["createdAt"] = _iso(updated.get("createdAt"))
        updated["approvedAt"] = _iso(updated.get("approvedAt"))
        for entry in updated.get("comment") or []:
            if isinstance(entry, dict):
                entry["createdAt"] = _iso(entry.get("createdAt"))

        target = {"resource": task["resourceId"]} if task.get("resourceId") else {"record": task.get("recordId")}
        audit_details = {
            **target,
            "task": task_id,
            "status": updated.get("status"),
            "approved": update.get("status") == STATUS_APPROVED,
            "commented": "comment" in update,
        }

        _audit(user, "usertask_update", audit_details)
        _call_hook("usertask_update", updated)

        return updated, 200
    except Exception:
        logger.exception("Could not update review task %s", task_id)
        return {"error": _("Error while processing the request")}, 500


def _resource_type(resource_id: str):
    """Content type of the resource a task targets, when resources is available."""
    try:
        from archihub.api.resources.services import get_resource_type

        return get_resource_type(resource_id)
    except Exception:
        logger.debug("Could not resolve content type for %s", resource_id)
        return None

