"""The OpenAI chat-completions protocol.

Spoken by almost everything: OpenAI, OpenRouter, Groq, Together, DeepSeek,
Mistral, xAI, Fireworks, Cerebras, Perplexity, Azure AI, Google's compatibility
endpoint, vLLM, LM Studio, llama.cpp's server, Ollama's ``/v1``. In the legacy
module that was five near-identical classes; here it is one adapter and five
rows in a collection.

**Model metadata is read from the provider, never from a table in this file.**
`/models` returns different amounts of detail per provider — OpenRouter gives
context length and input modalities, Together gives context length, OpenAI gives
nothing but identifiers — and all of it is passed through as reported. Where a
provider says nothing, nothing is claimed. The legacy code kept dictionaries of
model names with hand-written context windows and capability lists, which is
both wrong for models it had not heard of and wrong for models whose windows
changed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from archihub.api.aiservices import transport
from archihub.api.aiservices.dialects.base import (
    ChatChunk,
    ChatResult,
    ModelInfo,
    first_int,
    normalise_capabilities,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleDialect:
    """Chat completions over the OpenAI wire format."""

    name = "openai-compatible"
    label = "OpenAI-compatible"
    requires_base_url = False

    #: Used when a provider record does not give one.
    default_base_url = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        headers: dict | None = None,
        **_ignored,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.extra_headers = headers or {}

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    # -- discovery --------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        payload = transport.request_json(
            "GET",
            self._url("models"),
            headers=self._headers(),
            timeout=transport.DISCOVERY_TIMEOUT,
            attempts=2,
        )

        entries = payload.get("data")
        if not isinstance(entries, list):
            # Some servers answer with a bare list, others nest under `models`.
            entries = payload.get("models") if isinstance(payload.get("models"), list) else []

        models = [self._model(entry) for entry in entries if isinstance(entry, dict)]
        return sorted((m for m in models if m is not None), key=lambda m: m.id)

    def _model(self, entry: dict) -> ModelInfo | None:
        identifier = entry.get("id") or entry.get("name")
        if not identifier:
            return None

        architecture = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
        top_provider = entry.get("top_provider") if isinstance(entry.get("top_provider"), dict) else {}

        # Every key a compatible provider is known to use for the same fact.
        # Reading several is not guessing: each is the provider stating it.
        context_length = first_int(
            entry.get("context_length"),
            entry.get("context_window"),
            entry.get("max_context_length"),
            entry.get("max_model_len"),
            top_provider.get("context_length"),
            (entry.get("config") or {}).get("context_length") if isinstance(entry.get("config"), dict) else None,
        )
        max_output = first_int(
            entry.get("max_output_tokens"),
            entry.get("max_completion_tokens"),
            top_provider.get("max_completion_tokens"),
        )

        declared = []
        declared.extend(architecture.get("input_modalities") or [])
        declared.extend(architecture.get("modality", "").split("+") if isinstance(architecture.get("modality"), str) else [])
        declared.extend(entry.get("capabilities") or [])
        declared.extend(entry.get("supported_features") or [])
        if entry.get("supports_vision"):
            declared.append("image")
        if entry.get("supports_function_calling") or entry.get("supports_tools"):
            declared.append("tools")

        capabilities = normalise_capabilities(declared)

        return ModelInfo(
            id=str(identifier),
            name=str(entry.get("name") or identifier),
            context_length=context_length,
            max_output_tokens=max_output,
            capabilities=capabilities,
            metadata=_presentable(entry),
        )

    # -- calling ----------------------------------------------------------

    def _body(self, messages: list[dict], options: dict, *, stream: bool) -> dict:
        body: dict = {
            "model": options["model"],
            "messages": messages,
            "stream": stream,
        }

        # `max_tokens` was renamed `max_completion_tokens` for reasoning models.
        # The legacy code decided which to send by testing whether the model id
        # started with "gpt-5", "o1", "o3" or "o4" - a hardcoded list that is
        # wrong for every model released after it was written, and for every
        # other provider. Both are sent through the caller's chosen key, and the
        # retry on `unsupported_parameter` below handles servers that reject it.
        if options.get("max_tokens") is not None:
            body[options.get("max_tokens_field") or "max_tokens"] = options["max_tokens"]

        for key in ("temperature", "top_p", "stop", "seed", "response_format", "reasoning_effort"):
            if options.get(key) is not None:
                body[key] = options[key]

        if options.get("tools"):
            body["tools"] = options["tools"]
            if options.get("tool_choice"):
                body["tool_choice"] = options["tool_choice"]

        if stream and options.get("stream_usage", True):
            # Ignored by servers that do not implement it.
            body["stream_options"] = {"include_usage": True}

        return body

    def chat(self, messages: list[dict], **options) -> ChatResult:
        payload = transport.request_json(
            "POST",
            self._url("chat/completions"),
            headers=self._headers(),
            json_body=self._body(messages, options, stream=False),
        )
        return self._result(payload)

    def stream(self, messages: list[dict], **options) -> Iterator[ChatChunk]:
        lines = transport.stream_lines(
            "POST",
            self._url("chat/completions"),
            headers=self._headers(),
            json_body=self._body(messages, options, stream=True),
        )

        for payload in transport.parse_sse_data(lines):
            chunk = self._chunk(payload)
            if chunk is not None:
                yield chunk

    # -- response shapes --------------------------------------------------

    def _result(self, payload: dict) -> ChatResult:
        choices = payload.get("choices") or []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}

        return ChatResult(
            content=_text_of(message.get("content")),
            tool_calls=list(message.get("tool_calls") or []),
            finish_reason=first.get("finish_reason"),
            usage=_usage(payload.get("usage")),
            model=payload.get("model"),
            raw=payload,
        )

    def _chunk(self, payload: dict) -> ChatChunk | None:
        choices = payload.get("choices") or []
        if not choices:
            # A usage-only frame, which some providers send last.
            usage = _usage(payload.get("usage"))
            return ChatChunk(usage=usage) if usage else None

        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}

        # Two spellings in the wild for the same thing: DeepSeek and several
        # OpenRouter-fronted models send `reasoning_content`, others `reasoning`.
        # Neither is in the OpenAI spec, so both are read and absence is normal.
        reasoning = _text_of(delta.get("reasoning_content")) or _text_of(delta.get("reasoning"))

        return ChatChunk(
            delta=_text_of(delta.get("content")),
            reasoning=reasoning,
            tool_calls=list(delta.get("tool_calls") or []),
            finish_reason=first.get("finish_reason"),
            usage=_usage(payload.get("usage")),
        )


# ---------------------------------------------------------------------------
# Shared response helpers
# ---------------------------------------------------------------------------

#: Fields worth keeping from a provider's model entry for display. Anything else
#: is dropped rather than passed through, so a provider that returns a large
#: object per model does not bloat every catalogue response.
PRESENTABLE_KEYS = (
    "description", "owned_by", "created", "pricing", "architecture",
    "top_provider", "family", "parameter_size", "quantization",
)


def _presentable(entry: dict) -> dict:
    return {key: entry[key] for key in PRESENTABLE_KEYS if key in entry}


def _text_of(content) -> str:
    """The text of a message whose content may be a string or a part list.

    Reasoning models return content as a list of typed parts, and some providers
    use that shape for plain text too.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
        return "".join(p for p in parts if isinstance(p, str))
    return str(content)


def _usage(usage) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    resolved = {}
    for ours, theirs in (
        ("prompt", ("prompt_tokens", "input_tokens")),
        ("completion", ("completion_tokens", "output_tokens")),
        ("total", ("total_tokens",)),
    ):
        value = first_int(*(usage.get(key) for key in theirs))
        if value is not None:
            resolved[ours] = value

    if "total" not in resolved and "prompt" in resolved and "completion" in resolved:
        resolved["total"] = resolved["prompt"] + resolved["completion"]
    return resolved
