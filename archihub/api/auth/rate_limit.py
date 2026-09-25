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

3. **An attempt is counted before the password is checked, and the check and
   the count are one atomic operation** (the Redis script below), so no number
   of simultaneous attempts gets past a budget.

4. **It fails CLOSED.** If Redis is unavailable, login is refused rather than
   allowed through unthrottled. This is the one place in the codebase where an
   infrastructure outage should reduce availability instead of protection: the
   alternative is that brute-force defence silently disappears at exactly the
   moment the system is least healthy, with nothing in the logs to say so.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# One value for both the window and the TTL - see invariant 1.
WINDOW_SECONDS = 600
MAX_ATTEMPTS_PER_USERNAME = 5
# Higher, because one address may legitimately serve many users behind NAT.
MAX_ATTEMPTS_PER_IP = 20

KEY_PREFIX = "login_attempts"

# Each key holds a JSON list of attempt timestamps. KEYS are the budgets to
# charge; ARGV is now, the window, then one limit per key. Answers 0 when the
# attempt was recorded in every budget, or the 1-based index of the budget that
# is exhausted - in which case nothing is recorded.
_ACQUIRE = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local cutoff = now - window
local lists = {}
for i, key in ipairs(KEYS) do
  local list = {}
  local raw = redis.call('GET', key)
  if raw then
    local ok, stored = pcall(cjson.decode, raw)
    if ok and type(stored) == 'table' then
      for _, ts in ipairs(stored) do
        if type(ts) == 'number' and ts > cutoff then list[#list + 1] = ts end
      end
    end
  end
  if #list >= tonumber(ARGV[2 + i]) then return i end
  lists[i] = list
end
for i, key in ipairs(KEYS) do
  local list = lists[i]
  list[#list + 1] = now
  redis.call('SET', key, cjson.encode(list), 'EX', window)
end
return 0
"""

class RateLimitUnavailable(RuntimeError):
    """Raised when the attempt store cannot be reached - callers must refuse."""


def _redis():
    from archihub.infra.cache import get_cache

    return get_cache().client


def _key(scope: str, value: str) -> str:
    return f"{KEY_PREFIX}:{scope}:{value}"


def _budgets(username: str, client_ip: str | None) -> list[tuple[str, str, int]]:
    budgets = []
    if username:
        budgets.append(("account", _key("user", username), MAX_ATTEMPTS_PER_USERNAME))
    if client_ip:
        budgets.append(("address", _key("ip", client_ip), MAX_ATTEMPTS_PER_IP))
    return budgets


def acquire(username: str, client_ip: str | None = None) -> bool:
    """Charge one login attempt to the account's and the address's budgets.

    Returns ``False`` when either budget is exhausted and the attempt must be
    refused without checking credentials. Raises :class:`RateLimitUnavailable`
    when the store cannot be reached.
    """
    now = time.time()
    budgets = _budgets(username, client_ip)
    if not budgets:
        return True

    keys = [key for _, key, _ in budgets]
    limits = [limit for _, _, limit in budgets]
    try:
        exhausted = int(_redis().eval(_ACQUIRE, len(keys), *keys, now, WINDOW_SECONDS, *limits))
    except Exception as exc:
        raise RateLimitUnavailable(str(exc)) from exc

    if exhausted:
        scope = budgets[exhausted - 1][0]
        subject = username if scope == "account" else client_ip
        logger.warning("Login throttled for %s %s", scope, subject)
        return False
    return True


def clear_attempts(username: str, client_ip: str | None = None) -> None:
    """Reset both budgets after a successful login.

    The address budget is cleared too: behind a proxy that does not forward
    the client's address, every user shares one, and failures that outlived a
    success would lock the whole organisation out.
    """
    for _, key, _ in _budgets(username, client_ip):
        try:
            _redis().delete(key)
        except Exception:
            logger.debug("Could not clear login attempts", exc_info=True)
