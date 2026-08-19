"""What went wrong with a model call, classified so callers can act on it.

The tempting implementation decides what an error meant by looking for
substrings in its message::

    _CONTEXT_TOKEN_ERROR_MARKERS = (
        "context_length_exceeded", "maximum context length", ...
    )

That is guessing. Every provider words its errors differently, wording changes
between versions, and it is locale-dependent — an error returned in another
language matches nothing. It also conflates unrelated failures, which is why
such lists end up needing clauses like "unless it also mentions rate limit":
the phrase "token limit" appears in both.

Here the classification reads **structured signals** in order of reliability:

1. the HTTP status code,
2. the provider's own machine-readable ``error.code`` / ``error.type``,
3. and only then, as a last resort, message text — against a table that lives in
   data (``rules.json``) rather than in code, so an operator can extend it for a
   provider we have not seen without a release.

A failure nothing recognises is ``UNKNOWN``, and callers treat that as
non-retryable. Guessing wrong in the retryable direction turns one bad request
into five.
"""

from __future__ import annotations

import enum
import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class Reason(str, enum.Enum):
    """Why a call failed, in terms a caller can act on."""

    #: The conversation is longer than the model's context window. Recoverable
    #: by compressing history and retrying — the only reason that is.
    CONTEXT_LENGTH = "context_length"
    #: Too many requests. Retry after a delay.
    RATE_LIMITED = "rate_limited"
    #: The credential is missing, wrong, or lacks entitlement.
    AUTH = "auth"
    #: The provider does not have that model, or the caller may not use it.
    MODEL_NOT_FOUND = "model_not_found"
    #: The request is malformed or asks for something the model cannot do.
    INVALID_REQUEST = "invalid_request"
    #: The provider refused on content-policy grounds.
    CONTENT_FILTERED = "content_filtered"
    #: The provider is down, overloaded, or unreachable. Retry.
    UNAVAILABLE = "unavailable"
    #: The call took too long. Retry.
    TIMEOUT = "timeout"
    #: Nothing matched. Not retried.
    UNKNOWN = "unknown"


#: Reasons a retry could plausibly succeed without changing the request.
RETRYABLE = frozenset({Reason.RATE_LIMITED, Reason.UNAVAILABLE, Reason.TIMEOUT})

#: The one reason the caller can fix by changing the request, by sending less.
RECOVERABLE_BY_SHRINKING = frozenset({Reason.CONTEXT_LENGTH})

#: HTTP status → reason. The most reliable signal, and provider-independent.
STATUS_REASONS = {
    400: Reason.INVALID_REQUEST,
    401: Reason.AUTH,
    403: Reason.AUTH,
    404: Reason.MODEL_NOT_FOUND,
    408: Reason.TIMEOUT,
    413: Reason.CONTEXT_LENGTH,
    422: Reason.INVALID_REQUEST,
    429: Reason.RATE_LIMITED,
    500: Reason.UNAVAILABLE,
    502: Reason.UNAVAILABLE,
    503: Reason.UNAVAILABLE,
    504: Reason.TIMEOUT,
    529: Reason.UNAVAILABLE,
}


class ProviderError(Exception):
    """A model call that failed, with the reason and the facts behind it."""

    def __init__(
        self,
        reason: Reason,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.provider_code = provider_code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.reason in RETRYABLE

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"ProviderError({self.reason.value}, status={self.status_code}, code={self.provider_code!r})"


@lru_cache(maxsize=1)
def _rules() -> dict:
    """Classification rules that live in data, not code.

    Held in ``rules.json`` beside this module so a provider whose error codes we
    have not seen can be taught to the classifier without a code change.
    """
    path = Path(__file__).with_name("rules.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read the provider error rules; falling back to status codes only")
        return {"codes": {}, "phrases": {}}


def reset_rules_cache() -> None:
    _rules.cache_clear()


def classify(
    *,
    status_code: int | None = None,
    provider_code: str | None = None,
    message: str = "",
) -> Reason:
    """The reason behind a failure, from the most reliable signal available."""
    rules = _rules()

    # 1. The provider's own machine-readable code. More specific than the status
    #    (a 400 carrying `context_length_exceeded` is not a generic bad request).
    if provider_code:
        mapped = rules.get("codes", {}).get(str(provider_code).lower())
        if mapped:
            return Reason(mapped)

    # 2. The HTTP status.
    if status_code is not None:
        reason = STATUS_REASONS.get(status_code)
        if reason is not None:
            # A 400 is the one status providers overload, so let the phrase table
            # refine it when it can.
            if reason is Reason.INVALID_REQUEST and message:
                refined = _from_phrases(rules, message)
                if refined is not None:
                    return refined
            return reason
        if 500 <= status_code < 600:
            return Reason.UNAVAILABLE

    # 3. Last resort: the message text.
    return _from_phrases(rules, message) or Reason.UNKNOWN


def _from_phrases(rules: dict, message: str) -> Reason | None:
    """Match against the phrase table, most specific reason first.

    Order matters and is taken from the data file rather than dict iteration:
    "token limit" appears in both context-window and rate-limit messages, and
    the legacy code needed a hand-written exception for exactly that clash.
    """
    text = (message or "").lower()
    if not text:
        return None

    for entry in rules.get("phrases", []):
        if any(phrase in text for phrase in entry.get("any", [])):
            if any(phrase in text for phrase in entry.get("unless", [])):
                continue
            return Reason(entry["reason"])
    return None


def from_response(status_code: int, body: object, *, message: str = "") -> ProviderError:
    """Build an error from a provider's HTTP response.

    Understands the shapes providers actually return: OpenAI-compatible
    ``{"error": {"message", "code", "type"}}``, Anthropic's ``{"type": "error",
    "error": {...}}``, Ollama's bare ``{"error": "..."}``, and a plain string.
    """
    detail, code = _unpack(body)
    text = message or detail or f"HTTP {status_code}"
    reason = classify(status_code=status_code, provider_code=code, message=text)
    return ProviderError(reason, text, status_code=status_code, provider_code=code)


def _unpack(body: object) -> tuple[str, str | None]:
    """``(message, code)`` from whatever shape the provider used."""
    if isinstance(body, str):
        return body, None
    if not isinstance(body, dict):
        return "", None

    error = body.get("error", body)
    if isinstance(error, str):
        return error, None
    if not isinstance(error, dict):
        return "", None

    message = error.get("message") or error.get("detail") or ""
    code = error.get("code") or error.get("type") or error.get("status")
    return (message if isinstance(message, str) else str(message)), (
        str(code) if code is not None else None
    )
