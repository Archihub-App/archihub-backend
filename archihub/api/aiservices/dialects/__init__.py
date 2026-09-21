"""Provider adapters, organised by **wire protocol** rather than by vendor.

THIS IS THE CENTRAL DESIGN DECISION. A class per vendor collapses under its own
weight: most vendors speak *the same protocol* and differ only in a base URL, a
header and a credential, so the classes end up near-identical and drift apart
anyway. Worse, it makes adding a vendor a code change and adding a model a
release.

Here there are two independent axes:

* a **dialect** is a wire protocol — how you frame a chat request, how you read
  a stream, how you ask what models exist. There are only a handful in the
  world, and they change on the timescale of years. These are code.
* a **provider** is an endpoint that speaks one — a base URL, a credential, some
  headers, a display name. There are unboundedly many and new ones appear
  monthly. **These are data**, stored in Mongo and managed through the API.

So OpenAI, OpenRouter, Groq, Together, DeepSeek, Mistral, xAI, Fireworks,
Cerebras, vLLM, LM Studio, llama.cpp, Azure AI and Google's compatibility
endpoint are all *the same dialect* with different rows. Supporting a new one is
a configuration change, not a release. That is what "no hardcoded parts" has to
mean to be worth anything.

Everything speaks HTTP through `httpx` directly rather than through a vendor
SDK. One HTTP path means one place for timeouts, retries, streaming and error
mapping - and an SDK that has to be bypassed for tool calls is not earning the
dependency it costs.
"""

from __future__ import annotations

from archihub.api.aiservices.dialects.base import ChatChunk, ChatResult, Dialect, ModelInfo
from archihub.api.aiservices.dialects.anthropic import AnthropicDialect
from archihub.api.aiservices.dialects.google import GoogleDialect
from archihub.api.aiservices.dialects.ollama import OllamaDialect
from archihub.api.aiservices.dialects.openai_compat import OpenAICompatibleDialect

#: The protocols we can speak. Keyed by the identifier stored on a provider
#: record, so this is the one place a name is fixed in code — and it names a
#: protocol, not a company.
DIALECTS: dict[str, type[Dialect]] = {
    OpenAICompatibleDialect.name: OpenAICompatibleDialect,
    OllamaDialect.name: OllamaDialect,
    GoogleDialect.name: GoogleDialect,
    AnthropicDialect.name: AnthropicDialect,
}

#: What a provider record may declare as its dialect.
DIALECT_NAMES = tuple(DIALECTS)

__all__ = [
    "ChatChunk",
    "ChatResult",
    "DIALECTS",
    "DIALECT_NAMES",
    "Dialect",
    "ModelInfo",
    "get_dialect",
]


def get_dialect(name: str) -> type[Dialect]:
    """The adapter for a dialect name, or a clear failure naming what exists."""
    try:
        return DIALECTS[name]
    except KeyError:
        raise KeyError(
            f"Unknown dialect {name!r}; this build speaks {', '.join(sorted(DIALECTS))}"
        ) from None
