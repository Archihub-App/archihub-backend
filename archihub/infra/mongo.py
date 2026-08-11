"""MongoDB access.

Port of ``app/utils/MongoConector.py`` + ``app/utils/DatabaseHandler.py``.

Kept synchronous on purpose (PLAN_FASTAPI.md decision 6): the same instance is
used by FastAPI route handlers (which run in Starlette's threadpool because they
are declared ``def``) and by Celery task bodies, exactly as the legacy
``DatabaseHandler`` singleton was.

The generic CRUD surface is preserved method-for-method so ported service
modules keep the same call shapes. Two deliberate corrections:

* ``update_record`` accepted only Pydantic v1 models (it called ``.dict()``
  unconditionally). It now accepts v2 models, v1 models and plain dicts, which
  is what callers were already passing in practice.
* Connection settings come from ``archihub.core.settings`` rather than being
  re-read from ``os.environ`` per module, so the hardcoded fallback Mongo
  password in ``MongoConector`` is gone. See the settings module docstring.
"""

from __future__ import annotations

import logging
from typing import Any

import pymongo
from pymongo.database import Database

from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)


def _to_payload(record: Any, *, exclude_unset: bool = True) -> dict:
    """Normalise a dict / Pydantic v2 model / Pydantic v1 model to a dict."""
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):  # Pydantic v2
        return record.model_dump(exclude_unset=exclude_unset)
    if hasattr(record, "dict"):  # Pydantic v1 (legacy models still in tree)
        return record.dict(exclude_unset=exclude_unset)
    raise TypeError(f"Expected a dict or model-like object, got {type(record).__name__}")


class MongoClientWrapper:
    """Thin, generic wrapper over a pymongo database handle."""

    def __init__(self) -> None:
        settings = get_settings()
        self.database_name = settings.mongo_database
        self.client: pymongo.MongoClient = pymongo.MongoClient(settings.mongo_uri())
        self.db: Database = self.client[self.database_name]

    # -- connectivity ---------------------------------------------------
    def ping(self) -> None:
        """Raise if the server is unreachable. Used by /health/ready."""
        self.client.admin.command("ping")

    def get_collections(self) -> list[str]:
        return self.db.list_collection_names()

    # -- reads ----------------------------------------------------------
    def get_all_records(
        self,
        collection: str,
        filters: dict | None = None,
        sort: list | None = None,
        limit: int = 0,
        skip: int = 0,
        fields: dict | None = None,
    ):
        """Return a cursor.

        NOTE: this returns a live pymongo cursor, matching legacy behaviour.
        Callers that test it for emptiness must materialise it first - a cursor
        is always truthy, which is the root of the documented
        ``logs/routes.py`` "never returns 404" bug.
        """
        cursor = self.db[collection].find(filters or {}, fields or {})
        if sort:
            cursor = cursor.sort(sort)
        return cursor.limit(limit).skip(skip)

    def get_record(self, collection: str, filters: dict | None = None, fields: dict | None = None):
        return self.db[collection].find_one(filters or {}, fields or {})

    def distinct(self, collection: str, field: str, filters: dict | None = None):
        return self.db[collection].distinct(field, filters or {})

    def count(self, collection: str, filters: dict | None = None) -> int:
        return self.db[collection].count_documents(filters or {})

    def aggregate(self, collection: str, pipeline: list):
        return self.db[collection].aggregate(pipeline)

    # -- writes ---------------------------------------------------------
    def insert_record(self, collection: str, record: Any):
        return self.db[collection].insert_one(_to_payload(record))

    def insert_records(self, collection: str, records: list[Any]):
        """Insert many documents in one round trip.

        ``ordered=False`` so one rejected document does not abandon the rest of
        the batch - the caller gets a ``BulkWriteError`` naming the failures and
        everything else is written. Used by the boundary loader, which inserts
        tens of thousands of polygons and was doing so one at a time.
        """
        if not records:
            return None
        return self.db[collection].insert_many([_to_payload(r) for r in records], ordered=False)

    def update_record(self, collection: str, filters: dict, update_model: Any):
        return self.db[collection].update_one(filters, {"$set": _to_payload(update_model)})

    def upsert_record(self, collection: str, filters: dict, update_model: Any):
        """Write, creating the document if it is not there.

        Distinct from ``update_record`` because "record this if absent" and
        "change this if present" are different intentions, and silently
        creating on a mistyped filter is a way to grow a collection of
        near-duplicates.
        """
        return self.db[collection].update_one(
            filters, {"$set": _to_payload(update_model)}, upsert=True
        )

    def update_records(self, collection: str, filters: dict, update_fields: dict):
        return self.db[collection].update_many(filters, {"$set": update_fields})

    def update_record_operator(self, collection: str, filters: dict, operator: dict, **kwargs):
        return self.db[collection].update_one(filters, operator, **kwargs)

    def increment_record(self, collection: str, filters: dict, field: str, value: int):
        return self.db[collection].update_one(filters, {"$inc": {field: value}})

    def delete_record(self, collection: str, filters: dict):
        return self.db[collection].delete_one(filters)

    def delete_records(self, collection: str, filters: dict):
        return self.db[collection].delete_many(filters)


_mongo: MongoClientWrapper | None = None


def get_mongo() -> MongoClientWrapper:
    """Return the process-wide Mongo client, creating it on first use.

    Lazily constructed rather than instantiated at import time so that importing
    a service module never opens a socket - which is what made the legacy
    handlers impossible to import in a test or a script without a live database.
    """
    global _mongo
    if _mongo is None:
        _mongo = MongoClientWrapper()
        logger.debug("Mongo client initialised for database %s", _mongo.database_name)
    return _mongo


def reset_mongo() -> None:
    """Drop the cached client (tests, and reconnect-after-config-change)."""
    global _mongo
    if _mongo is not None:
        try:
            _mongo.client.close()
        except Exception:  # pragma: no cover - best effort
            logger.warning("Error closing Mongo client", exc_info=True)
    _mongo = None
