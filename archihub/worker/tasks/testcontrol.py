"""The disposable-instance reset task.

Port of ``reset_task`` in ``app/api/health/testcontrol_services.py``.

THIS TASK DESTROYS DATA. It drops every Mongo collection, empties every search
and vector index, flushes the cache, clears the temporary-files directory and
then reseeds the instance with a fresh administrator. It exists so
``ArchiHUBTestRunner`` can start each suite from a known state.

It is reachable only through ``/health/test-control/reset``, behind the
three-part gate in ``core/security/test_control.py``: ``ARCHIHUB_TEST_MODE``
must be set, a marker document must have been inserted into the `system`
collection BY HAND, and a shared secret must be presented. The application never
writes that marker itself, which is the property that stops a production
instance from being one environment variable away from being wiped.

THE GATE IS RE-CHECKED HERE, not merely at the route. A queued Celery message is
a durable object: a reset queued against a disposable instance, left unconsumed,
and then picked up by a worker attached to a database that is no longer
disposable would run with no gate at all. The route's check answers "may this
caller ask?"; this one answers "may this database be wiped, now?".
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from datetime import datetime, timezone

from celery import shared_task

logger = logging.getLogger(__name__)

SEED_ADMIN_USERNAME = "test_admin@archihub.test"


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def _assert_disposable() -> None:
    """Refuse unless this instance is still marked disposable."""
    from archihub.core.security.test_control import TEST_MODE_MARKER_NAME
    from archihub.core.settings import get_settings

    if not get_settings().archihub_test_mode:
        raise RuntimeError("Refusing to reset: ARCHIHUB_TEST_MODE is not enabled")

    marker = _mongo().get_record("system", {"name": TEST_MODE_MARKER_NAME})
    if not (marker and marker.get("value")):
        raise RuntimeError("Refusing to reset: this instance is not marked disposable")


def _wipe_mongo() -> None:
    """Drop everything except the marker that says this may be done.

    Collections are DROPPED rather than emptied, and `system` is kept, both as
    in the original: a partial wipe leaves cross-collection references pointing
    at documents that no longer exist, and losing `system` would take the
    disposability marker with it - after which no further reset could run.
    """
    from archihub.core.security.test_control import TEST_MODE_MARKER_NAME

    mongo = _mongo()
    mongo.delete_records("system", {"name": {"$ne": TEST_MODE_MARKER_NAME}})

    for name in mongo.get_collections():
        if name == "system":
            continue
        mongo.db.drop_collection(name)


def _wipe_search() -> None:
    from archihub.api.search import services as search_services

    if not search_services.indexing_enabled():
        return
    try:
        from archihub.infra.search import get_search

        client = get_search()
        for alias in client.get_aliases():
            # Aliases come back fully qualified; delete_all_documents resolves a
            # suffix, so strip the prefix back off rather than double-applying it.
            client.delete_all_documents(_suffix_of(alias, client.index_prefix))
    except Exception:
        # A reset must finish even if one backing store is unreachable; the
        # reseed below is what the caller actually depends on.
        logger.exception("Could not clear the search indices during reset")


def _suffix_of(alias: str, prefix: str) -> str:
    marker = f"{prefix}-"
    return alias[len(marker) :] if alias.startswith(marker) else alias


def _wipe_vectors() -> None:
    from archihub.api.system import services as system_services

    if not system_services.get_setting_value("index_management", "vector_activation"):
        return
    try:
        from qdrant_client import models

        from archihub.infra.vectors import COLLECTIONS, get_vectors

        # No embedding model needed to delete points, and loading it costs
        # hundreds of megabytes and several seconds.
        client = get_vectors(load_model=False)
        for collection in COLLECTIONS:
            client.qdrant.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
            )
    except Exception:
        logger.exception("Could not clear the vector collections during reset")


def _wipe_temporal_files() -> None:
    """Empty the scratch directory, without following anything out of it."""
    from archihub.core.settings import get_settings

    path = get_settings().temporal_files_path
    if not path or not os.path.isdir(path):
        return

    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        try:
            # A symlink is unlinked, never walked into: `rmtree` on a link to a
            # real directory would delete that directory's contents.
            if os.path.islink(entry_path) or os.path.isfile(entry_path):
                os.remove(entry_path)
            elif os.path.isdir(entry_path):
                shutil.rmtree(entry_path)
        except Exception:
            logger.warning("Could not remove temporary entry %s", entry_path)


def _seed_baseline(run_id: str) -> dict:
    """Recreate the default settings and one administrator.

    The password is generated per run and returned to the caller, which is the
    only place it exists - the runner reads it from the reset result. It is
    never logged.
    """
    from archihub.api.system import services as system_services

    system_services.set_system_setting()

    password = secrets.token_urlsafe(18)
    result, status = system_services.set_first_time(
        {
            "username": SEED_ADMIN_USERNAME,
            "password": password,
            "confirmPassword": password,
            "typeTemplate": "basic",
        }
    )
    if status not in (200, 201):
        raise RuntimeError(f"Seeding failed with status {status}: {result}")

    return {
        "run_id": run_id,
        "admin_username": SEED_ADMIN_USERNAME,
        "admin_password": password,
    }


@shared_task(ignore_result=False, name="testcontrol.reset")
def reset_task(run_id: str) -> dict:
    """Wipe and reseed this instance. See the module docstring first."""
    _assert_disposable()

    logger.warning("Resetting this instance (run %s)", run_id)

    _wipe_mongo()
    _wipe_search()
    _wipe_vectors()

    try:
        from archihub.infra.cache import get_cache

        get_cache().clear_cache()
    except Exception:
        logger.exception("Could not flush the cache during reset")

    _wipe_temporal_files()

    credentials = _seed_baseline(run_id)
    credentials["completed_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("Reset complete (run %s)", run_id)
    return credentials
