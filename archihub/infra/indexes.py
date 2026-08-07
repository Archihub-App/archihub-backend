"""MongoDB index definitions.

WHY THIS FILE EXISTS

Before this, the database had **no indexes at all** beyond the `_id` one MongoDB
creates automatically - verified against a live instance, on every collection.
Every lookup that was not by `_id` was a full collection scan.

That is invisible on a development instance holding a handful of documents, and
it is the dominant cost on a real archive. The queries it affects are not
occasional ones; they are on the hot path of essentially every request:

  users   by `username`  - resolved several times per authenticated request
                           (get_by_username, has_role, has_right)
  system  by `name`      - settings lookups on most requests, plus every beat tick
  tasks   by `taskId`    - polled continuously while any background job runs
  resources / records    - every catalogue listing and detail view

Every definition below is derived from filters and sorts that actually appear in
the codebase (counted across all service modules), not from guesswork. The
comment on each says which access pattern it serves.

DESIGN NOTES

* Compound index field order follows the ESR rule - Equality, then Sort, then
  Range. A compound index also serves queries on any prefix of its fields, which
  is why, for example, no separate single-field index on `resources.post_type`
  is needed alongside the compound one that starts with it.
* Everything is created with `background=True`. On a large existing collection a
  foreground build holds a database-level write lock for the duration, which on a
  production archive means an outage. This matters because these indexes are
  being added to instances that already hold data.
* `create_index` is idempotent: creating an index that already exists with the
  same specification is a no-op, so this is safe to run on every startup and
  safe to re-run.
* Uniqueness is asserted in exactly one place (`users.username`). Adding a unique
  index to a collection that already contains duplicates FAILS, so that one is
  applied defensively - see `ensure_indexes`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pymongo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    keys: list[tuple[str, int]]
    name: str
    unique: bool = False
    sparse: bool = False
    reason: str = ""
    # Set when a unique index could fail on pre-existing duplicate data, so the
    # failure is reported as actionable rather than crashing startup.
    tolerate_failure: bool = field(default=False)


# Ascending/descending shorthands
ASC = pymongo.ASCENDING
DESC = pymongo.DESCENDING


INDEXES: list[IndexSpec] = [
    # ---------------------------------------------------------------- users
    IndexSpec(
        collection="users",
        keys=[("username", ASC)],
        name="ix_users_username",
        unique=True,
        tolerate_failure=True,  # pre-existing duplicates would abort the build
        reason=(
            "Resolved on every authenticated request, several times over "
            "(get_by_username, has_role, has_right). 25+ query sites. Unique "
            "because usernames are the login identity."
        ),
    ),
    IndexSpec(
        collection="users",
        keys=[("roles", ASC)],
        name="ix_users_roles",
        reason="Multikey index for role lookups such as {'roles': {'$in': [...]}} (usertasks editors).",
    ),
    # --------------------------------------------------------------- system
    IndexSpec(
        collection="system",
        keys=[("name", ASC)],
        name="ix_system_name",
        unique=True,
        tolerate_failure=True,
        reason=(
            "Settings documents are addressed exclusively by name "
            "(active_plugins, index_management, user_management, access_rights, "
            "runtime_control). Read on most requests and on every beat tick."
        ),
    ),
    # ---------------------------------------------------------------- tasks
    IndexSpec(
        collection="tasks",
        keys=[("taskId", ASC)],
        name="ix_tasks_taskId",
        reason="Task status reconciliation polls by taskId continuously while a job runs.",
    ),
    IndexSpec(
        collection="tasks",
        keys=[("user", ASC), ("date", DESC)],
        name="ix_tasks_user_date",
        reason=(
            "The task list is 'my tasks, newest first' - equality on user, sort "
            "on date. Serves user-only queries too, being a prefix."
        ),
    ),
    IndexSpec(
        collection="tasks",
        keys=[("user", ASC), ("name", ASC), ("status", ASC)],
        name="ix_tasks_user_name_status",
        reason="has_task() looks for a pending task by user + task name (duplicate-job guard).",
    ),
    # ------------------------------------------------------------ resources
    IndexSpec(
        collection="resources",
        keys=[("post_type", ASC), ("status", ASC), ("metadata.firstLevel.title", ASC)],
        name="ix_resources_type_status_title",
        reason=(
            "The main catalogue listing: filter by content type and status, sort "
            "by title. post_type appears in 56 filters and status in 34; the "
            "title sort appears in 10. Prefix also serves post_type-only queries."
        ),
    ),
    IndexSpec(
        collection="resources",
        keys=[("parent.id", ASC)],
        name="ix_resources_parent_id",
        reason="Hierarchy traversal - fetching the children of a resource.",
    ),
    IndexSpec(
        collection="resources",
        keys=[("accessRights", ASC), ("status", ASC)],
        name="ix_resources_accessRights_status",
        reason="Access-rights filtering on public listings (14 filter sites).",
    ),
    IndexSpec(
        collection="resources",
        keys=[("ident", ASC)],
        name="ix_resources_ident",
        sparse=True,
        reason="Lookup by external identifier; sparse because not every resource has one.",
    ),
    IndexSpec(
        collection="resources",
        keys=[("favCount", DESC)],
        name="ix_resources_favCount",
        reason="'Most favourited' ordering.",
    ),
    # -------------------------------------------------------------- records
    IndexSpec(
        collection="records",
        keys=[("parent.id", ASC)],
        name="ix_records_parent_id",
        reason=(
            "Every resource detail view fetches its records this way. One of the "
            "highest-traffic queries in the application."
        ),
    ),
    IndexSpec(
        collection="records",
        keys=[("processing.fileProcessing.type", ASC)],
        name="ix_records_processing_type",
        sparse=True,
        reason="Filtering records by processed file type (image/audio/video) for galleries.",
    ),
    IndexSpec(
        collection="records",
        keys=[("hash", ASC)],
        name="ix_records_hash",
        sparse=True,
        reason="Duplicate detection on upload.",
    ),
    # ----------------------------------------------------------------- logs
    IndexSpec(
        collection="logs",
        keys=[("date", DESC)],
        name="ix_logs_date",
        reason="The audit log is always read newest-first.",
    ),
    IndexSpec(
        collection="logs",
        keys=[("user", ASC), ("date", DESC)],
        name="ix_logs_user_date",
        reason="Per-user audit history, newest first.",
    ),
    IndexSpec(
        collection="logs",
        keys=[("action", ASC), ("date", DESC)],
        name="ix_logs_action_date",
        reason="Filtering the audit log by action type, newest first.",
    ),
    IndexSpec(
        collection="logs",
        keys=[("resource", ASC), ("date", DESC)],
        name="ix_logs_resource_date",
        sparse=True,
        reason="Change history for one resource - drives the field-level diff view.",
    ),
    # ---------------------------------------------------------------- snaps
    IndexSpec(
        collection="snaps",
        keys=[("user", ASC), ("type", ASC), ("createdAt", DESC)],
        name="ix_snaps_user_type_created",
        reason="A user's snaps of a given type, newest first - exactly the listing query.",
    ),
    # ------------------------------------------ lookup / definition tables
    # Small collections, but read constantly and always by slug.
    IndexSpec(
        collection="post_types",
        keys=[("slug", ASC)],
        name="ix_post_types_slug",
        unique=True,
        tolerate_failure=True,
        reason="Content types are addressed by slug throughout (11+ sites).",
    ),
    IndexSpec(
        collection="forms",
        keys=[("slug", ASC)],
        name="ix_forms_slug",
        unique=True,
        tolerate_failure=True,
        reason="Form definitions are addressed by slug; resolved when rendering any form.",
    ),
    IndexSpec(
        collection="lists",
        keys=[("slug", ASC)],
        name="ix_lists_slug",
        # NOT unique, and sparse. Verified against a live instance: `lists`
        # documents do not carry a `slug` field at all - they are addressed by
        # `_id` everywhere except one function. A unique index would treat every
        # document's missing slug as the same null value and fail to build;
        # sparse indexes only the documents that actually have the field.
        # (The lone slug-based lookup, lists.get_by_slug, cannot currently match
        # anything - noted separately as a correctness issue.)
        sparse=True,
        reason="The single get_by_slug lookup; sparse because most lists have no slug.",
    ),
    IndexSpec(
        collection="options",
        keys=[("term", ASC)],
        name="ix_options_term",
        reason="List option lookup by term.",
    ),
]


def ensure_indexes(mongo=None, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Create every declared index. Idempotent and safe to call at startup.

    Returns ``{"created": [...], "existing": [...], "failed": [...]}``.

    Never raises: a missing index degrades performance, whereas refusing to
    start denies service entirely. Failures are logged at ERROR so they are
    visible without being fatal.
    """
    if mongo is None:
        from archihub.infra.mongo import get_mongo

        mongo = get_mongo()

    result: dict[str, list[str]] = {"created": [], "existing": [], "failed": []}

    for spec in INDEXES:
        label = f"{spec.collection}.{spec.name}"

        try:
            existing = {idx["name"] for idx in mongo.db[spec.collection].list_indexes()}
        except Exception:
            logger.warning("Could not list indexes on %s", spec.collection, exc_info=True)
            existing = set()

        if spec.name in existing:
            result["existing"].append(label)
            continue

        if dry_run:
            result["created"].append(label)
            continue

        try:
            mongo.db[spec.collection].create_index(
                spec.keys,
                name=spec.name,
                unique=spec.unique,
                sparse=spec.sparse,
                # Never hold a write lock on a populated collection.
                background=True,
            )
            result["created"].append(label)
            logger.info("Created index %s (%s)", label, spec.reason)
        except pymongo.errors.DuplicateKeyError:
            # A unique index over data that already contains duplicates. This is
            # a data problem for an operator to resolve, not a reason to fail
            # startup - and the message must say which collection and field.
            result["failed"].append(label)
            logger.error(
                "Could not create UNIQUE index %s: the collection already contains "
                "duplicate values for %s. Resolve the duplicates, then restart. "
                "Until then this lookup remains unindexed.",
                label,
                [key for key, _ in spec.keys],
            )
        except Exception:
            result["failed"].append(label)
            if spec.tolerate_failure:
                logger.error("Could not create index %s", label, exc_info=True)
            else:
                logger.warning("Could not create index %s", label, exc_info=True)

    logger.info(
        "Index check complete: %d created, %d already present, %d failed",
        len(result["created"]),
        len(result["existing"]),
        len(result["failed"]),
    )
    return result
