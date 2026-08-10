"""Google's native Generative Language API.

Google also publishes an OpenAI-compatible endpoint, and for plain chat the
compatible dialect is the better choice — fewer moving parts. This adapter
exists because the native API is the only one that **lists models with their
real limits**: ``/v1beta/models`` returns ``inputTokenLimit``,
``outputTokenLimit`` and ``supportedGenerationMethods`` for every model, so a
catalogue built from it needs no hardcoded table.

The legacy code kept one anyway — a `_GOOGLE_META` dict naming individual Gemini
and Gemma releases with hand-written token limits — while calling an endpoint
that was already returning those numbers.
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

#: Generation methods that mean "this model can hold a conversation".
CHAT_METHODS = ("generateContent", "streamGenerateContent")


class GoogleDialect:
    """Chat over Google's native ``generateContent`` API."""

    name = "google"
    label = "Google (native)"
    requires_base_url = False

    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None,
                 headers: dict | None = None, **_ignored) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.extra_headers = headers or {}

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            # The header form, rather than a query parameter, so the credential
            # does not end up in access logs or proxy caches.
            headers.setdefault("x-goog-api-key", self.api_key)
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    # -- discovery --------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        page_token = None

        while True:
            url = self._url("models?pageSize=200")
            if page_token:
                url = f"{url}&pageToken={page_token}"

            payload = transport.request_json(
                "GET", url, headers=self._headers(),
                timeout=transport.DISCOVERY_TIMEOUT, attempts=2,
            )

            for entry in payload.get("models") or []:
                model = self._model(entry)
                if model is not None:
                    models.append(model)

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return sorted(models, key=lambda m: m.id)

    def _model(self, entry: dict) -> ModelInfo | None:
        if not isinstance(entry, dict):
            return None
        raw_name = entry.get("name") or ""
        identifier = raw_name.split("/")[-1]
        if not identifier:
            return None

        methods = entry.get("supportedGenerationMethods") or []
        if not any(method in methods for method in CHAT_METHODS):
            # Embedding-only and other non-chat models are still listed, tagged
            # by what they actually do.
            if not any("embed" in str(m).lower() for m in methods):
                return None

        capabilities = list(normalise_capabilities(entry.get("supportedGenerationMethods")))
        if any(method in methods for method in CHAT_METHODS):
            capabilities.append("chat")
        if any("embed" in str(m).lower() for m in methods):
            capabilities.append("embedding")

        return ModelInfo(
            id=identifier,
            name=entry.get("displayName") or identifier,
            context_length=first_int(entry.get("inputTokenLimit")),
            max_output_tokens=first_int(entry.get("outputTokenLimit")),
            capabilities=tuple(dict.fromkeys(capabilities)),
            metadata={
                "description": entry.get("description"),
                "version": entry.get("version"),
                "methods": methods,
            },
        )

    # -- calling ----------------------------------------------------------

    def _body(self, messages: list[dict], options: dict) -> dict:
        contents, system = [], []

        for message in messages:
            role = message.get("role", "user")
            parts = _parts(message.get("content"))
            if not parts:
                continue
            if role == "system":
                system.extend(parts)
                continue
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})

        body: dict = {"contents": contents}
        if system:
            # A first-class field, not a fake user turn - which is what the
            # legacy converter did, leaving the instruction visible as dialogue.
            body["systemInstruction"] = {"parts": system}

        config = {}
        if options.get("temperature") is not None:
            config["temperature"] = options["temperature"]
        if options.get("max_tokens") is not None:
            config["maxOutputTokens"] = options["max_tokens"]
        if options.get("top_p") is not None:
            config["topP"] = options["top_p"]
        if config:
            body["generationConfig"] = config

        return body

    def chat(self, messages: list[dict], **options) -> ChatResult:
        payload = transport.request_json(
            "POST",
            self._url(f"models/{options['model']}:generateContent"),
            headers=self._headers(),
            json_body=self._body(messages, options),
        )
        candidate = (payload.get("candidates") or [{}])[0]

        return ChatResult(
            content=_text(candidate),
            finish_reason=candidate.get("finishReason"),
            usage=_usage(payload.get("usageMetadata")),
            model=options.get("model"),
            raw=payload,
        )

    def stream(self, messages: list[dict], **options) -> Iterator[ChatChunk]:
        lines = transport.stream_lines(
            "POST",
            self._url(f"models/{options['model']}:streamGenerateContent?alt=sse"),
            headers=self._headers(),
            json_body=self._body(messages, options),
        )

        for payload in transport.parse_sse_data(lines):
            candidate = (payload.get("candidates") or [{}])[0]
            yield ChatChunk(
                delta=_text(candidate),
                finish_reason=candidate.get("finishReason"),
                usage=_usage(payload.get("usageMetadata")),
            )


def _parts(content) -> list[dict]:
    """Message content as Google's parts, keeping inline images inline."""
    if isinstance(content, str):
        return [{"text": content}] if content else []

    parts = []
    for item in content or []:
        if isinstance(item, str):
            parts.append({"text": item})
        elif isinstance(item, dict):
            if item.get("type") == "text":
                parts.append({"text": item.get("text") or ""})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    header, _, data = url.partition(",")
                    media_type = header[5:].split(";")[0] or "image/jpeg"
                    parts.append({"inlineData": {"mimeType": media_type, "data": data}})
    return parts


def _text(candidate: dict) -> str:
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(part.get("text") or "" for part in parts if isinstance(part, dict))


def _usage(metadata) -> dict[str, int]:
    if not isinstance(metadata, dict):
        return {}
    resolved = {}
    for ours, theirs in (
        ("prompt", "promptTokenCount"),
        ("completion", "candidatesTokenCount"),
        ("total", "totalTokenCount"),
    ):
        value = first_int(metadata.get(theirs))
        if value is not None:
            resolved[ours] = value
    return resolved
