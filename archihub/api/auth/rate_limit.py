"""Login throttling.

Failed login attempts are counted in Redis and refused past a threshold.

DESIGN INVARIANTS - each of these is load-bearing, and each was chosen because
the obvious alternative is weaker:

1. **The counter's lifetime and the window it represents are the same value.**
   Both derive from one constant below, so they cannot drift apart. Were the
   stored record to expire sooner than the window the code believes it is
   enforcing, the effective throttle would silently become the shorter of the
   two - with nothing to indicate that the configured limit is not the limit in
   force. Keep them tied to a single value.

2. **Attempts are counted per (username, client IP), and both budgets apply.**
   Counting by username alone leaves credential-spraying across many accounts
   unthrottled - each username carries its own fresh allowance - and lets anyone
   deliberately exhaust a chosen user's budget to lock them out. Counting by IP
   alone fails behind NAT and against distributed sources. Requiring both means
   neither pattern gets a free pass.

3. **It fails CLOSED.** If Redis is unavailable, login is refused rather than
   allowed through unthrottled. This is the one place in the codebase where an
   infrastructure outage should reduce availability instead of protection: the
   alternative is that brute-force defence silently disappears at exactly the
   moment the system is least healthy, with nothing in the logs to say so.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

# One value for both the window and the TTL - see invariant 1.
WINDOW_SECONDS = 600
MAX_ATTEMPTS_PER_USERNAME = 5
# Higher, because one address may legitimately serve many users behind NAT.
MAX_ATTEMPTS_PER_IP = 20

KEY_PREFIX = "login_attempts"


class RateLimitUnavailable(RuntimeError):
    """Raised when the attempt store cannot be reached - callers must refuse."""


def _redis():
    from archihub.infra.cache import get_cache

    return get_cache().client


def _key(scope: str, value: str) -> str:
    return f"{KEY_PREFIX}:{scope}:{value}"


def _recent_attempts(key: str) -> list[float]:
    """Timestamps inside the window. Raises if the store is unreachable."""
    try:
        raw = _redis().get(key)
    except Exception as exc:
        raise RateLimitUnavailable(str(exc)) from exc

    if not raw:
        return []

    try:
        attempts = json.loads(raw)
    except (TypeError, ValueError):
        return []

    cutoff = time.time() - WINDOW_SECONDS
    return [float(ts) for ts in attempts if isinstance(ts, (int, float)) and float(ts) > cutoff]


def is_rate_limited(username: str, client_ip: str | None = None) -> bool:
    """Whether this login attempt should be refused before checking credentials.

    Raises :class:`RateLimitUnavailable` when the store cannot be reached, so
    the caller refuses rather than silently proceeding unprotected.
    """
    if len(_recent_attempts(_key("user", username))) >= MAX_ATTEMPTS_PER_USERNAME:
        logger.warning("Login throttled for account %s", username)
        return True

    if client_ip and len(_recent_attempts(_key("ip", client_ip))) >= MAX_ATTEMPTS_PER_IP:
        logger.warning("Login throttled for address %s", client_ip)
        return True

    return False


def record_attempt(username: str, client_ip: str | None = None) -> None:
    """Record one failed attempt against both budgets."""
    now = time.time()

    for scope, value in (("user", username), ("ip", client_ip)):
        if not value:
            continue
        key = _key(scope, value)
        try:
            attempts = _recent_attempts(key)
            attempts.append(now)
            # TTL derived from the same constant as the window, so the record
            # cannot outlive - or under-live - the period it represents.
            _redis().setex(key, WINDOW_SECONDS, json.dumps(attempts))
        except Exception:
            # Recording is best-effort; the gate itself fails closed, so a
            # write failure cannot open the door.
            logger.warning("Could not record login attempt for %s", scope, exc_info=True)


def clear_attempts(username: str, client_ip: str | None = None) -> None:
    """Reset both budgets after a successful login."""
    for scope, value in (("user", username), ("ip", client_ip)):
        if not value:
            continue
        try:
            _redis().delete(_key(scope, value))
        except Exception:
            logger.debug("Could not clear login attempts for %s", scope, exc_info=True)
