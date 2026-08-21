"""Audit log.

``register_log`` is called from ordinary service code at the point where the
business logic has decided an action succeeded - NOT from middleware. That is
deliberate: an audit entry needs to know which business action occurred and with
what metadata (including a field-level diff for resource edits), and middleware
only ever sees "a request happened". Inferring one from the other would either
duplicate the domain logic or record something less useful.

METADATA IS FILTERED BEFORE IT IS STORED. ``_build_details`` reduces each
action's metadata to the fields worth keeping, and in the case of article edits
strips the article body - which can be megabytes of HTML and would otherwise be
duplicated into the audit collection on every save.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from bson import json_util

from archihub.core.i18n import gettext as _
from archihub.core.log_actions import log_actions

logger = logging.getLogger(__name__)

COLLECTION = "logs"
PAGE_SIZE = 20

# Fields a client may filter the audit log by. As everywhere else, a
# client-supplied filter document is reduced to string equality on an allowlist
# before it reaches a query.
ALLOWED_LOG_FILTER_FIELDS = frozenset({"username", "action"})


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def parse_result(result):
    return json.loads(json_util.dumps(result))


def get_current_date() -> datetime:
    return datetime.now()


def sanitize_log_filters(filters) -> dict:
    if not isinstance(filters, dict):
        return {}
    return {
        key: value
        for key, value in filters.items()
        if key in ALLOWED_LOG_FILTER_FIELDS and isinstance(value, str)
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def normalize_action(action: str) -> str:
    """Accept either the action key or its stored value.

    ``log_actions`` maps a lowercase key (``'list_create'``) to the stored value
    (``'LIST_CREATE'``). Legacy call sites pass ``log_actions['list_create']``;
    ported ones pass the key directly, which reads better. Both must end up
    storing the same value - the audit filter vocabulary comes from this same
    mapping, so an entry stored under a key would be invisible to every filter.
    """
    return log_actions.get(action, action)


def register_log(username: str | None, action: str, metadata: dict | None = None) -> None:
    """Record one audited action.

    Never raises. An audit write that fails must not fail the operation it is
    describing - the work already happened, and turning a successful edit into
    an error because its log entry could not be written would be worse than the
    missing entry. Failures are logged operationally instead.
    """
    try:
        _mongo().insert_record(
            COLLECTION,
            {
                "username": username or "system",
                "action": normalize_action(action),
                "date": get_current_date(),
                "metadata": metadata or {},
            },
        )
    except Exception:
        logger.error("Could not write an audit entry for action %s", action, exc_info=True)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _extract_resource_ids(resources, fallback_ids=None) -> list:
    if isinstance(resources, list):
        return [r.get("id") if isinstance(r, dict) else r for r in resources]
    return fallback_ids or []


def build_details(action: str, metadata: dict | None) -> dict:
    """Reduce an entry's metadata to what is worth showing.

    Each action carries a different payload, and some carry far more than the
    audit view needs.
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    form = metadata.get("form") if isinstance(metadata.get("form"), dict) else {}

    if action in (log_actions["av_transcribe"], log_actions["img_analyze"]):
        return {
            "form": {"resources": _extract_resource_ids(form.get("resources"), metadata.get("ids"))},
            "prompt": form.get("prompt", metadata.get("prompt")),
            "parent": form.get("parent", metadata.get("parent")),
            "post_type": form.get("post_type", metadata.get("post_type")),
        }

    if action in (
        log_actions["form_create"],
        log_actions["form_delete"],
        log_actions["form_update"],
        log_actions["form_duplicate"],
    ):
        return {"form": {"name": form.get("name"), "slug": form.get("slug")}}

    if action == log_actions["search"]:
        filters = metadata.get("filters") if isinstance(metadata.get("filters"), dict) else {}
        return {
            "filters": {"keyword": filters.get("keyword")},
            "page": filters.get("page", metadata.get("page")),
        }

    if action == log_actions["resource_article_update"]:
        # The article body can be megabytes of HTML. Storing or returning it
        # here would duplicate the whole document into the audit view on every
        # save, for no diagnostic value.
        details = dict(metadata)
        details.pop("articleBody", None)

        resource = details.get("resource")
        if isinstance(resource, dict):
            resource = dict(resource)
            resource.pop("articleBody", None)

            resource_metadata = resource.get("metadata")
            if isinstance(resource_metadata, dict):
                resource_metadata = dict(resource_metadata)
                resource_metadata.pop("articleBody", None)
                resource["metadata"] = resource_metadata

            details["resource"] = resource

        return details

    return metadata


def normalize_log_details(logs: list[dict]) -> list[dict]:
    """Replace each entry's raw ``metadata`` with a presentable ``details``."""
    normalized = []
    for log in logs:
        entry = dict(log)
        metadata = entry.pop("metadata", {})
        entry["details"] = build_details(entry.get("action"), metadata)
        normalized.append(entry)
    return normalized


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def filter_logs(body: dict) -> tuple[list | dict, int]:
    """Paginated audit entries, newest first.

    Returns an empty list rather than a 404 when nothing matches. The legacy
    version tested ``if not logs`` on a pymongo **cursor**, which is always
    truthy, so its documented 404 could never fire - and "no entries match this
    filter" is a successful query with no results, not a missing resource.
    """
    try:
        filters = sanitize_log_filters(body.get("filters"))
        page = int(body.get("page") or 0)

        mongo = _mongo()
        logs = list(
            mongo.get_all_records(
                COLLECTION,
                filters,
                limit=PAGE_SIZE,
                skip=page * PAGE_SIZE,
                sort=[("date", -1)],
                fields={"_id": 0},
            )
        )
        total = mongo.count(COLLECTION, filters)

        logs = normalize_log_details(parse_result(logs))
        for entry in logs:
            entry["total"] = total

        return logs, 200
    except Exception as exc:
        logger.exception("Could not read the audit log")
        return {"msg": str(exc)}, 500


def get_log_actions() -> tuple[dict, int]:
    """The action vocabulary, for building filter dropdowns."""
    return dict(log_actions), 200


def get_logs_for_resource(body: dict, resource_id: str) -> tuple[list | dict, int]:
    """Change history for one resource, as field-level diffs."""
    try:
        page = int(body.get("page") or 0)
        logs = list(
            _mongo().get_all_records(
                COLLECTION,
                {"metadata.resource._id": resource_id},
                limit=PAGE_SIZE,
                skip=page * PAGE_SIZE,
                sort=[("date", -1)],
                fields={"_id": 0},
            )
        )
        return extract_changes(parse_result(logs)), 200
    except Exception as exc:
        logger.exception("Could not read the history for resource %s", resource_id)
        return {"msg": str(exc)}, 500


def compare_objects(old_obj, new_obj, path_prefix: str, date) -> list[dict]:
    """Field-level differences between two versions of a document."""
    changes: list[dict] = []

    if isinstance(old_obj, dict) and isinstance(new_obj, dict):
        for key in set(old_obj) | set(new_obj):
            path = f"{path_prefix}.{key}" if path_prefix else key
            changes.extend(compare_objects(old_obj.get(key), new_obj.get(key), path, date))
        return changes

    if old_obj != new_obj:
        changes.append({"field": path_prefix, "old": old_obj, "new": new_obj, "date": date})

    return changes


def extract_changes(logs: list[dict]) -> list[dict]:
    """Turn a series of create/update entries into a change history.

    Each entry is compared against the one before it, so the result reads as
    "what changed, when" rather than a list of full snapshots.
    """
    history: list[dict] = []
    ordered = sorted(logs, key=lambda entry: entry.get("date") or "")

    previous = None
    for entry in ordered:
        resource = (entry.get("metadata") or {}).get("resource")
        if not isinstance(resource, dict):
            continue

        if previous is not None:
            history.extend(
                compare_objects(previous, resource, "", entry.get("date"))
            )
        previous = resource

    history.reverse()  # newest first, matching the listing
    return history
