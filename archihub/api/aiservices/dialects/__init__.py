"""Provider adapters, organised by **wire protocol** rather than by vendor.

THIS IS THE CENTRAL DESIGN DECISION, and it is what the legacy module got
wrong. It had one Python class per vendor — `OpenAIProvider`, `GoogleProvider`,
`OpenRouterProvider`, `AzureProvider`, `OllamaProvider`, `LlamaServerProvider` —
roughly 1,000 lines in which five of the six spoke *the same protocol* and
differed only in a base URL, a header, and which hardcoded table of model names
they consulted. Adding a vendor meant writing a class. Adding a model meant
editing a dict in source and shipping a release.

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
SDK. The legacy code depended on `aisuite`, `openai` **and** raw `httpx`, and
already bypassed `aisuite` whenever tools were involved — which is the codebase
telling you the abstraction was not earning its dependency. One HTTP path means
one place for timeouts, retries, streaming and error mapping.
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
