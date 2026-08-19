"""Search-index maintenance tasks.

Port of ``app/api/system/tasks/elasticTasks.py``. Three tasks, all long-running
and all triggered from the admin settings screen:

    system.regenerate_index         rebuild the index under a new mapping
    system.index_resources          (re)index resources into it
    system.index_resources_delete   drop one resource from it

The dotted names are stable identifiers: a message already queued in Redis, and
every row in the `tasks` collection, resolves by this string.

WHAT CHANGED, AND WHY IT HAD TO
-------------------------------

**A failed resource is counted as a failure.** Incrementing the counter before
the work and swallowing exceptions means a run in which every document was
rejected still finishes with "Indexing finished for 12000 resources" and no
error anywhere - the only symptom being an empty search. Failures are counted
separately and named in the result.

**A filtered run stays filtered.** Applying the caller's filter to the first page
and querying ``{}`` for the rest is correct only while the result fits in one
page - so it works for a single resource and silently walks the entire
collection for anything larger.

**Documents go in batches.** One HTTP round trip per resource is what a full
reindex of a real archive spends its time on. ``bulk_index`` sends a page at a
time and reports which ids the cluster refused.

**Types are resolved once.** ``get_by_slug`` and ``get_metadata`` were called
per resource, so a 12000-resource archive with 20 content types made 24000
lookups to answer 20 questions.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

#: Resources read - and indexed - per batch.
PAGE_SIZE = 1000

RESOURCES_INDEX = "resources"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _client():
    from archihub.infra.search import get_search

    return get_search()


@shared_task(ignore_result=False, name="system.regenerate_index")
def regenerate_index_task(mapping: dict, user: str | None = None) -> str:
    """Rebuild the resources index under ``mapping``.

    ``user`` is unused and kept only because the legacy task was called with it
    positionally; a message already in the queue at cutover carries two
    arguments.
    """
    from archihub.core.i18n import gettext as _

    name, created = _client().regenerate_index(RESOURCES_INDEX, mapping)
    if created:
        return _("Main index %(index)s created", index=name)
    return _("Main index %(index)s updated", index=name)


@shared_task(ignore_result=False, name="system.index_resources")
def index_resources_task(body: dict | None = None) -> str:
    """Index resources into the search index.

    With no body, the index is emptied first and the whole collection is
    rebuilt. With ``{"_id": ...}`` or any other Mongo filter, only the matching
    resources are reindexed and nothing is emptied.
    """
    from bson.objectid import ObjectId

    from archihub.core.i18n import gettext as _

    body = body or {}
    filters = {"_id": ObjectId(body["_id"])} if "_id" in body else dict(body)
    full_rebuild = not filters

    client = _client()
    if full_rebuild:
        # Only a full run clears the index. A filtered run that emptied it would
        # delete everything it was not asked to touch.
        client.delete_all_documents(RESOURCES_INDEX)

    indexed = 0
    skipped = 0
    failed = 0
    types = _TypeCache()

    for page in _pages(filters):
        documents = []
        record_types = _record_types(page)

        for resource in page:
            try:
                document = _document_for(resource, types, record_types)
            except _Skip:
                skipped += 1
                continue
            except Exception:
                logger.exception("Could not build an index document for %s", resource.get("_id"))
                failed += 1
                continue
            documents.append((str(resource["_id"]), document))

        try:
            failures = client.bulk_index(RESOURCES_INDEX, documents)
        except Exception:
            # The batch never reached the cluster. Every document in it is a
            # failure; the run continues so a transient problem on one page
            # does not discard the rest.
            logger.exception("Bulk write failed for a page of %d resources", len(documents))
            failed += len(documents)
            continue

        for doc_id, reason in failures:
            logger.warning("Elasticsearch rejected resource %s: %s", doc_id, reason)
        failed += len(failures)
        indexed += len(documents) - len(failures)

    message = _("Indexing finished for %(count)s resources", count=indexed)
    if failed or skipped:
        # Appended rather than replacing the message, so the string an operator
        # is used to seeing is still the first thing they read.
        message = f"{message} ({failed} failed, {skipped} skipped)"
        logger.warning("Indexing run finished with %d failures and %d skipped", failed, skipped)
    return message


@shared_task(ignore_result=False, name="system.index_resources_delete")
def index_resources_delete_task(body: dict | None = None) -> str:
    """Remove one resource from the search index."""
    from archihub.core.i18n import gettext as _

    resource_id = (body or {}).get("_id")
    if not resource_id:
        # The original subscripted body['_id'], so a caller that forgot it got a
        # KeyError recorded as a failed task with no message.
        raise ValueError("index_resources_delete requires an _id")

    result = _client().delete_document(RESOURCES_INDEX, str(resource_id))
    if result.get("result") not in ("deleted", "not_found"):
        raise RuntimeError(f"Unexpected delete result for {resource_id}: {result.get('result')!r}")

    return _("Resource %(id)s deleted from index", id=str(resource_id))


# ---------------------------------------------------------------------------
# Building one document
# ---------------------------------------------------------------------------


class _Skip(Exception):
    """This resource is deliberately not indexed."""


class _TypeCache:
    """Content-type metadata, resolved once per type rather than per resource."""

    def __init__(self) -> None:
        self._fields: dict[str, list[dict]] = {}
        self._is_article: dict[str, bool] = {}

    def for_type(self, slug: str) -> tuple[list[dict], bool]:
        if slug not in self._fields:
            from archihub.api.types import services as types_services

            post_type = types_services.get_by_slug(slug)
            metadata = types_services.get_metadata(slug)
            self._fields[slug] = (metadata or {}).get("fields") or []
            self._is_article[slug] = bool((post_type or {}).get("isArticle"))
        return self._fields[slug], self._is_article[slug]


def _document_for(resource: dict, types: _TypeCache, record_types: dict[str, str]) -> dict:
    from archihub.api.search.documents import (
        NotIndexable,
        build_resource_document,
        resolve_records,
    )
    from archihub.core.hooks import get_hook_handler

    post_type = resource.get("post_type")
    if not post_type:
        raise _Skip("resource has no content type")

    fields, is_article = types.for_type(post_type)

    try:
        return build_resource_document(
            resource,
            fields,
            is_article=is_article,
            records=resolve_records(resource, record_types),
            centroid_lookup=_centroid,
            hook_call=get_hook_handler().call,
        )
    except NotIndexable as exc:
        raise _Skip(str(exc)) from exc


def _centroid(ident: str, parent: str | None, level: int):
    from archihub.api.geosystem import services as geo_services

    return geo_services.get_shape_centroid(ident, parent, level)


def _pages(filters: dict):
    """Yield pages of resources matching ``filters``.

    Paginates by ``_id`` rather than by ``skip``. A ``skip``-based walk re-runs
    the query for every page and re-scans everything it has already returned, so
    the cost of a full reindex grows with the square of the collection; it is
    also unstable, because a document inserted or removed mid-run shifts the
    window and silently skips or repeats a resource.
    """
    from bson.objectid import ObjectId

    mongo = _mongo()
    last_id: ObjectId | None = None

    while True:
        query = dict(filters)
        if last_id is not None:
            existing = query.get("_id")
            clause = {"$gt": last_id}
            if existing is not None:
                # A caller-supplied _id filter is kept and combined, never
                # overwritten - otherwise page two would ignore it.
                query["$and"] = [{"_id": existing}, {"_id": clause}]
                query.pop("_id")
            else:
                query["_id"] = clause

        page = list(mongo.get_all_records("resources", query, sort=[("_id", 1)], limit=PAGE_SIZE))
        if not page:
            return

        yield page

        if len(page) < PAGE_SIZE:
            return
        last_id = page[-1]["_id"]


def _record_types(resources: list[dict]) -> dict[str, str]:
    """``{record id: fileProcessing type}`` for every file on this page.

    One query per page instead of one per resource.
    """
    from bson.objectid import ObjectId

    ids: set[str] = set()
    for resource in resources:
        for entry in resource.get("filesObj") or []:
            if isinstance(entry, dict) and entry.get("id"):
                ids.add(str(entry["id"]))
    if not ids:
        return {}

    object_ids = []
    for value in ids:
        try:
            object_ids.append(ObjectId(value))
        except Exception:
            logger.warning("Skipping unusable record id %r on a resource", value)

    if not object_ids:
        return {}

    rows = _mongo().get_all_records(
        "records",
        {"_id": {"$in": object_ids}},
        fields={"_id": 1, "processing.fileProcessing.type": 1},
    )

    resolved: dict[str, str] = {}
    for row in rows:
        kind = ((row.get("processing") or {}).get("fileProcessing") or {}).get("type")
        if kind:
            resolved[str(row["_id"])] = kind
    return resolved
