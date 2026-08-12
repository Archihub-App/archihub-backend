"""Editorial review tasks.

Port of ``app/api/usertasks/services.py``.

A user task assigns review work on one resource or record to one editor. Exactly
one may be open per target at a time, which is what stops two editors being
asked to review the same thing.

ERROR KEY: this domain mostly answers with ``{"error": ...}`` rather than the
``{"msg": ...}`` every other domain uses - **except** the two "there are no
tasks" 404s, which the legacy service returns under ``msg``. So the legacy is
inconsistent with itself, not merely with its neighbours.

That is preserved exactly, both parts. Unifying the two would be a wire change
with no behavioural benefit, and the harness diffs the body key by key, so the
inconsistency is easier to keep than to notice. Do not tidy it without a paired
frontend change.
"""

from __future__ import annotations

import logging
from datetime import datetime

from bson.objectid import ObjectId

from archihub.core.i18n import gettext as _
from archihub.core.security.jwt import LEGACY_ROLE_FAILURE_STATUS

logger = logging.getLogger(__name__)

COLLECTION = "usertasks"
PAGE_SIZE = 10

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"

_LIST_PROJECTION = {"user": 1, "status": 1, "createdAt": 1, "resourceId": 1, "recordId": 1}


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


def process_comments(comments) -> str:
    """Flatten a task's comment thread into readable text."""
    lines = []
    for comment in comments or []:
        if isinstance(comment, dict):
            lines.append(f"{comment.get('user', '')}: {comment.get('comment', '')}")
        else:
            lines.append(str(comment))
    return "\n---------------\n".join(lines)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_resource_tasks(resource_id: str) -> tuple[dict, int]:
    task = _mongo().get_record(COLLECTION, {"resourceId": resource_id, "status": STATUS_PENDING})
    if not task:
        # `msg`, not `error`: the legacy service uses `msg` for exactly
        # these two and `error` for everything else in this module.
        return {"msg": _("There are no tasks for this resource")}, 404

    task["_id"] = str(task["_id"])
    task["createdAt"] = _iso(task.get("createdAt"))
    return task, 200


def get_record_tasks(record_id: str) -> tuple[dict, int]:
    task = _mongo().get_record(COLLECTION, {"recordId": record_id, "status": STATUS_PENDING})
    if not task:
        # `msg`, not `error`: the legacy service uses `msg` for exactly
        # these two and `error` for everything else in this module.
        return {"msg": _("There are no tasks for this record")}, 404

    task["_id"] = str(task["_id"])
    task["createdAt"] = _iso(task.get("createdAt"))
    return task, 200


def get_all_tasks(filters: dict) -> tuple[dict, int]:
    """Paginated task list, newest first."""
    try:
        statuses = filters.get("status") or []
        if isinstance(statuses, str):
            statuses = [statuses]

        query: dict = {"status": {"$in": statuses}}
        # An absent user means "any", expressed as a presence check rather than
        # an omitted key so the shape of the query stays constant.
        query["user"] = filters["user"] if filters.get("user") else {"$exists": True}

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

        return {"results": tasks, "total": mongo.count(COLLECTION, query)}, 200
    except Exception as exc:
        logger.exception("Could not list review tasks")
        return {"error": str(exc)}, 500


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
                {"roles": {"$in": ["editor", "transcriber"]}},
                fields={"name": 1, "username": 1, "_id": 0},
            )
        )
        options = [
            {"label": editor.get("name", editor.get("username")), "value": editor.get("username")}
            for editor in editors
        ]
        return options, 200
    except Exception as exc:
        logger.exception("Could not list editors")
        return {"error": str(exc)}, 500


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

        if mongo.get_record(COLLECTION, {target_field: target_value, "status": STATUS_PENDING}):
            message = (
                _("There is already a task for this resource")
                if resource_id
                else _("There is already a task for this record")
            )
            return {"error": message}, 400

        now = datetime.now()
        record = {
            target_field: target_value,
            "user": body["user"],
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

        return created, 201
    except Exception as exc:
        logger.exception("Could not create a review task")
        return {"error": str(exc)}, 500


def update_task(task_id: str, body: dict, user: str, is_team_lead: bool) -> tuple[dict, int]:
    """Comment on a task, and optionally approve it.

    ONLY A TEAM LEAD MAY APPROVE. An editor can add comments to their own task
    but cannot sign off their own work - which is the point of the review step.
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

        update: dict = {}

        comment = (body.get("comment") or "").strip()
        if comment:
            update["comment"] = (task.get("comment") or []) + [
                {"user": user, "comment": comment, "createdAt": datetime.now()}
            ]

        if body.get("status") == STATUS_APPROVED:
            if not is_team_lead:
                return {"error": _("You don't have the required authorization")}, LEGACY_ROLE_FAILURE_STATUS
            update["status"] = STATUS_APPROVED
            update["approvedBy"] = user
            update["approvedAt"] = datetime.now()

        if not update:
            return {"error": _("No changes were made")}, 400

        mongo.update_record(COLLECTION, {"_id": object_id}, update)

        updated = mongo.get_record(COLLECTION, {"_id": object_id}) or {}
        updated["_id"] = str(updated.get("_id"))
        updated["createdAt"] = _iso(updated.get("createdAt"))
        updated["approvedAt"] = _iso(updated.get("approvedAt"))
        for entry in updated.get("comment") or []:
            entry["createdAt"] = _iso(entry.get("createdAt"))

        return updated, 200
    except Exception as exc:
        logger.exception("Could not update review task %s", task_id)
        return {"error": str(exc)}, 500


def _resource_type(resource_id: str):
    """Content type of the resource a task targets, when resources is available."""
    try:
        from archihub.api.resources.services import get_resource_type

        return get_resource_type(resource_id)
    except ImportError:
        logger.debug("resources domain not ported yet; resourceType omitted")
        return None
