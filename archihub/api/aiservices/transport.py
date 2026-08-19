"""The single HTTP path every dialect goes through.

One place for timeouts, retries, connection reuse, streaming and error mapping.
Several transports chosen per branch is how a timeout comes to apply on one path
and not another, and how a retry loop ends up existing for some vendors and not
others - differences that read as decisions to whoever finds them next.

**Retries are bounded, jittered, and only for reasons a retry can fix.** Deciding
by substring - retrying whenever "429" appears anywhere in an exception - also
matches an unrelated model name or request id; and retrying on a fixed schedule
with no jitter turns one rate-limited instance into synchronised retry storms.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Iterator

import httpx

from archihub.api.aiservices import errors

logger = logging.getLogger(__name__)

#: Connect/read/write/pool timeouts. A model call is slow by nature, so the read
#: budget is generous — but it is finite, which the legacy `getModels` calls
#: were not.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

#: Discovery is not slow, and blocking a settings screen on an unreachable
#: endpoint is worse than telling the operator it is unreachable.
DISCOVERY_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)

MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.75
BACKOFF_CAP = 20.0

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    """The shared connection pool.

    Reused across calls because TLS handshakes to a model endpoint are a
    meaningful share of the latency of a short completion, and because the
    legacy code built a fresh client per request in every provider class.
    """
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=False,
        )
    return _client


def reset_client() -> None:
    """Close the pool. Called at shutdown, and by tests."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # pragma: no cover - best effort
            logger.debug("Could not close the AI transport client", exc_info=True)
        _client = None


def _sleep_for(attempt: int, retry_after: float | None) -> float:
    """Exponential backoff with full jitter, honouring ``Retry-After``.

    Jitter matters here: several workers rate-limited by the same provider at
    the same moment will otherwise retry in lockstep and stay rate-limited.
    """
    if retry_after is not None:
        return min(retry_after, BACKOFF_CAP)
    ceiling = min(BACKOFF_BASE * (2**attempt), BACKOFF_CAP)
    return random.uniform(0, ceiling)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        # The header may be an HTTP date. Falling back to computed backoff is
        # better than parsing dates for a value we only use as a hint.
        return None


def request_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
    timeout: httpx.Timeout | None = None,
    attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Make a request and return the decoded body, or raise ``ProviderError``."""
    client = get_client()
    last: errors.ProviderError | None = None

    for attempt in range(attempts):
        try:
            response = client.request(
                method, url, headers=headers, json=json_body, timeout=timeout or DEFAULT_TIMEOUT
            )
        except httpx.TimeoutException as exc:
            last = errors.ProviderError(errors.Reason.TIMEOUT, str(exc) or "The request timed out")
        except httpx.HTTPError as exc:
            last = errors.ProviderError(errors.Reason.UNAVAILABLE, str(exc) or "The provider is unreachable")
        else:
            if response.status_code < 400:
                return _decode(response)
            last = errors.from_response(response.status_code, _safe_json(response))
            last.retry_after = _retry_after(response)

        if not last.retryable or attempt == attempts - 1:
            raise last

        delay = _sleep_for(attempt, last.retry_after)
        logger.info(
            "Retrying %s after %s in %.2fs (attempt %d/%d)",
            url, last.reason.value, delay, attempt + 1, attempts,
        )
        time.sleep(delay)

    raise last or errors.ProviderError(errors.Reason.UNKNOWN, "The request failed")


def _decode(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise errors.ProviderError(
            errors.Reason.UNKNOWN, "The provider returned a body that is not JSON"
        ) from None
    return payload if isinstance(payload, dict) else {"data": payload}


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        # Truncated: an HTML error page from a proxy is not a useful message and
        # can be very large.
        return response.text[:500]


def stream_lines(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
    timeout: httpx.Timeout | None = None,
) -> Iterator[str]:
    """Yield the response's lines, raising ``ProviderError`` on a bad status.

    Deliberately **not retried**. Once bytes have reached the caller a retry
    would duplicate them, and a stream that fails before its first byte is
    cheap for the caller to reissue with full knowledge of what it wants to do.
    """
    client = get_client()
    try:
        with client.stream(
            method, url, headers=headers, json=json_body, timeout=timeout or DEFAULT_TIMEOUT
        ) as response:
            if response.status_code >= 400:
                body = response.read()
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = body.decode("utf-8", errors="replace")[:500]
                raise errors.from_response(response.status_code, parsed)

            for line in response.iter_lines():
                if line:
                    yield line
    except httpx.TimeoutException as exc:
        raise errors.ProviderError(errors.Reason.TIMEOUT, str(exc) or "The stream timed out") from None
    except httpx.HTTPError as exc:
        raise errors.ProviderError(
            errors.Reason.UNAVAILABLE, str(exc) or "The provider is unreachable"
        ) from None


def parse_sse_data(lines: Iterator[str]) -> Iterator[dict]:
    """Decode an SSE stream's ``data:`` payloads, stopping at ``[DONE]``.

    Tolerant on purpose: providers differ on whether they send ``event:`` lines,
    whether they pad with comments, and whether they terminate with ``[DONE]``
    at all.
    """
    for line in lines:
        if not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                return
            continue

        try:
            decoded = json.loads(payload)
        except ValueError:
            logger.debug("Skipping an undecodable stream payload")
            continue

        if isinstance(decoded, dict):
            yield decoded
