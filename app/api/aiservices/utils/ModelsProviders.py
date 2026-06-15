import aisuite as ai
from .BaseLLMProvider import BaseLLMProvider
import mimetypes
import base64
import os
import time
import httpx
from app.utils import SkillManager

GOOGLE_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_MODEL_CACHE: dict = {}
_CACHE_TTL = 300  # seconds
skillManager = SkillManager.SkillManager()


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
    # Gemini 3 Frontier Series
    "gemini-3.5-flash":      {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},
    "gemini-3.1-pro":        {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},
    "gemini-3-flash":        {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},
    "gemini-3.1-flash-lite": {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},

    # Gemini 2.5 Reasoning Series
    "gemini-2.5-pro":        {"max_tokens": 65536, "capabilities": ["chat", "image"]},
    "gemini-2.5-flash":      {"max_tokens": 65536, "capabilities": ["chat", "image"]},
    "gemini-2.5-flash-lite": {"max_tokens": 65536, "capabilities": ["chat", "image"]},

    # Gemini 2.0 Legacy Series
    "gemini-2.0-flash":      {"max_tokens": 8192,  "capabilities": ["chat", "image"]},
    "gemini-2.0-flash-lite": {"max_tokens": 8192,  "capabilities": ["chat", "image"]},

    # Gemma 4 Open Weight Models
    "gemma-4-12b-it":        {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},
    "gemma-4-26b-a4b-it":    {"max_tokens": 65536, "capabilities": ["chat", "image"]},
    "gemma-4-31b-it":        {"max_tokens": 65536, "capabilities": ["chat", "image"]},
    "gemma-4-e4b-it":        {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},
    "gemma-4-e2b-it":        {"max_tokens": 65536, "capabilities": ["chat", "image", "audio"]},
}

# Families / substrings that imply vision support in Ollama model names
_OLLAMA_VISION_FAMILIES = (
    "llava", "bakllava", "moondream", "minicpm-v", "qwen-vl", "qwen3-vl",
    "gemma3", "pixtral", "vision", "gemma4"
)
# Substrings that imply embedding models
_OLLAMA_EMBED_FAMILIES = (
    "embed", "nomic-embed", "mxbai-embed", "all-minilm",
    "snowflake-arctic-embed",
)


llm_providers = [
    "OpenAI",
    "Google",
    "OpenRouter",
    "Azure",
    "Ollama",
    "LlamaServer"
]

def get_llm_providers():
    return llm_providers


_CONTEXT_TOKEN_ERROR_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "max context length",
    "context window",
    "too many tokens",
    "token limit",
    "prompt is too long",
    "input is too long",
    "exceeds the context",
    "reduce the length",
)


def _is_context_token_error(error: Exception) -> bool:
    message = str(error).lower()
    if not message:
        return False
    if "rate limit" in message or "429" in message or "per min" in message:
        return False
    return any(marker in message for marker in _CONTEXT_TOKEN_ERROR_MARKERS)


def _flatten_message_content(content):
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get('type')
                if item_type == 'text':
                    text_value = item.get('text')
                    if text_value:
                        parts.append(str(text_value))
                    continue
                if item_type == 'image_path':
                    image_path = item.get('path')
                    parts.append(f"[image_path: {image_path}]" if image_path else "[image_path]")
                    continue
                if item_type == 'image_url':
                    image_url = item.get('image_url')
                    if isinstance(image_url, dict):
                        image_url = image_url.get('url')
                    parts.append(f"[image_url: {image_url}]" if image_url else "[image_url]")
                    continue
            parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def _serialize_message_for_compression(message):
    if not isinstance(message, dict):
        return str(message)

    role = message.get('role', 'unknown')
    content = _flatten_message_content(message.get('content'))
    if not content:
        content = "[empty message]"

    tool_calls = message.get('tool_calls') or []
    if isinstance(tool_calls, list) and tool_calls:
        tool_names = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function_data = call.get('function') or {}
            function_name = function_data.get('name')
            if function_name:
                tool_names.append(function_name)
        if tool_names:
            content = f"{content}\n[tool_calls: {', '.join(tool_names)}]"

    return f"{role}: {content}"


def _compress_messages_second_to_n_minus_3(messages):
    if not isinstance(messages, list) or len(messages) < 5:
        return None

    middle_messages = messages[1:-3]
    if not middle_messages:
        return None

    compressed_content = "\n\n".join(
        _serialize_message_for_compression(msg)
        for msg in middle_messages
    ).strip()

    if not compressed_content:
        return None

    compressed_message = {
        'role': 'user',
        'content': "Compressed previous conversation context:\n\n" + compressed_content,
    }

    return [messages[0], compressed_message, *messages[-3:]]


def _call_with_context_token_fallback(messages, call_fn):
    try:
        return call_fn(messages)
    except Exception as e:
        if not _is_context_token_error(e):
            raise

        compressed_messages = _compress_messages_second_to_n_minus_3(messages)
        if not compressed_messages:
            raise

        print("Context token overflow detected. Retrying with compressed middle messages.")
        return call_fn(compressed_messages)


def _apply_skill_context(messages, kwargs):
    enriched_messages, _ = skillManager.enrich_messages(
        messages,
        skill_ids=kwargs.get('skill_ids'),
        skill_paths=kwargs.get('skill_paths'),
        skill_names=kwargs.get('skill_names'),
        skills=kwargs.get('skills'),
        skill_context_applied=bool(kwargs.get('skill_context_applied')),
    )
    return enriched_messages


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


def _convert_openai_to_gemini_rest(messages):
    contents = []
    system_instruction = None

    for msg in messages:
        role = msg.get('role', '')
        content = msg.get('content', '')

        if role == 'system':
            system_instruction = {"parts": [{"text": content}]}
            continue

        gemini_role = 'user' if role == 'user' else 'model'
        parts = []

        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            for item in content:
                if item.get('type') == 'text':
                    parts.append({"text": item['text']})
                elif item.get('type') == 'image_url':
                    url = item.get('image_url', {}).get('url', '')
                    if url.startswith("data:"):
                        mime = url.split(";")[0][5:]
                        b64_data = url.split(",")[1]
                        parts.append({
                            "inline_data": {
                                "mime_type": mime,
                                "data": b64_data
                            }
                        })

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    return contents, system_instruction


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


def _to_positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _aisuite_response_to_dict(response):
    """Normalize an aisuite ChatCompletionResponse to the internal dict format."""
    message = response.choices[0].message
    content = getattr(message, 'content', None)

    tool_calls = []
    raw_tool_calls = getattr(message, 'tool_calls', None) or []
    for call in raw_tool_calls:
        function_obj = getattr(call, 'function', None)
        function_name = getattr(function_obj, 'name', None)
        function_arguments = getattr(function_obj, 'arguments', None)

        if isinstance(call, dict):
            function_payload = call.get('function') or {}
            function_name = function_name or function_payload.get('name')
            function_arguments = function_arguments or function_payload.get('arguments')

        tool_calls.append({
            'id': getattr(call, 'id', None) or (call.get('id') if isinstance(call, dict) else None),
            'type': getattr(call, 'type', None) or (call.get('type') if isinstance(call, dict) else 'function') or 'function',
            'function': {
                'name': function_name,
                'arguments': function_arguments or '{}'
            }
        })

    normalized_message = {
        'role': 'assistant',
        'content': content if content is not None else '',
    }
    if tool_calls:
        normalized_message['tool_calls'] = tool_calls

    return {
        'choices': [
            {
                'message': normalized_message
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
        stream = bool(kwargs.get("stream", kwargs.get("strem", kwargs.get("sstream", False))))
        tools = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        prepared_messages = _apply_skill_context(messages, kwargs)
        model_info = next((m for m in self.getModels() if m['id'] == model), None)

        url = self.endpoint if not (model_info and model_info.get("cognitive_services")) else self.endpointCognitive
        if not url:
            raise ValueError("Endpoint URL is not set for Azure provider.")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key}",
        }

        def _do_request(call_messages):
            processed_messages = _preprocess_messages_openai_compat(call_messages, self.process_image)
            data = {
                "model": model,
                "messages": processed_messages,
                "max_tokens": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.2),
                "stream": stream,
            }
            if tools:
                data['tools'] = tools
                if tool_choice:
                    data['tool_choice'] = tool_choice

            if stream:
                def iter_stream_lines():
                    try:
                        with httpx.stream("POST", url, headers=headers, json=data, timeout=60) as resp:
                            resp.raise_for_status()
                            for line in resp.iter_lines():
                                if line:
                                    yield line
                    except httpx.HTTPStatusError as e:
                        detail = e.response.text if e.response is not None else str(e)
                        raise ValueError(f"Azure API returned an error: {detail}")
                    except httpx.ConnectError as e:
                        raise ValueError(f"Connection to Azure API failed: {e}")

                return iter_stream_lines()

            try:
                resp = httpx.post(url, headers=headers, json=data, timeout=30)
                resp.raise_for_status()
                resp_json = resp.json()
                if resp_json.get("error"):
                    error_message = resp_json["error"].get("message", "Unknown Azure API error")
                    raise ValueError(f"Azure API error: {error_message}")
                return resp_json
            except httpx.HTTPStatusError as e:
                detail = e.response.text if e.response is not None else str(e)
                raise ValueError(f"Azure API returned an error: {detail}")
            except httpx.ConnectError as e:
                raise ValueError(f"Connection to Azure API failed: {e}")

        return _call_with_context_token_fallback(prepared_messages, _do_request)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class GoogleProvider(BaseLLMProvider):
    def getModels(self):
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

                fetched_ids = set()
                for m in page.data:
                    mid = m.id
                    if not mid.startswith("gemini") or "embedding" in mid or "aqa" in mid:
                        continue
                    meta = _GOOGLE_META.get(mid, {"max_tokens": 65536, "capabilities": ["chat", "image"]})
                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "chat",
                        **meta,
                    })
                    fetched_ids.add(mid)

                for mid, meta in _GOOGLE_META.items():
                    if not mid.startswith(("gemini", "gemma")) or mid in fetched_ids:
                        continue

                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "chat",
                        **meta,
                    })

                return sorted(result, key=lambda x: x["id"]) if result else _google_fallback()
            except Exception:
                return _google_fallback()

        models = _get_cached(cache_key, fetch)
        return [
            model for model in models
            if str(model.get('id', '')).startswith(("gemini", "gemma"))
        ]

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "gemini-2.0-flash")
        stream = bool(kwargs.get("stream", kwargs.get("strem", kwargs.get("sstream", False))))
        tools = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        prepared_messages = _apply_skill_context(messages, kwargs)

        model_ids = [m['id'] for m in self.getModels()]
        if model not in model_ids:
            raise ValueError(f"Model {model} is not supported. Available: {model_ids}")

        is_gemma = model.startswith("gemma")

        def _do_request(call_messages):
            processed_messages = _preprocess_messages_openai_compat(call_messages, self.process_image)

            create_kwargs = {
                'model': model,
                'messages': processed_messages,
                'temperature': kwargs.get("temperature", 0.2),
                'max_tokens': kwargs.get("max_tokens", 2048),
                'stream': stream,
            }
            if tools:
                create_kwargs['tools'] = tools
                if tool_choice:
                    create_kwargs['tool_choice'] = tool_choice

            if is_gemma:
                gemini_contents, sys_inst = _convert_openai_to_gemini_rest(processed_messages)

                payload = {
                    "contents": gemini_contents,
                    "generationConfig": {
                        "temperature": kwargs.get("temperature", 0.2),
                        "maxOutputTokens": kwargs.get("max_tokens", 2048)
                    }
                }
                if sys_inst:
                    payload["systemInstruction"] = sys_inst

                if stream:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?key={self.key}&alt=sse"

                    def gemma_stream():
                        import json

                        with httpx.stream("POST", url, json=payload, timeout=60) as resp:
                            resp.raise_for_status()
                            for line in resp.iter_lines():
                                if line.startswith("data: "):
                                    try:
                                        chunk = json.loads(line[6:])
                                        candidates = chunk.get("candidates", [])
                                        if candidates:
                                            parts = candidates[0].get("content", {}).get("parts", [])
                                            if parts and "text" in parts[0]:
                                                class StreamMessage:
                                                    content = parts[0]["text"]

                                                class StreamChoice:
                                                    delta = StreamMessage()

                                                class StreamResponse:
                                                    choices = [StreamChoice()]

                                                yield StreamResponse()
                                    except Exception:
                                        pass

                    return gemma_stream()

                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.key}"
                resp = httpx.post(url, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()

                response_text = ""
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        response_text = parts[0]["text"]

                return {
                    'choices': [{
                        'message': {
                            'role': 'assistant',
                            'content': response_text
                        }
                    }]
                }
            else:
                if tools:
                    import openai as _openai
                    client = _openai.OpenAI(
                        api_key=self.key,
                        base_url=GOOGLE_OPENAI_BASE_URL,
                    )
                    response = client.chat.completions.create(**create_kwargs)
                else:
                    client = ai.Client({
                        "openai": {
                            "api_key": self.key,
                            "base_url": GOOGLE_OPENAI_BASE_URL,
                        }
                    })
                    aisuite_kwargs = create_kwargs.copy()
                    aisuite_kwargs['model'] = f"openai:{model}"
                    response = client.chat.completions.create(**aisuite_kwargs)

            if stream:
                return response

            return _aisuite_response_to_dict(response)

        return _call_with_context_token_fallback(prepared_messages, _do_request)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class OpenAIProvider(BaseLLMProvider):
    def getModels(self):
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
        stream = bool(kwargs.get("stream", kwargs.get("strem", kwargs.get("sstream", False))))
        tools = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        uses_max_completion_tokens = model.startswith(("gpt-5", "o1", "o3", "o4"))
        prepared_messages = _apply_skill_context(messages, kwargs)

        def _do_request(call_messages):
            processed_messages = _preprocess_messages_openai_compat(call_messages, self.process_image)

            create_kwargs: dict = {"model": model, "messages": processed_messages, "stream": stream}
            max_tokens_value = _to_positive_int(kwargs.get("max_tokens"))
            if uses_max_completion_tokens:
                if max_tokens_value is not None:
                    create_kwargs["max_completion_tokens"] = max_tokens_value
            else:
                if max_tokens_value is not None:
                    create_kwargs["max_tokens"] = max_tokens_value
                create_kwargs["temperature"] = kwargs.get("temperature", 0.2)
            if tools:
                create_kwargs['tools'] = tools
                if tool_choice:
                    create_kwargs['tool_choice'] = tool_choice

            max_retries, backoff_factor = 5, 1
            for attempt in range(max_retries):
                try:
                    if tools:
                        import openai as _openai
                        client = _openai.OpenAI(api_key=self.key)
                        response = client.chat.completions.create(**create_kwargs)
                    else:
                        client = ai.Client({"openai": {"api_key": self.key}})
                        aisuite_kwargs = create_kwargs.copy()
                        aisuite_kwargs["model"] = f"openai:{model}"
                        response = client.chat.completions.create(**aisuite_kwargs)
                    if stream:
                        return response
                    return _aisuite_response_to_dict(response)
                except Exception as e:
                    if "429" in str(e) and attempt < max_retries - 1:
                        sleep_time = backoff_factor * (2 ** attempt)
                        print(f"Rate limit exceeded. Retrying in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                        continue
                    raise ValueError(f"Request to OpenAI API failed: {e}")
            raise ValueError("Failed to get a response from OpenAI API after multiple retries.")

        return _call_with_context_token_fallback(prepared_messages, _do_request)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class OpenRouterProvider(BaseLLMProvider):
    def _get_base_url(self):
        return (self.endpoint or OPENROUTER_BASE_URL).rstrip('/')

    def getModels(self):
        base_url = self._get_base_url()
        cache_key = f"openrouter:models:{base_url}"

        def fetch():
            try:
                import openai as _openai

                client = _openai.OpenAI(
                    api_key=self.key,
                    base_url=base_url,
                )
                page = client.models.list()
                result = []
                for m in page.data:
                    mid = m.id
                    model_name = getattr(m, 'name', None) or mid
                    model_caps = ["chat"]
                    lower_blob = f"{mid} {model_name}".lower()
                    if any(token in lower_blob for token in ("vision", "vl", "image", "multimodal")):
                        model_caps.append("image")
                    result.append({
                        "id": mid,
                        "name": model_name,
                        "type": "chat",
                        "max_tokens": getattr(m, 'context_length', None) or 65536,
                        "capabilities": model_caps,
                    })
                return sorted(result, key=lambda x: x["id"])
            except Exception:
                return []

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "")
        stream = bool(kwargs.get("stream", kwargs.get("strem", kwargs.get("sstream", False))))
        tools = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        prepared_messages = _apply_skill_context(messages, kwargs)
        models = self.getModels()
        model_ids = [m['id'] for m in models]

        if not model and len(models) == 1:
            model = models[0]['id']
        if models and model not in model_ids:
            raise ValueError(f"Model {model} is not supported by OpenRouter. Available: {model_ids}")

        def _do_request(call_messages):
            processed_messages = _preprocess_messages_openai_compat(call_messages, self.process_image)

            create_kwargs: dict = {
                "model": model,
                "messages": processed_messages,
                "stream": stream,
            }
            max_tokens_value = _to_positive_int(kwargs.get("max_tokens"))
            if max_tokens_value is not None:
                create_kwargs["max_tokens"] = max_tokens_value
            create_kwargs["temperature"] = kwargs.get("temperature", 0.2)
            if tools:
                create_kwargs['tools'] = tools
                if tool_choice:
                    create_kwargs['tool_choice'] = tool_choice

            max_retries, backoff_factor = 5, 1
            for attempt in range(max_retries):
                try:
                    if tools:
                        import openai as _openai

                        client = _openai.OpenAI(
                            api_key=self.key,
                            base_url=self._get_base_url(),
                        )
                        response = client.chat.completions.create(**create_kwargs)
                    else:
                        client = ai.Client({
                            "openai": {
                                "api_key": self.key,
                                "base_url": self._get_base_url(),
                            }
                        })
                        aisuite_kwargs = create_kwargs.copy()
                        aisuite_kwargs["model"] = f"openai:{model}"
                        response = client.chat.completions.create(**aisuite_kwargs)
                    if stream:
                        return response
                    return _aisuite_response_to_dict(response)
                except Exception as e:
                    if "429" in str(e) and attempt < max_retries - 1:
                        sleep_time = backoff_factor * (2 ** attempt)
                        print(f"Rate limit exceeded. Retrying in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                        continue
                    raise ValueError(f"Request to OpenRouter API failed: {e}")
            raise ValueError("Failed to get a response from OpenRouter API after multiple retries.")

        return _call_with_context_token_fallback(prepared_messages, _do_request)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)


class OllamaProvider(BaseLLMProvider):
    def _get_api_url(self):
        host = os.getenv('OLLAMA_HOST', 'http://localhost')
        port = os.getenv('OLLAMA_PORT', '11434')
        return f"{host}:{port}"

    def getModels(self):
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
                        "max_tokens": 512 if is_embed else 65000,
                        "capabilities": ["embedding"] if is_embed
                                        else (["chat", "image"] if is_vision else ["chat"]),
                    })
                return result
            except Exception:
                return []

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        model = kwargs.get("model", "gpt-oss:20b")
        stream = bool(kwargs.get("stream", kwargs.get("strem", kwargs.get("sstream", False))))
        prepared_messages = _apply_skill_context(messages, kwargs)
        models = self.getModels()
        model_ids = [m['id'] for m in models]
        if models and model not in model_ids:
            raise ValueError(f"Model {model} is not installed in Ollama. Available: {model_ids}")

        model_info = next((m for m in models if m['id'] == model), None)
        max_tokens = model_info.get('max_tokens', 65000) if model_info else 65000

        def _do_request(call_messages):
            processed_messages = _preprocess_messages_ollama(call_messages, self.process_image)

            client = ai.Client({"ollama": {"api_url": self._get_api_url(), "timeout": 300}})

            response = client.chat.completions.create(
                model=f"ollama:{model}",
                messages=processed_messages,
                options={"num_ctx": max_tokens},
                stream=stream,
            )

            if stream:
                return response

            return _aisuite_response_to_dict(response)

        return _call_with_context_token_fallback(prepared_messages, _do_request)

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
                for m in resp.json().get("models", []):
                    mid = m["name"]

                    result.append({
                        "id": mid,
                        "name": mid,
                        "type": "chat",
                        "capabilities": m.get('capabilities', [])
                    })

                return result
            except Exception:
                return []

        return _get_cached(cache_key, fetch)

    def call(self, messages, **kwargs):
        base_url = self._get_base_url()
        stream = bool(kwargs.get("stream", kwargs.get("strem", kwargs.get("sstream", False))))
        tools = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        prepared_messages = _apply_skill_context(messages, kwargs)
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

        def _do_request(call_messages):
            processed_messages = _preprocess_messages_openai_compat(call_messages, self.process_image)

            create_kwargs = {
                'model': model,
                'messages': processed_messages,
                'temperature': kwargs.get("temperature", 0.2),
                'max_tokens': max_tokens,
                'stream': stream,
            }
            if tools:
                create_kwargs['tools'] = tools
                if tool_choice:
                    create_kwargs['tool_choice'] = tool_choice

            if tools:
                import openai as _openai
                client = _openai.OpenAI(
                    api_key=self.key or "not-needed",
                    base_url=f"{base_url}/v1/",
                )
                response = client.chat.completions.create(**create_kwargs)
            else:
                client = ai.Client({
                    "openai": {
                        "api_key": self.key or "not-needed",
                        "base_url": f"{base_url}/v1/",
                    }
                })
                aisuite_kwargs = create_kwargs.copy()
                aisuite_kwargs['model'] = f"openai:{model}"
                response = client.chat.completions.create(**aisuite_kwargs)

            if stream:
                return response

            return _aisuite_response_to_dict(response)

        return _call_with_context_token_fallback(prepared_messages, _do_request)

    def process_image(self, image_path):
        return _process_image_to_data_url(image_path)

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
        if mid.startswith("gemini") or mid.startswith("gemma")
    ]