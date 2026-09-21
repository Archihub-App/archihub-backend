"""Anthropic's Messages API.

One adapter of about a hundred lines, and every *deployment* of it — Anthropic
direct, Bedrock-style proxies, gateways — is a row in a collection rather than
another class.

The wire format differs enough to need its own adapter: the system prompt is a
top-level field rather than a message, ``max_tokens`` is required rather than
optional, images are inline base64 blocks, and the stream is a typed event
sequence rather than a series of choice deltas.
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

#: Required by the API on every request.
API_VERSION = "2023-06-01"

#: The Messages API requires `max_tokens`. Used only when the caller gives none,
#: so that a missing option is not a hard failure; any real caller sets it.
FALLBACK_MAX_TOKENS = 4096

#: Extended thinking's token allowance, when the caller asks for it but does not
#: say how much. The API requires `max_tokens` to exceed this, so `_body` grows
#: `max_tokens` to make room rather than sending a request the provider refuses.
THINKING_BUDGET_TOKENS = 4096

#: Anthropic's own server-side tool - the model issues the search itself, mid
#: turn, rather than a function call this backend would have to execute.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


class AnthropicDialect:
    """Chat over the Anthropic Messages API."""

    name = "anthropic"
    label = "Anthropic (Messages)"
    requires_base_url = False

    default_base_url = "https://api.anthropic.com/v1"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None,
                 headers: dict | None = None, **_ignored) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.extra_headers = headers or {}

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": API_VERSION,
            **self.extra_headers,
        }
        if self.api_key:
            headers.setdefault("x-api-key", self.api_key)
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    # -- discovery --------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        payload = transport.request_json(
            "GET", self._url("models?limit=1000"), headers=self._headers(),
            timeout=transport.DISCOVERY_TIMEOUT, attempts=2,
        )

        models = []
        for entry in payload.get("data") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            models.append(
                ModelInfo(
                    id=str(entry["id"]),
                    name=str(entry.get("display_name") or entry["id"]),
                    context_length=first_int(entry.get("context_window")),
                    max_output_tokens=first_int(entry.get("max_output_tokens")),
                    # The listing does not describe capabilities, so nothing is
                    # claimed. Operators can supply overrides; a guess made here
                    # would be indistinguishable from fact downstream.
                    capabilities=normalise_capabilities(entry.get("capabilities")),
                    metadata={"created_at": entry.get("created_at"), "type": entry.get("type")},
                )
            )
        return sorted(models, key=lambda m: m.id)

    # -- calling ----------------------------------------------------------

    def _body(self, messages: list[dict], options: dict, *, stream: bool) -> dict:
        system, turns = [], []
        for message in messages:
            role = message.get("role", "user")
            if role == "system":
                system.append({"type": "text", "text": _flatten(message.get("content"))})
                continue
            turns.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": _blocks(message.get("content")),
            })

        max_tokens = options.get("max_tokens") or FALLBACK_MAX_TOKENS
        thinking_on = bool(options.get("thinking"))
        if thinking_on:
            # `max_tokens` must exceed the thinking budget or the API refuses
            # the request outright.
            max_tokens = max(max_tokens, THINKING_BUDGET_TOKENS + FALLBACK_MAX_TOKENS)

        body: dict = {
            "model": options["model"],
            "messages": turns,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if thinking_on:
            body["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
            # Extended thinking requires `temperature` at its default (1) and
            # `top_p`/`top_k` unset - sending either is a request the API
            # refuses, not a value it adjusts for you.
        else:
            for ours, theirs in (("temperature", "temperature"), ("top_p", "top_p")):
                if options.get(ours) is not None:
                    body[theirs] = options[ours]
        if options.get("stop") is not None:
            body["stop_sequences"] = options["stop"]

        tools = list(options.get("tools") or [])
        if options.get("web_search"):
            tools.append(WEB_SEARCH_TOOL)
        if tools:
            body["tools"] = tools
        return body

    def chat(self, messages: list[dict], **options) -> ChatResult:
        payload = transport.request_json(
            "POST", self._url("messages"), headers=self._headers(),
            json_body=self._body(messages, options, stream=False),
        )

        text, tool_calls = [], []
        for block in payload.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {"name": block.get("name"), "arguments": block.get("input")},
                })

        return ChatResult(
            content="".join(text),
            tool_calls=tool_calls,
            finish_reason=payload.get("stop_reason"),
            usage=_usage(payload.get("usage")),
            model=payload.get("model"),
            raw=payload,
        )

    def stream(self, messages: list[dict], **options) -> Iterator[ChatChunk]:
        lines = transport.stream_lines(
            "POST", self._url("messages"), headers=self._headers(),
            json_body=self._body(messages, options, stream=True),
        )

        for payload in transport.parse_sse_data(lines):
            kind = payload.get("type")

            if kind == "content_block_delta":
                delta = payload.get("delta") or {}
                if delta.get("type") in ("text_delta", None):
                    yield ChatChunk(delta=delta.get("text") or "")
                elif delta.get("type") == "thinking_delta":
                    yield ChatChunk(reasoning=delta.get("thinking") or "")
            elif kind == "message_delta":
                yield ChatChunk(
                    finish_reason=(payload.get("delta") or {}).get("stop_reason"),
                    usage=_usage(payload.get("usage")),
                )
            elif kind == "error":
                from archihub.api.aiservices import errors

                detail, code = errors._unpack(payload)
                raise errors.ProviderError(
                    errors.classify(provider_code=code, message=detail),
                    detail or "The provider reported an error mid-stream",
                    provider_code=code,
                )


def _flatten(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text") or "")
    return "".join(parts)


def _blocks(content) -> list[dict]:
    """Message content as Anthropic content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]

    blocks = []
    for item in content or []:
        if isinstance(item, str):
            blocks.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            if item.get("type") == "text":
                blocks.append({"type": "text", "text": item.get("text") or ""})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    header, _, data = url.partition(",")
                    media_type = header[5:].split(";")[0] or "image/jpeg"
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    })
            elif item.get("type") == "document_url":
                url = (item.get("document_url") or {}).get("url", "")
                if url.startswith("data:"):
                    header, _, data = url.partition(",")
                    media_type = header[5:].split(";")[0] or "application/pdf"
                    blocks.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    })
    return blocks


def _usage(usage) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    resolved = {}
    prompt = first_int(usage.get("input_tokens"))
    completion = first_int(usage.get("output_tokens"))
    if prompt is not None:
        resolved["prompt"] = prompt
    if completion is not None:
        resolved["completion"] = completion
    if prompt is not None and completion is not None:
        resolved["total"] = prompt + completion
    return resolved
