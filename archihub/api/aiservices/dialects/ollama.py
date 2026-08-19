"""Ollama's native API.

Ollama also exposes an OpenAI-compatible surface at ``/v1``, so this adapter
exists for one reason: **the native API tells you what a model can do, and the
compatible one does not.** ``/api/tags`` lists what is pulled locally and
``/api/show`` reports a model's capabilities and context length directly.

That is worth an adapter, because the alternative is:

    _OLLAMA_VISION_FAMILIES = ("llava", "bakllava", "moondream", "minicpm-v",
                               "qwen-vl", "qwen3-vl", "gemma3", "pixtral",
                               "vision", "gemma4")

— guessing a model's capabilities from substrings of its name. That is wrong for
every vision model whose name does not contain one of those strings, wrong for
any model whose name coincidentally does, and needs editing every time somebody
publishes a new one. Ollama has known the answer all along; this asks it.
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


class OllamaDialect:
    """Chat over Ollama's native ``/api`` endpoints."""

    name = "ollama"
    label = "Ollama (native)"
    requires_base_url = True

    default_base_url = "http://localhost:11434"

    def __init__(self, *, base_url: str | None = None, headers: dict | None = None, api_key: str | None = None, **_ignored) -> None:
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.extra_headers = headers or {}
        self.api_key = api_key

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            # Ollama itself takes no key, but it is commonly put behind a proxy
            # that does.
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    # -- discovery --------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        payload = transport.request_json(
            "GET", self._url("api/tags"), headers=self._headers(),
            timeout=transport.DISCOVERY_TIMEOUT, attempts=2,
        )

        models = []
        for entry in payload.get("models") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("model") or entry.get("name")
            if not name:
                continue
            models.append(self._describe(str(name), entry))

        return sorted(models, key=lambda m: m.id)

    def _describe(self, name: str, tag_entry: dict) -> ModelInfo:
        """One model, enriched from ``/api/show`` where that is available.

        A `show` call per model is several round trips against a local daemon,
        which is cheap and cached upstream. If the daemon is too old to report
        capabilities, the fields stay empty — which is the honest answer, and
        the operator can supply overrides.
        """
        details = tag_entry.get("details") if isinstance(tag_entry.get("details"), dict) else {}
        capabilities: tuple[str, ...] = ()
        context_length = None
        info: dict = {}

        try:
            shown = transport.request_json(
                "POST", self._url("api/show"), headers=self._headers(),
                json_body={"model": name}, timeout=transport.DISCOVERY_TIMEOUT, attempts=1,
            )
        except Exception:
            logger.debug("Could not read capabilities for the Ollama model %s", name, exc_info=True)
        else:
            capabilities = normalise_capabilities(shown.get("capabilities"))
            info = shown.get("model_info") if isinstance(shown.get("model_info"), dict) else {}
            # The context-length key is namespaced by architecture
            # (`llama.context_length`, `qwen3.context_length`, ...), so it is
            # found by suffix rather than by a table of architecture names.
            context_length = first_int(
                *(value for key, value in info.items() if key.endswith(".context_length"))
            )

        return ModelInfo(
            id=name,
            name=name,
            context_length=context_length,
            capabilities=capabilities,
            metadata={
                "family": details.get("family"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "size": tag_entry.get("size"),
            },
        )

    # -- calling ----------------------------------------------------------

    def _body(self, messages: list[dict], options: dict, *, stream: bool) -> dict:
        body: dict = {
            "model": options["model"],
            "messages": [_message(m) for m in messages],
            "stream": stream,
        }

        model_options = {}
        if options.get("temperature") is not None:
            model_options["temperature"] = options["temperature"]
        if options.get("max_tokens") is not None:
            model_options["num_predict"] = options["max_tokens"]
        if options.get("seed") is not None:
            model_options["seed"] = options["seed"]
        if model_options:
            body["options"] = model_options

        if options.get("tools"):
            body["tools"] = options["tools"]
        return body

    def chat(self, messages: list[dict], **options) -> ChatResult:
        payload = transport.request_json(
            "POST", self._url("api/chat"), headers=self._headers(),
            json_body=self._body(messages, options, stream=False),
        )
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}

        return ChatResult(
            content=message.get("content") or "",
            tool_calls=list(message.get("tool_calls") or []),
            finish_reason=payload.get("done_reason"),
            usage=_usage(payload),
            model=payload.get("model"),
            raw=payload,
        )

    def stream(self, messages: list[dict], **options) -> Iterator[ChatChunk]:
        """Ollama streams newline-delimited JSON, not SSE."""
        import json

        lines = transport.stream_lines(
            "POST", self._url("api/chat"), headers=self._headers(),
            json_body=self._body(messages, options, stream=True),
        )

        for line in lines:
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue

            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            yield ChatChunk(
                delta=message.get("content") or "",
                reasoning=message.get("thinking") or "",
                tool_calls=list(message.get("tool_calls") or []),
                finish_reason=payload.get("done_reason") if payload.get("done") else None,
                usage=_usage(payload) if payload.get("done") else {},
            )


def _message(message: dict) -> dict:
    """Translate one message into Ollama's shape.

    Ollama takes images as a separate list of base64 strings rather than inline
    content parts, so a multimodal message is split apart here.
    """
    content = message.get("content")
    if isinstance(content, str):
        return {"role": message.get("role", "user"), "content": content}

    text_parts, images = [], []
    for part in content or []:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "text":
                text_parts.append(part.get("text") or "")
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    images.append(url.split(",", 1)[-1])

    result = {"role": message.get("role", "user"), "content": "".join(text_parts)}
    if images:
        result["images"] = images
    return result


def _usage(payload: dict) -> dict[str, int]:
    resolved = {}
    prompt = first_int(payload.get("prompt_eval_count"))
    completion = first_int(payload.get("eval_count"))
    if prompt is not None:
        resolved["prompt"] = prompt
    if completion is not None:
        resolved["completion"] = completion
    if prompt is not None and completion is not None:
        resolved["total"] = prompt + completion
    return resolved
