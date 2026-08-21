"""Redis cache, and the rule that makes it safe to use.

TWO PIECES, AND THE SECOND IS THE WHOLE DESIGN
----------------------------------------------

``cached(...)`` memoises a read in Redis. ``bump(collection)`` makes every entry
that read a collection unreachable. The point is that **nothing calls ``bump``
by hand**: ``MongoClientWrapper``'s write methods call it, so a cache entry is
invalidated by the act of writing to the data it was derived from.

**INVALIDATION IS NEVER A LIST OF NAMES.** The obvious design - each write path
calling the caches it believes it affects - is correct only while every author
of every write remembers every cache, and it degrades silently: a forgotten call
site produces no error, no log line, and an answer computed before the data
changed. What that costs here is not a stale page but ``has_role`` answering
from before a role was revoked, so the mechanism has to be one that cannot be
forgotten rather than one that is easy to remember.

HOW A GENERATION WORKS. Each collection has a counter in Redis. A cached
function declares which collections it reads, and their current counters are
part of its key. Writing to a collection increments its counter, so every key
derived from the old value is simply never looked up again - no key enumeration,
no ``SCAN``, no cross-process coordination, and it is atomic. Superseded entries
fall out on their TTL.

WHAT THIS COSTS. One ``MGET`` of the generation counters per cached call, and a
counter ``INCR`` per write. A write also invalidates more than strictly
necessary - any entry reading that collection, not only the affected document -
which is the trade being made deliberately: over-invalidation costs a recompute,
under-invalidation serves a wrong authorisation decision.

DECIDING WHAT TO CACHE. Small, slow-changing, read on nearly every request:
authorisation, the role and access-right vocabularies, content types, forms.
Not: anything derived from a user's own request, anything large enough that
moving it through Redis costs what recomputing it would, and nothing whose
freshness requirement is finer than a collection.

WHEN REDIS IS DOWN the decorator calls straight through. A cache that takes the
application down with it is worse than no cache; ``/health/ready`` reports Redis
separately, which is where an operator should learn about it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from typing import Any, Callable, Iterable

from redis import StrictRedis

from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

#: Every key this module writes begins with this. `clear_cache` deletes by this
#: prefix rather than issuing FLUSHDB, because the cache shares a Redis database
#: with the Celery broker: flushing would discard queued jobs - reindexing,
#: file processing, every plugin bulk action - as a side effect of an operator
#: clicking "clear cache".
KEY_PREFIX = "archihub:cache"
GENERATION_PREFIX = "archihub:gen"

#: A cached entry expires even if its generation never moves. A backstop, not
#: the invalidation mechanism: it bounds how long a defect in the bump path can
#: serve a stale answer.
DEFAULT_TTL = 900

#: Compose the key from the current generation counters and read it, server
#: side. Returns both, because a miss still needs the key to write back to.
#: A counter that has never been set reads as generation 0.
LOOKUP_SCRIPT = """
local parts = {ARGV[1]}
for i = 1, #KEYS do
  parts[#parts + 1] = redis.call('GET', KEYS[i]) or '0'
end
parts[#parts + 1] = ARGV[2]
local key = table.concat(parts, ':')
return {key, redis.call('GET', key)}
"""


def _dumps(value: Any) -> str:
    """Serialise a cached value, preserving Mongo's own types.

    ``bson.json_util`` round-trips ``ObjectId`` and ``datetime``; plain ``json``
    would raise on the first of either, which is most documents in this
    database.
    """
    from bson import json_util

    return json_util.dumps(value)


def _loads(raw: str) -> Any:
    from bson import json_util

    return json_util.loads(raw)


def _fingerprint(function: Callable, args: tuple, kwargs: dict) -> str:
    """A stable key for one call.

    Stable ACROSS PROCESSES, which rules out ``hash()`` - it is salted per
    interpreter, so the web process and a worker would compute different keys
    for the same call and neither would ever see the other's entry.
    """
    payload = json.dumps([args, sorted(kwargs.items())], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class CacheClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.cache_enabled
        self.client = StrictRedis(host=settings.celery_broker_host, decode_responses=True)
        self._lookup_script = self.client.register_script(LOOKUP_SCRIPT)

    # -- connectivity ---------------------------------------------------
    def ping(self) -> None:
        """Raise if Redis is unreachable. Used by /health/ready."""
        self.client.ping()

    # -- generations ----------------------------------------------------
    def generations(self, collections: Iterable[str]) -> list[str]:
        names = [f"{GENERATION_PREFIX}:{c}" for c in collections]
        if not names:
            return []
        return [value or "0" for value in self.client.mget(names)]

    def lookup(self, namespace: str, collections: Iterable[str], fingerprint: str):
        """The composed key and its value, in ONE round trip.

        Composing the key needs the generation counters, and reading the value
        needs the key - which is two sequential round trips if done from here.
        Measured on this deployment a Redis round trip costs 0.36ms against
        0.57ms for the Mongo point read being avoided, so paying two of them
        makes the cache SLOWER than the database for a single-document lookup.
        The composition therefore happens inside Redis.
        """
        names = [f"{GENERATION_PREFIX}:{c}" for c in collections]
        key, value = self._lookup_script(keys=names, args=[namespace, fingerprint])
        return key, (None if value is None else _loads(value))

    def bump(self, collection: str) -> None:
        """Make every cached read of ``collection`` unreachable."""
        self.client.incr(f"{GENERATION_PREFIX}:{collection}")

    def bump_all(self) -> None:
        """Invalidate everything without deleting a single cache key.

        Used by the operator-facing clear and by the node-to-node broadcast.
        Deleting the generation counters is what does it: a missing counter
        reads as ``0`` and every live key was built from something higher.
        """
        self._delete_prefix(GENERATION_PREFIX)

    # -- values ---------------------------------------------------------
    def get(self, key: str) -> Any:
        raw = self.client.get(key)
        return None if raw is None else _loads(raw)

    def set(self, key: str, value: Any, ttl: int) -> None:
        self.client.set(key, _dumps(value), ex=ttl)

    def clear_cache(self) -> None:
        """Drop this application's cached entries. Not a FLUSHDB - see KEY_PREFIX."""
        try:
            self._delete_prefix(KEY_PREFIX)
            self.bump_all()
        except Exception:
            logger.warning("Cache clear failed", exc_info=True)

    def flush_database(self) -> None:
        """Drop the entire Redis database, queued Celery jobs included.

        Only for the disposable-instance reset, where discarding queued work is
        the intent: a job queued before a wipe refers to documents that no
        longer exist. Never reachable from an operator action - that is
        ``clear_cache``, which is scoped.
        """
        self.client.flushdb()

    def _delete_prefix(self, prefix: str) -> None:
        """Delete by prefix in batches.

        ``scan_iter`` rather than ``KEYS``: this runs against the same Redis the
        broker uses, and ``KEYS`` blocks the server for the length of the
        keyspace.
        """
        batch: list[str] = []
        for key in self.client.scan_iter(match=f"{prefix}:*", count=500):
            batch.append(key)
            if len(batch) >= 500:
                self.client.delete(*batch)
                batch = []
        if batch:
            self.client.delete(*batch)


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


def bump(collection: str) -> None:
    """Invalidate every cached read of ``collection``.

    Called by the Mongo wrapper's write methods, never from a service. If you
    find yourself wanting to call this by hand, the write it should follow is
    not going through the wrapper.
    """
    try:
        if get_cache().enabled:
            get_cache().bump(collection)
    except Exception:
        # A cache that cannot be invalidated must not fail the write. The TTL
        # bounds the exposure; the alternative is refusing a legitimate write
        # because Redis is unavailable.
        logger.warning("Could not invalidate cache for %s", collection, exc_info=True)


def cached(*collections: str, ttl: int = DEFAULT_TTL) -> Callable:
    """Memoise a read whose answer depends only on ``collections``.

    Declaring the collections is not documentation - it is what invalidates the
    entry. A function that reads a collection it does not declare will serve
    answers from before that collection was last written, and nothing will
    report it.
    """
    if not collections:
        raise ValueError("a cached function must declare the collections it reads")

    def decorate(function: Callable) -> Callable:
        namespace = f"{function.__module__}.{function.__qualname__}"

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                client = get_cache()
                if not client.enabled:
                    return function(*args, **kwargs)
                key, hit = client.lookup(
                    f"{KEY_PREFIX}:{namespace}",
                    collections,
                    _fingerprint(function, args, kwargs),
                )
                if hit is not None:
                    return hit
            except Exception:
                logger.warning("Cache read failed for %s", namespace, exc_info=True)
                return function(*args, **kwargs)

            value = function(*args, **kwargs)
            try:
                # `None` is not stored: it is indistinguishable from a miss on
                # read, so caching it would mean recomputing every time anyway
                # while looking like a hit path that works.
                if value is not None:
                    client.set(key, value, ttl)
            except Exception:
                logger.warning("Cache write failed for %s", namespace, exc_info=True)
            return value

        wrapper.cache_collections = collections  # type: ignore[attr-defined]
        return wrapper

    return decorate
