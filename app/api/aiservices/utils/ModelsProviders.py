import aisuite as ai
from .BaseLLMProvider import BaseLLMProvider
import mimetypes
import base64
import os
import time
import httpx

GOOGLE_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_MODEL_CACHE: dict = {}
_CACHE_TTL = 300  # seconds


def _get_cached(key: str, fetch_fn):
    now = time.time()
    if key in _MODEL_CACHE:
        data, ts = _MODEL_CACHE[key]
        if now - ts < _CACHE_TTL:
            return data
    data = fetch_fn()
    _MODEL_CACHE[key] = (data, now)
    return data


_OPENAI_META: dict = {
    "gpt-4o":           {"max_tokens": 128000, "capabilities": ["chat", "image"]},
    "gpt-4o-mini":      {"max_tokens": 128000, "capabilities": ["chat", "image"]},
    "gpt-4.1":          {"max_tokens": 1047576, "capabilities": ["chat", "image"]},
    "gpt-4.1-mini":     {"max_tokens": 1047576, "capabilities": ["chat", "image"]},
    "gpt-4.1-nano":     {"max_tokens": 1047576, "capabilities": ["chat", "image"]},
    "gpt-5":            {"max_tokens": 1047576, "capabilities": ["chat", "image"]},
    "gpt-5-mini":       {"max_tokens": 1047576, "capabilities": ["chat", "image"]},
    "gpt-5-nano":       {"max_tokens": 1047576, "capabilities": ["chat", "image"]},
    "o4-mini":          {"max_tokens": 200000,  "capabilities": ["chat", "image"]},
    "o3":               {"max_tokens": 200000,  "capabilities": ["chat", "image"]},
    "o3-mini":          {"max_tokens": 200000,  "capabilities": ["chat"]},
    "gpt-3.5-turbo":    {"max_tokens": 16384,   "capabilities": ["chat"]},
    # Azure AI-served OpenAI models available through the OpenAI org
    "gpt-oss:20b":      {"max_tokens": 100000,  "capabilities": ["chat"]},
    "gpt-oss:120b":     {"max_tokens": 100000,  "capabilities": ["chat"]},
}

_GOOGLE_META: dict = {
    "gemini-2.0-flash":      {"max_tokens": 8192,  "capabilities": ["chat", "image"]},
    "gemini-2.0-flash-lite": {"max_tokens": 8192,  "capabilities": ["chat", "image"]},
    "gemini-2.5-flash-lite": {"max_tokens": 65536, "capabilities": ["chat", "image"]},
    "gemini-2.5-flash":      {"max_tokens": 65536, "capabilities": ["chat", "image"]},
    "gemini-2.5-pro":        {"max_tokens": 65536, "capabilities": ["chat", "image"]},
}

# Families / substrings that imply vision support in Ollama model names
_OLLAMA_VISION_FAMILIES = (
    "llava", "bakllava", "moondream", "minicpm-v", "qwen-vl", "qwen3-vl",
    "gemma3", "pixtral", "vision",
)
# Substrings that imply embedding models
_OLLAMA_EMBED_FAMILIES = (
    "embed", "nomic-embed", "mxbai-embed", "all-minilm",
    "snowflake-arctic-embed",
)


llm_providers = [
    "OpenAI",
    "Google",
    "Azure",
    "Ollama",
    "LlamaServer"
]

def get_llm_providers():
    return llm_providers


def _preprocess_messages_openai_compat(messages, process_image_fn):
    """Convert messages with image_path items to OpenAI image_url base64 format."""
    processed = []
    for msg in messages:
        if isinstance(msg['content'], str):
            processed.append(msg)
        elif isinstance(msg['content'], list):
            content_parts = []
            for item in msg['content']:
                if item['type'] == 'text':
                    content_parts.append({"type": "text", "text": item['text']})
                elif item['type'] == 'image_path':
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": process_image_fn(item['path'])}
                    })
            processed.append({"role": msg['role'], "content": content_parts})
        else:
            processed.append(msg)
    return processed


def _preprocess_messages_ollama(messages, process_image_fn):
    """Convert messages with image_path items to Ollama's native format (images array)."""
    processed = []
    for msg in messages:
        if isinstance(msg['content'], str):
            processed.append({"role": msg['role'], "content": msg['content']})
        elif isinstance(msg['content'], list):
            text_chunks, image_parts = [], []
            for item in msg['content']:
                if item['type'] == 'text':
                    text_chunks.append(item['text'])
                elif item['type'] == 'image_path':
                    image_parts.append(process_image_fn(item['path']))
            msg_dict = {"role": msg['role'], "content": "\n".join(text_chunks)}
            if image_parts:
                msg_dict["images"] = image_parts
            processed.append(msg_dict)
        else:
            processed.append(msg)
    return processed


def _process_image_to_data_url(image_path):
    """Convert image file to base64 data URL string."""
    with open(image_path, "rb") as f:
        base64_image = base64.b64encode(f.read()).decode('utf-8')
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or not mime_type.startswith('image/'):
        mime_type = 'image/jpeg'
    return f"data:{mime_type};base64,{base64_image}"


def _process_image_to_base64(image_path):
    """Convert image file to raw base64 string (Ollama native format)."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')


def _aisuite_response_to_dict(response):
    """Normalize an aisuite ChatCompletionResponse to the internal dict format."""
    return {
        'choices': [
            {
                'message': {
                    'role': 'assistant',
                    'content': response.choices[0].message.content,
                }
            }
        ]
    }


class AzureProvider(BaseLLMProvider):
    def getModels(self):
        return [
            {
                "id": "gpt-4.1",
                "name": "GPT-4.1",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "gpt-4.1-mini",
                "name": "GPT-4.1 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "gpt-4.1-nano",
                "name": "GPT-4.1 Nano",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "o4-mini",
                "name": "o4 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
                "cognitive_services": True,
            },
            {
                "id": "gpt-35-turbo",
                "name": "GPT-3.5 Turbo",
                "type": "chat",
                "max_tokens": 16384,
                "capabilities": ["chat"],
                "cognitive_services": True,
            },
            {
                "id": "grok-3",
                "name": "Grok 3",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "grok-3-mini",
                "name": "Grok 3 Mini",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            },
            {
                "id": "DeepSeek-R1-0528",
                "name": "DeepSeek R1 0528",
                "type": "chat",
                "max_tokens": 100000,
                "capabilities": ["chat", "image"],
            }
        ]

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "gpt-4.1")
        model_info = next((m for m in self.getModels() if m['id'] == model), None)

        url = self.endpoint if not (model_info and model_info.get("cognitive_services")) else self.endpointCognitive
        if not url:
            raise ValueError("Endpoint URL is not set for Azure provider.")

        processed_messages = _preprocess_messages_openai_compat(messages, self.process_image)
        
        data = {
            "model": model,
            "messages": processed_messages,
            "max_tokens": kwargs.get("max_tokens", 2048),
            "temperature": kwargs.get("temperature", 0.5),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key}",
        }
        try:
            resp = httpx.post(url, headers=headers, json=data, timeout=30)
            resp.raise_for_status()
            resp_json = resp.json()
            if resp_json.get("error"):
                raise ValueError(f"Azure API error: {resp_json['error']['message']}")
            return resp_json
        except httpx.HTTPStatusError as e:
            raise ValueError(f"Azure API returned an error: {e}")
        except httpx.ConnectError as e:
            raise ValueError(f"Connection to Azure API failed: {e}")

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class GoogleProvider(BaseLLMProvider):
    def getModels(self):
        """
        Fetch available Gemini models dynamically from Google's OpenAI-compatible
        endpoint, overlaying known capability metadata. Falls back to hardcoded
        defaults if the API is unreachable.
        """
        cache_key = "google:models"

        def fetch():
            try:
                import openai as _openai
                client = _openai.OpenAI(
                    api_key=self.key,
                    base_url=GOOGLE_OPENAI_BASE_URL,
                )
                page = client.models.list()
                result = []
                for m in page.data:
                    mid = m.id
                    # Only chat models (skip embedding, AQA, etc.)
                    if not mid.startswith("gemini") or "embedding" in mid or "aqa" in mid:
                        continue
                    meta = _GOOGLE_META.get(mid, {"max_tokens": 65536, "capabilities": ["chat", "image"]})
                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "chat",
                        **meta,
                    })
                return sorted(result, key=lambda x: x["id"]) if result else _google_fallback()
            except Exception:
                return _google_fallback()

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "gemini-2.0-flash")
        model_ids = [m['id'] for m in self.getModels()]
        if model not in model_ids:
            raise ValueError(f"Model {model} is not supported. Available: {model_ids}")

        processed_messages = _preprocess_messages_openai_compat(messages, self.process_image)

        client = ai.Client({
            "openai": {
                "api_key": self.key,
                "base_url": GOOGLE_OPENAI_BASE_URL,
            }
        })

        response = client.chat.completions.create(
            model=f"openai:{model}",
            messages=processed_messages,
            temperature=kwargs.get("temperature", 0.5),
            max_tokens=kwargs.get("max_tokens", 2048),
        )

        return _aisuite_response_to_dict(response)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class OpenAIProvider(BaseLLMProvider):
    def getModels(self):
        """
        Fetch available chat models dynamically from the OpenAI models endpoint,
        overlaying known capability/context metadata. Falls back to hardcoded
        defaults if the API is unreachable.
        """
        cache_key = "openai:models"

        def fetch():
            try:
                import openai as _openai
                _CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
                client = _openai.OpenAI(api_key=self.key)
                page = client.models.list()
                result = []
                for m in page.data:
                    mid = m.id
                    if not any(mid.startswith(p) for p in _CHAT_PREFIXES):
                        continue
                    if ":ft-" in mid or "ft:" in mid or "audio" in mid or "realtime" in mid:
                        continue
                    meta = _OPENAI_META.get(mid, {"max_tokens": 128000, "capabilities": ["chat"]})
                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "chat",
                        **meta,
                    })
                return sorted(result, key=lambda x: x["id"]) if result else _openai_fallback()
            except Exception:
                return _openai_fallback()

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "gpt-3.5-turbo")
        new_models = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "o4-mini", "o3", "o3-mini", "o1", "o1-mini"}

        processed_messages = _preprocess_messages_openai_compat(messages, self.process_image)

        client = ai.Client({"openai": {"api_key": self.key}})

        create_kwargs: dict = {"model": f"openai:{model}", "messages": processed_messages}
        if model in new_models:
            create_kwargs["max_completion_tokens"] = kwargs.get("max_tokens", 2048)
        else:
            create_kwargs["max_tokens"] = kwargs.get("max_tokens", 2048)
            create_kwargs["temperature"] = kwargs.get("temperature", 0.5)

        max_retries, backoff_factor = 5, 1
        for attempt in range(max_retries):
            try:
                return _aisuite_response_to_dict(client.chat.completions.create(**create_kwargs))
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    sleep_time = backoff_factor * (2 ** attempt)
                    print(f"Rate limit exceeded. Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                    continue
                raise ValueError(f"Request to OpenAI API failed: {e}")
        raise ValueError("Failed to get a response from OpenAI API after multiple retries.")

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class OllamaProvider(BaseLLMProvider):
    def _get_api_url(self):
        host = os.getenv('OLLAMA_HOST', 'http://localhost')
        port = os.getenv('OLLAMA_PORT', '11434')
        return f"{host}:{port}"

    def getModels(self):
        """
        Fetch installed models dynamically from the Ollama /api/tags endpoint.
        Model type (chat/embedding) and vision capability are inferred from the
        model name. Falls back to an empty list if Ollama is unreachable.
        """
        api_url = self._get_api_url()
        cache_key = f"ollama:{api_url}"

        def fetch():
            try:
                resp = httpx.get(f"{api_url}/api/tags", timeout=5)
                resp.raise_for_status()
                result = []
                for m in resp.json().get("models", []):
                    mid = m["name"]
                    name_lower = mid.lower()
                    is_embed = any(f in name_lower for f in _OLLAMA_EMBED_FAMILIES)
                    is_vision = any(f in name_lower for f in _OLLAMA_VISION_FAMILIES)
                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "embedding" if is_embed else "chat",
                        "max_tokens": 512 if is_embed else 100000,
                        "capabilities": ["embedding"] if is_embed
                                        else (["chat", "image"] if is_vision else ["chat"]),
                    })
                return result
            except Exception:
                return []

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "gpt-oss:20b")
        models = self.getModels()
        model_ids = [m['id'] for m in models]
        if models and model not in model_ids:
            raise ValueError(f"Model {model} is not installed in Ollama. Available: {model_ids}")

        model_info = next((m for m in models if m['id'] == model), None)
        max_tokens = model_info.get('max_tokens', 100000) if model_info else 100000

        processed_messages = _preprocess_messages_ollama(messages, self.process_image)

        client = ai.Client({"ollama": {"api_url": self._get_api_url()}})

        response = client.chat.completions.create(
            model=f"ollama:{model}",
            messages=processed_messages,
            options={"num_ctx": max_tokens},
        )

        return _aisuite_response_to_dict(response)

    def process_image(self, image_path):
        return _process_image_to_base64(image_path)


class LlamaServerProvider(BaseLLMProvider):
    def _get_base_url(self):
        if self.endpoint:
            return self.endpoint.rstrip('/')
        host = os.getenv('LLAMA_SERVER_HOST', 'http://localhost')
        port = os.getenv('LLAMA_SERVER_PORT', '8080')
        return f"{host}:{port}"

    def getModels(self):
        """
        Fetch loaded models from the llama-server /v1/models endpoint.
        Type and vision capability are inferred from the model name (same
        heuristics as OllamaProvider).  Falls back to an empty list if the
        server is unreachable.
        """
        base_url = self._get_base_url()
        cache_key = f"llama_server:{base_url}"

        def fetch():
            try:
                resp = httpx.get(f"{base_url}/v1/models", timeout=5)
                resp.raise_for_status()
                result = []
                for m in resp.json().get("data", []):
                    mid = m["id"]
                    name_lower = mid.lower()
                    is_embed = any(f in name_lower for f in _OLLAMA_EMBED_FAMILIES)
                    is_vision = any(f in name_lower for f in _OLLAMA_VISION_FAMILIES)
                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "embedding" if is_embed else "chat",
                        "max_tokens": 512 if is_embed else 100000,
                        "capabilities": ["embedding"] if is_embed
                                         else (["chat", "image"] if is_vision else ["chat"]),
                    })
                return result
            except Exception:
                return []

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        base_url = self._get_base_url()
        models = self.getModels()
        model_ids = [m['id'] for m in models]

        model = kwargs.get("model", "")
        if not model and len(models) == 1:
            model = models[0]['id']
        if models and model not in model_ids:
            raise ValueError(
                f"Model '{model}' is not loaded in llama-server. Available: {model_ids}"
            )

        model_info = next((m for m in models if m['id'] == model), None)
        max_tokens = kwargs.get(
            "max_tokens",
            model_info.get('max_tokens', 2048) if model_info else 2048,
        )

        processed_messages = _preprocess_messages_openai_compat(messages, self.process_image)

        client = ai.Client({
            "openai": {
                "api_key": self.key or "not-needed",
                "base_url": f"{base_url}/v1/",
            }
        })

        response = client.chat.completions.create(
            model=f"openai:{model}",
            messages=processed_messages,
            temperature=kwargs.get("temperature", 0.5),
            max_tokens=max_tokens,
        )

        return _aisuite_response_to_dict(response)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


# ---------------------------------------------------------------------------
# Hardcoded fallbacks (used when the live API is unreachable)
# ---------------------------------------------------------------------------

def _openai_fallback():
    return [
        {"id": "gpt-4o",       "name": "gpt-4o",       "type": "chat", **_OPENAI_META["gpt-4o"]},
        {"id": "gpt-4.1",      "name": "gpt-4.1",      "type": "chat", **_OPENAI_META["gpt-4.1"]},
        {"id": "gpt-4.1-mini", "name": "gpt-4.1-mini", "type": "chat", **_OPENAI_META["gpt-4.1-mini"]},
        {"id": "gpt-4.1-nano", "name": "gpt-4.1-nano", "type": "chat", **_OPENAI_META["gpt-4.1-nano"]},
        {"id": "o4-mini",      "name": "o4-mini",      "type": "chat", **_OPENAI_META["o4-mini"]},
    ]


def _google_fallback():
    return [
        {"id": mid, "name": mid, "type": "chat", **meta}
        for mid, meta in _GOOGLE_META.items()
    ]