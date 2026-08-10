"""What models exist, discovered from the providers themselves.

**Nothing in this codebase contains a list of model names.** The catalogue is
whatever each configured provider says it offers, asked over the network and
cached briefly. That is the whole point: a table of model names in source is
wrong the day a vendor ships anything, and the legacy module had three of them
(`_OPENAI_META`, `_GOOGLE_META`, and the Ollama family substrings) plus
hardcoded fallback lists for when discovery failed.

Those fallbacks are the part worth dwelling on. When `getModels` raised, the
legacy code returned a hand-written list of models it hoped existed — so a
provider with an expired key, or a typo'd base URL, presented a normal-looking
catalogue of models that could not actually be called. Discovery failure is
reported here, not papered over.

Capability and context data come from the provider where the provider states it
(see each dialect), and otherwise from an **operator-managed overrides
collection** — never from a constant. An operator can record that a model their
gateway exposes supports images; nobody has to ship a release for it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from archihub.api.aiservices import errors
from archihub.api.aiservices.dialects import ModelInfo, get_dialect

logger = logging.getLogger(__name__)

OVERRIDES_COLLECTION = "llm_model_metadata"

#: How long a discovered catalogue is reused. Short enough that a newly pulled
#: Ollama model appears without a restart; long enough that a settings screen
#: does not hammer a paid endpoint.
CACHE_TTL_SECONDS = 300

_cache: dict[str, tuple[list[ModelInfo], float]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Catalogue:
    """The models one provider offers, or why we could not find out."""

    provider_id: str
    models: list[ModelInfo]
    #: Set when discovery failed. The catalogue is then empty and **that is
    #: reported**, rather than replaced with a plausible-looking guess.
    error: str | None = None
    reason: str | None = None
    stale: bool = False

    def as_dict(self) -> dict:
        return {
            "provider": self.provider_id,
            "models": [m.as_dict() for m in self.models],
            "error": self.error,
            "reason": self.reason,
            "stale": self.stale,
        }


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def clear_cache(provider_id: str | None = None) -> None:
    """Forget discovered catalogues, for one provider or all of them."""
    with _lock:
        if provider_id is None:
            _cache.clear()
        else:
            _cache.pop(provider_id, None)


def for_provider(provider: dict, *, refresh: bool = False) -> Catalogue:
    """The models a provider offers.

    On a discovery failure, a previously cached catalogue is returned marked
    ``stale`` rather than discarded — a transient outage should not empty the
    model picker of an archive mid-session — but the failure travels with it so
    the interface can say so.
    """
    provider_id = str(provider.get("id") or provider.get("_id") or "")
    cached = _cached(provider_id) if not refresh else None
    if cached is not None:
        return Catalogue(provider_id, cached)

    try:
        adapter = build_adapter(provider)
        discovered = adapter.list_models()
    except errors.ProviderError as exc:
        logger.warning("Model discovery failed for provider %s: %s", provider_id, exc)
        return _failure(provider_id, str(exc), exc.reason.value)
    except Exception as exc:
        logger.exception("Model discovery failed for provider %s", provider_id)
        return _failure(provider_id, str(exc), errors.Reason.UNKNOWN.value)

    models = apply_overrides(provider_id, discovered)
    with _lock:
        _cache[provider_id] = (models, time.time())
    return Catalogue(provider_id, models)


def _cached(provider_id: str) -> list[ModelInfo] | None:
    with _lock:
        entry = _cache.get(provider_id)
    if entry is None:
        return None
    models, stamped = entry
    return models if time.time() - stamped < CACHE_TTL_SECONDS else None


def _failure(provider_id: str, message: str, reason: str) -> Catalogue:
    with _lock:
        entry = _cache.get(provider_id)
    if entry is not None:
        return Catalogue(provider_id, entry[0], error=message, reason=reason, stale=True)
    return Catalogue(provider_id, [], error=message, reason=reason)


def build_adapter(provider: dict):
    """Construct the dialect adapter for a provider record."""
    from archihub.api.aiservices.providers import decrypt_key

    dialect = get_dialect(provider.get("dialect") or "")
    return dialect(
        api_key=decrypt_key(provider.get("key")),
        base_url=provider.get("base_url"),
        headers=provider.get("headers") or {},
    )


# ---------------------------------------------------------------------------
# Operator-managed metadata
# ---------------------------------------------------------------------------


def apply_overrides(provider_id: str, models: list[ModelInfo]) -> list[ModelInfo]:
    """Merge operator-recorded metadata over what the provider reported.

    The provider is the primary source; an override wins only where it is set,
    because an operator recording "this model handles images" is stating
    something the endpoint did not.
    """
    overrides = load_overrides(provider_id)
    if not overrides:
        return models
    return [model.merged_with(overrides.get(model.id, {})) for model in models]


def load_overrides(provider_id: str) -> dict[str, dict]:
    """Every override for a provider, keyed by model id."""
    try:
        rows = _mongo().get_all_records(
            OVERRIDES_COLLECTION, {"provider": provider_id}
        )
    except Exception:
        logger.warning("Could not read model metadata overrides", exc_info=True)
        return {}

    overrides: dict[str, dict] = {}
    for row in rows:
        model_id = row.get("model")
        if not model_id:
            continue
        overrides[str(model_id)] = {
            key: row[key]
            for key in ("name", "context_length", "max_output_tokens", "capabilities", "metadata")
            if row.get(key) is not None
        }
    return overrides


def set_override(provider_id: str, model_id: str, values: dict, user: str | None = None) -> dict:
    """Record what an operator knows about a model that its provider does not say."""
    import datetime

    permitted = {
        key: values[key]
        for key in ("name", "context_length", "max_output_tokens", "capabilities", "metadata")
        if key in values
    }
    permitted.update(
        {
            "provider": provider_id,
            "model": model_id,
            "updatedBy": user or "system",
            "updatedAt": datetime.datetime.now(datetime.timezone.utc),
        }
    )

    _mongo().upsert_record(
        OVERRIDES_COLLECTION, {"provider": provider_id, "model": model_id}, permitted
    )
    clear_cache(provider_id)

    # Returned JSON-safe: the stored document carries a real datetime, and
    # handing that straight to a JSON response is a 500 after the write has
    # already happened - the worst shape of failure, because the caller is told
    # it did not work when it did.
    return {**permitted, "updatedAt": permitted["updatedAt"].isoformat()}


def find_model(provider: dict, model_id: str) -> ModelInfo | None:
    """One model from a provider's catalogue, or ``None``."""
    for model in for_provider(provider).models:
        if model.id == model_id:
            return model
    return None
