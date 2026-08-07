"""Redis cache.

Port of ``app/utils/CacheHandler.py``.

Stays on ``redis.StrictRedis`` + ``python-redis-cache`` deliberately. The first
migration draft proposed replacing the memoisation decorator with a hand-rolled
async one; the review found ``@cacheHandler.cache.cache()`` is applied to **79
functions** with **119** ``.invalidate()`` / ``.invalidate_all()`` call sites, so
a bespoke reimplementation would have to reproduce the library's exact key
derivation and attach the invalidation helpers to every wrapped function or ship
silent stale-cache bugs. Since the stack stays synchronous (decision 6), there is
no async-compatibility reason to swap it at all.
"""

from __future__ import annotations

import logging

from redis import StrictRedis
from redis_cache import RedisCache

from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)


class CacheClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = StrictRedis(host=settings.celery_broker_host, decode_responses=True)
        self.cache = RedisCache(redis_client=self.client)

    def ping(self) -> None:
        """Raise if Redis is unreachable. Used by /health/ready."""
        self.client.ping()

    def clear_cache(self) -> None:
        """Flush the Redis database.

        Preserves legacy semantics exactly, including the sharp edge: this is a
        ``FLUSHDB``, so it drops *everything* in the database - which is the same
        database Celery uses as its broker. Not scoped to application keys.
        """
        try:
            self.client.flushdb()
        except Exception:
            logger.warning("Cache flush failed", exc_info=True)


_cache: CacheClient | None = None


def get_cache() -> CacheClient:
    global _cache
    if _cache is None:
        _cache = CacheClient()
    return _cache


def reset_cache() -> None:
    """Drop the cached client (tests)."""
    global _cache
    _cache = None
