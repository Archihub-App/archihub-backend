"""Geometry-index maintenance tasks.

Port of the two ``@shared_task`` functions at the end of
``app/api/geosystem/services.py``:

    geosystem.regenerate_index_shapes   rebuild the shapes index
    geosystem.index_shapes              (re)index the stored boundaries

These back the explore map's boundary layer. Both are admin-triggered from the
system settings screen and both are long-running: an administrative-boundary set
is tens of thousands of polygons.

TWO DEFECTS FIXED HERE, both invisible from outside
---------------------------------------------------

**THE INDEX NAME MUST CARRY THE INSTANCE PREFIX, on the clear as well as the
write.** Writing to ``<prefix>-shapes`` while clearing ``shapes`` targets an
index that exists on no real instance: Elasticsearch answers 404, the return
value is discarded, and the clear silently does nothing - so every rerun adds a
second copy of every boundary instead of replacing the first. This is why
``resolve_index`` is the only way to name an index.

**Write failures were discarded.** ``index_document``'s response was not looked
at, so a rejected mapping produced a partially-filled index and a message saying
indexing had finished.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

#: Shapes are large documents - a boundary polygon can carry thousands of
#: coordinate pairs - so the page is much smaller than the resources one.
PAGE_SIZE = 100

SHAPES_INDEX = "shapes"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _client():
    from archihub.infra.search import get_search

    return get_search()


@shared_task(ignore_result=False, name="geosystem.regenerate_index_shapes")
def regenerate_index_shapes() -> str:
    """Rebuild the shapes index under its fixed mapping."""
    from archihub.api.search.mapping import SHAPES_MAPPING
    from archihub.core.i18n import gettext as _

    name, created = _client().regenerate_index(SHAPES_INDEX, SHAPES_MAPPING)
    if created:
        return _("Main index %(index)s created", index=name)
    return _("Main index %(index)s updated", index=name)


@shared_task(ignore_result=False, name="geosystem.index_shapes")
def index_shapes(body: dict | None = None) -> str:
    """Index the stored boundary shapes.

    ``body`` is accepted because the legacy signature took it and a queued
    message may carry one, but it has never selected anything: the original
    built ``filters = {}`` and then never read ``body`` again except to decide
    whether to empty the index first. That is preserved - an empty body means a
    full rebuild - rather than quietly giving the parameter a new meaning that
    an existing caller would not expect.
    """
    from archihub.core.i18n import gettext as _

    client = _client()
    if not body:
        client.delete_all_documents(SHAPES_INDEX)

    indexed = 0
    failed = 0

    for page in _pages():
        documents = [(str(shape["_id"]), _shape_document(shape)) for shape in page]
        try:
            failures = client.bulk_index(SHAPES_INDEX, documents)
        except Exception:
            logger.exception("Bulk write failed for a page of %d shapes", len(documents))
            failed += len(documents)
            continue

        for doc_id, reason in failures:
            logger.warning("Elasticsearch rejected shape %s: %s", doc_id, reason)
        failed += len(failures)
        indexed += len(documents) - len(failures)

    message = _("Indexing finished for %(count)s resources", count=indexed)
    if failed:
        message = f"{message} ({failed} failed)"
        logger.warning("Shape indexing finished with %d failures", failed)
    return message


def _shape_document(shape: dict) -> dict:
    """The indexed form of one stored boundary.

    An allowlist, not a copy: the stored document also carries the simplified
    geometry cache and other bookkeeping that the map never queries.
    """
    stored = shape.get("properties") or {}
    properties = {
        "admin_level": stored.get("admin_level"),
        "ident": stored.get("ident"),
        "name": stored.get("name"),
    }
    for optional in ("parent", "parent_name"):
        if optional in stored:
            properties[optional] = stored[optional]

    return {"geometry": shape.get("geometry"), "properties": properties}


def _pages():
    """Yield pages of stored shapes, paginated by ``_id``.

    Same reasoning as the resources indexer: a ``skip``-based walk re-scans
    everything already returned, and shifts under any concurrent write.
    """
    mongo = _mongo()
    last_id = None

    while True:
        query = {} if last_id is None else {"_id": {"$gt": last_id}}
        page = list(mongo.get_all_records("shapes", query, sort=[("_id", 1)], limit=PAGE_SIZE))
        if not page:
            return

        yield page

        if len(page) < PAGE_SIZE:
            return
        last_id = page[-1]["_id"]
