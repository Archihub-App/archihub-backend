"""Wiring the search index into the resource write path.

Port of ``hookHandlerIndex()`` in ``app/api/system/services.py``, which
``create_app`` called whenever ``index_management.index_activation`` was on.

WHY THIS MODULE HAD TO EXIST. Without it the hook bus has no registrations at
all, so ``resource_create``/``resource_update``/``resource_delete`` fire into an
empty registry: every write returns 200, nothing is queued, and the search index
keeps answering with the state it had at the last manual reindex. There is no
error anywhere - a stale index looks exactly like a correct one until somebody
searches for something they just catalogued and does not find it.

QUEUE 101 IS LOAD-BEARING. Registrations run in ascending ``queue`` order, and
plugins register their automatic processing at the order an operator configured
(0 by default). Indexing sits above all of them deliberately, so a plugin that
rewrites a resource's metadata does so *before* the document is built. Moving
these numbers silently reorders every side effect on the write path.

TOGGLING INDEXING NEEDS A RESTART, and that is the legacy contract rather than
an oversight. Registration is a process-local fact, so a setting flipped in one
gunicorn worker could not reach the others (or the Celery workers) in any case;
this deployment applies such changes by restarting, which is what
``/system/restart`` is for. It is deliberately NOT the same question as
``search.services.indexing_enabled()``, which gates the *routes* and is read per
request precisely because a route's availability can be answered locally.

The legacy ``hookHandlerVector()`` beside it is not ported, because it is dead:
nothing calls it. Qdrant registrations come from the ``QdrantHandler`` plugin,
which registers them itself at import time.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Above every plugin's automatic processing, so a document is built from the
#: metadata those plugins have already finished writing.
INDEX_QUEUE = 101


def register_index_hooks() -> None:
    """Register the indexing tasks against the resource write hooks.

    A no-op when indexing is switched off, so an instance with no Elasticsearch
    does not queue a job per write that a worker can only fail.

    Never fatal: a backend that cannot read its own settings should still serve
    requests, and the consequence of skipping this is a stale index rather than
    an unavailable archive.
    """
    from archihub.core.hooks import get_hook_handler

    try:
        from archihub.api.search.services import indexing_enabled

        if not indexing_enabled():
            logger.info("Indexing is off; resource writes will not update the search index")
            return
    except Exception:
        logger.exception("Could not read the indexing setting; leaving the write hooks unregistered")
        return

    from archihub.worker.tasks.indexing import index_resources_delete_task, index_resources_task

    hooks = get_hook_handler()
    hooks.register("resource_create", index_resources_task, queue=INDEX_QUEUE)
    hooks.register("resource_update", index_resources_task, queue=INDEX_QUEUE)
    hooks.register("resource_delete", index_resources_delete_task, queue=INDEX_QUEUE)
    # Registered for parity with the legacy set. It never fires: the only caller
    # (`types.services`) spells the name in the plural. Left as it is rather
    # than corrected, because the body it would send is `{"slug": ...}` and
    # resources carry no `slug` field, so the task would match nothing either
    # way - fixing the name alone would buy a reindex of zero resources.
    hooks.register("resources_update_by_filter", index_resources_task, queue=INDEX_QUEUE)

    logger.info("Search indexing is active; registered the resource write hooks")
