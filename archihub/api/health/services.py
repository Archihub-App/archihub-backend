"""Readiness checks.

Port of ``app/api/health/services.py``. The response shape is reproduced exactly
because ``ArchiHUBTestRunner`` preflight and (once section 11 lands) the Docker
healthcheck both read it::

    {"ready": bool, "checks": {"<dep>": {"status": "ok"|"error"|"disabled",
                                         "error": "<only when failing>"}}}

Elasticsearch and Qdrant report ``disabled`` rather than failing when their
feature flags are off in the ``system`` collection, so an instance that never
enabled indexing is still "ready".

One deliberate behavioural fix: the legacy Elasticsearch check instantiated
``IndexHandler``, whose ``__new__`` calls ``start()``, which **creates an index**
when none exists. A readiness probe must not mutate cluster state, and Docker
would have called it every 30 seconds. The new ``SearchClient`` does no I/O on
construction and the check is a plain cluster-health read.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

logger = logging.getLogger(__name__)

# status value meaning "the feature is switched off, don't count it against readiness"
DISABLED = "disabled"


def _find_by_id(data_array: list | None, id_value: str) -> dict | None:
    """Port of ``app/utils/functions.py:find_by_id``."""
    for item in data_array or []:
        if item.get("id") == id_value:
            return item
    return None


def load_index_management() -> dict | None:
    """Fetch the ``index_management`` settings document, or None if unavailable.

    Read ONCE per readiness call and passed into both feature checks. Fetching
    it per check made an unreachable MongoDB cost three separate connection
    timeouts instead of one, pushing /health/ready past 40 seconds - long enough
    for a Docker healthcheck to time out and report the container unhealthy for
    the wrong reason.
    """
    from archihub.infra.mongo import get_mongo

    return get_mongo().get_record("system", {"name": "index_management"})


def _feature_enabled(index_management: dict | None, flag_id: str) -> bool:
    flag = _find_by_id((index_management or {}).get("data"), flag_id)
    return bool(flag and flag.get("value"))


def check_mongo() -> tuple[bool | str, str | None]:
    try:
        from archihub.infra.mongo import get_mongo

        get_mongo().ping()
        return True, None
    except Exception as exc:
        logger.warning("Mongo readiness check failed", exc_info=True)
        return False, str(exc)


def check_redis() -> tuple[bool | str, str | None]:
    try:
        from archihub.infra.cache import get_cache

        get_cache().ping()
        return True, None
    except Exception as exc:
        logger.warning("Redis readiness check failed", exc_info=True)
        return False, str(exc)


def check_elasticsearch(index_management: dict | None = None) -> tuple[bool | str, str | None]:
    if not _feature_enabled(index_management, "index_activation"):
        return DISABLED, None

    try:
        from archihub.infra.search import get_search

        get_search().ping()
        return True, None
    except Exception as exc:
        logger.warning("Elasticsearch readiness check failed", exc_info=True)
        return False, str(exc)


def check_qdrant(index_management: dict | None = None) -> tuple[bool | str, str | None]:
    if not _feature_enabled(index_management, "vector_activation"):
        return DISABLED, None

    try:
        from archihub.infra.vectors import get_vectors

        # load_model=False: readiness must not trigger a multi-hundred-MB
        # sentence-transformers load.
        get_vectors(load_model=False).ping()
        return True, None
    except Exception as exc:
        logger.warning("Qdrant readiness check failed", exc_info=True)
        return False, str(exc)


def check_celery() -> tuple[bool | str, str | None]:
    try:
        from archihub.worker.celery_app import celery_app

        pings = celery_app.control.ping(timeout=1.0)
        if not pings:
            return False, "No Celery worker responded"
        return True, None
    except Exception as exc:
        logger.warning("Celery readiness check failed", exc_info=True)
        return False, str(exc)


def _run_parallel(jobs: dict[str, object], timeout: float) -> dict[str, tuple]:
    """Run independent checks concurrently, bounding the total wait.

    The dependencies do not depend on one another, so probing them in sequence
    just adds up their timeouts: with everything down this endpoint took 41s
    (later 21s), well past the point where a Docker healthcheck or an
    orchestrator's readiness probe gives up and blames the container. Run
    together, the cost is the slowest single check rather than their sum.

    A job that overruns ``timeout`` is reported as an error instead of being
    waited on - a readiness probe that hangs is useless.
    """
    results: dict[str, tuple] = {}
    with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as pool:
        futures = {pool.submit(job): name for name, job in jobs.items()}  # type: ignore[arg-type]
        for future in as_completed(futures, timeout=None):
            name = futures[future]
            try:
                results[name] = future.result(timeout=timeout)
            except FuturesTimeout:
                results[name] = (False, f"check timed out after {timeout}s")
            except Exception as exc:  # a check itself blew up
                results[name] = (False, str(exc))
    return results


def get_readiness(timeout: float = 15.0) -> tuple[dict, int]:
    # Stage 1: everything that needs no prior information.
    stage_one = _run_parallel(
        {"mongo": check_mongo, "redis": check_redis, "celery": check_celery},
        timeout=timeout,
    )

    # The Elasticsearch and Qdrant checks are gated on flags stored in Mongo. If
    # Mongo is unreachable those flags are unknowable, so report both as
    # 'disabled' rather than paying another connection timeout each to
    # rediscover the outage check_mongo already reported.
    index_management: dict | None = None
    if stage_one.get("mongo", (False, None))[0] is True:
        try:
            index_management = load_index_management()
        except Exception:
            logger.warning("Could not read index_management settings", exc_info=True)

    # Stage 2: the flag-gated checks, also concurrent with each other. Both
    # return immediately when their feature is switched off.
    stage_two = _run_parallel(
        {
            "elasticsearch": lambda: check_elasticsearch(index_management),
            "qdrant": lambda: check_qdrant(index_management),
        },
        timeout=timeout,
    )

    checks = {
        "mongo": stage_one["mongo"],
        "redis": stage_one["redis"],
        "elasticsearch": stage_two["elasticsearch"],
        "qdrant": stage_two["qdrant"],
        "celery": stage_one["celery"],
    }

    result: dict[str, dict] = {}
    all_ready = True
    for name, (state, error) in checks.items():
        result[name] = {"status": state if isinstance(state, str) else ("ok" if state else "error")}
        if error:
            result[name]["error"] = error
        if state is False:
            all_ready = False

    return {"ready": all_ready, "checks": result}, (200 if all_ready else 503)
