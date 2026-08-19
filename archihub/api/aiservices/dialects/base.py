"""The contract every dialect implements, and the shapes they return.

A dialect answers three questions about one endpoint: *what models are there*,
*how do I ask one something*, and *how do I read the answer as it arrives*.
Everything above this layer — the catalogue, capability resolution, retries,
context recovery, conversations — is written once against these types and does
not know which vendor it is talking to.

**Capabilities are reported, never assumed.** ``ModelInfo.capabilities`` holds
only what the provider actually told us. Where a provider says nothing, the
field is empty and the resolver above (``capabilities.py``) decides what to do
about it — it does not become a guess made in an adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Things a model may be able to do. A closed vocabulary so callers can branch
#: on it; providers' own vocabularies are mapped onto this by each adapter.
CAPABILITIES = ("chat", "image", "audio", "video", "tools", "embedding", "reasoning")


@dataclass(frozen=True)
class ModelInfo:
    """One model, as its provider describes it.

    ``context_length`` and ``capabilities`` are ``None``/empty when the provider
    does not say — which is the honest answer for endpoints whose ``/models``
    returns nothing but identifiers. The legacy code filled that silence with a
    hardcoded table that was wrong the moment a vendor shipped anything.
    """

    id: str
    name: str
    #: Total context window in tokens, when the provider reports one.
    context_length: int | None = None
    #: Maximum tokens the model may generate, when reported separately.
    max_output_tokens: int | None = None
    #: Subset of :data:`CAPABILITIES` the provider declared.
    capabilities: tuple[str, ...] = ()
    #: Anything else the provider volunteered, kept for display and debugging.
    metadata: dict[str, Any] = field(default_factory=dict)

    def merged_with(self, overrides: dict) -> "ModelInfo":
        """This model with operator-supplied overrides applied."""
        if not overrides:
            return self
        capabilities = overrides.get("capabilities")
        return ModelInfo(
            id=self.id,
            name=overrides.get("name") or self.name,
            context_length=overrides.get("context_length") or self.context_length,
            max_output_tokens=overrides.get("max_output_tokens") or self.max_output_tokens,
            capabilities=tuple(capabilities) if capabilities else self.capabilities,
            metadata={**self.metadata, **(overrides.get("metadata") or {})},
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "context_length": self.context_length,
            "max_output_tokens": self.max_output_tokens,
            "capabilities": list(self.capabilities),
            "metadata": self.metadata,
        }


@dataclass
class ChatResult:
    """A completed, non-streamed answer."""

    content: str
    #: Tool calls the model asked for, in OpenAI's shape.
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    #: ``{"prompt": int, "completion": int, "total": int}`` where reported.
    usage: dict[str, int] = field(default_factory=dict)
    model: str | None = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "content": self.content,
            "tool_calls": self.tool_calls,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "model": self.model,
        }


@dataclass
class ChatChunk:
    """One increment of a streamed answer.

    ``delta`` is the new text only. Reassembling the whole answer is the
    caller's business, and it is the caller that knows whether it wants to.
    """

    delta: str = ""
    #: The model's own reasoning, where a provider streams it separately from
    #: the answer. Kept apart from ``delta`` rather than concatenated: it is not
    #: part of the reply, and the assistant renders it as a collapsible
    #: breakdown of what the model did rather than as text the user reads.
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class Dialect(Protocol):
    """What every adapter provides.

    Implementations are constructed with a resolved provider record and are
    cheap to build — one per request is fine; connection reuse belongs to the
    shared HTTP client, not to the adapter.
    """

    #: Stable identifier stored on provider records.
    name: str
    #: Shown when an operator is choosing a dialect.
    label: str
    #: Whether this dialect needs a base URL, or has a fixed one.
    requires_base_url: bool

    def list_models(self) -> list[ModelInfo]:
        """Every model this endpoint offers, as it describes them."""

    def chat(self, messages: list[dict], **options) -> ChatResult:
        """One completed answer."""

    def stream(self, messages: list[dict], **options) -> Iterator[ChatChunk]:
        """The answer as it arrives."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def normalise_capabilities(values) -> tuple[str, ...]:
    """Map a provider's capability vocabulary onto ours, dropping what we lack.

    Providers use their own words — ``"vision"``, ``"image"``, ``"multimodal"``,
    ``"function_calling"``. Unknown values are dropped rather than passed
    through, so callers can branch on a closed set.
    """
    synonyms = {
        "vision": "image",
        "images": "image",
        "image_input": "image",
        "multimodal": "image",
        "text": "chat",
        "completion": "chat",
        "function_calling": "tools",
        "functions": "tools",
        "tool_use": "tools",
        "tool-calling": "tools",
        "embeddings": "embedding",
        "embed": "embedding",
        "thinking": "reasoning",
        "audio_input": "audio",
        "speech": "audio",
    }

    resolved = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        key = value.strip().lower()
        mapped = synonyms.get(key, key)
        if mapped in CAPABILITIES and mapped not in resolved:
            resolved.append(mapped)

    return tuple(resolved)


def first_int(*values) -> int | None:
    """The first value that is a usable positive integer."""
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if number > 0:
            return number
    return None
