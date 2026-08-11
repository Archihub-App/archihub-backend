"""Writes a plugin makes into the core collections.

These were ``PluginClass.update_data`` and ``PluginClass.clear_cache``, both
``@classmethod``s that needed no instance — which did not stop ``filesProcessing``
from constructing an entire plugin object inside a Celery task purely to reach
them:

```python
instance = ExtendedPluginClass('filesProcessing', '', **plugin_info)
instance.update_data('records', str(record['_id']), update)
```

Module-level functions here, so a task imports what it uses.

WHY A PROCESSING RESULT IS NOT WRITTEN THROUGH THE RECORDS ROUTE'S UPDATE

``records.services.update_record_by_id`` is the *user-facing* update: it accepts
a display name and an access right and nothing else. A plugin writing an OCR
result or a transcript is doing something different, and giving it the same
entry point would mean widening what a user can submit to that route. So it has
its own, narrow one.

The narrowness also fixes a lost update. The legacy pattern was:

```python
update = {'processing': record['processing']}          # read a moment ago
update['processing']['liquidText'] = {...}
instance.update_data('records', str(record['_id']), update)   # writes ALL of it
```

Two plugins finishing different processings on the same record — which is the
normal case, since a file is OCR'd and transcribed and thumbnailed by different
tasks — each write back the whole `processing` block as they last read it, and
the second silently discards the first's result. ``store_processing_result``
`$set`s one dotted path instead, the same fix already applied in
``records/blocks.py``. BACKEND_FINDINGS F49.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _object_id(value: Any):
    from bson.errors import InvalidId
    from bson.objectid import ObjectId

    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def store_processing_result(record_id: str, key: str, result: dict) -> bool:
    """Record one processing result against a record.

    ``key`` names the processing (``liquidText``, ``fileProcessing``, ...) and
    becomes a Mongo field path, so it is validated: it comes from plugin code
    today, but a field name built from data is how a `$`-prefixed key ends up in
    a document that nothing can then query.

    Returns whether a record was matched.
    """
    if not key or not key.replace("_", "").isalnum():
        raise ValueError(f"Invalid processing key {key!r}")

    object_id = _object_id(record_id)
    if object_id is None:
        logger.warning("Not a record id: %r", record_id)
        return False

    outcome = _mongo().update_record_operator(
        "records",
        {"_id": object_id},
        {
            "$set": {
                f"processing.{key}": result,
                "updatedBy": "system",
                "updatedAt": datetime.datetime.now(),
            }
        },
    )

    matched = bool(getattr(outcome, "matched_count", 0))
    if not matched:
        logger.warning("No record %s to store %s result against", record_id, key)
        return False

    _call_hook("record_update", {"_id": str(record_id), "processing": {key: result}})
    return True


def get_processing(record_id: str) -> dict:
    """A record's processing block, or ``{}``."""
    object_id = _object_id(record_id)
    if object_id is None:
        return {}
    record = _mongo().get_record("records", {"_id": object_id}, fields={"processing": 1})
    return (record or {}).get("processing") or {}


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def update_resource(resource_id: str, update: dict) -> tuple[dict, int]:
    """Apply a plugin's metadata update to a resource.

    Validates against the content type's form exactly as a user edit does — the
    legacy ``update_data`` did too, and that is the one part of it worth keeping:
    a plugin writing a malformed date leaves the resource unindexable, and the
    error surfaces much later somewhere unrelated.

    ``updatedBy`` is ``system``, not the operator who started the job. That is
    the legacy behaviour and it is right: the change was made by a program.
    """
    from archihub.api.resources.validation import validate_fields
    from archihub.api.types.services import get_metadata

    object_id = _object_id(resource_id)
    if object_id is None:
        return {"msg": "Invalid resource id"}, 400

    post_type = update.get("post_type")
    if not post_type:
        # The original indexed `update['post_type']` directly, so a caller that
        # omitted it raised KeyError inside a Celery task.
        return {"msg": "The update must state the content type"}, 400

    metadata = get_metadata(post_type)
    if not metadata:
        return {"msg": "Unknown content type"}, 400

    body, errors = validate_fields(dict(update), metadata)
    if errors:
        return {"msg": errors}, 400

    body["updatedAt"] = datetime.datetime.now()
    body["updatedBy"] = "system"
    body.pop("_id", None)

    _mongo().update_record("resources", {"_id": object_id}, body)
    _call_hook("resource_update", {**body, "_id": str(resource_id)})

    return {"msg": "Resource updated"}, 200


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def broadcast_cache_clear() -> None:
    """Ask the master node to flush its cache.

    Fire-and-forget by design — a plugin task must not fail because a peer is
    unreachable — but with a **timeout**, which the original had not: it was a
    bare ``requests.get`` inside ``except: pass``, so an unresponsive master
    held the worker for the full socket timeout and the swallowing handler made
    that indistinguishable from success. Part of the P2 family.
    """
    from archihub.core.settings import get_settings

    settings = get_settings()
    master = (settings.master_host or "").rstrip("/")
    if not master or not settings.node_token:
        return

    try:
        import requests

        requests.get(
            f"{master}/system/node-clear-cache",
            headers={"Authorization": f"Bearer {settings.node_token}"},
            timeout=5,
        )
    except Exception:
        logger.debug("Cache-clear broadcast to %s failed", master, exc_info=True)


def _call_hook(name: str, payload: dict) -> None:
    from archihub.core.hooks import get_hook_handler

    try:
        get_hook_handler().call(name, payload)
    except Exception:
        logger.exception("%s hook failed", name)
