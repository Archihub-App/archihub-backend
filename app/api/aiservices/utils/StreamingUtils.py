import json


def coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def resolve_stream_flag(payload):
    opts = payload.get("opts", {}) if isinstance(payload, dict) else {}

    value = opts.get(
        "stream",
        opts.get(
            "strem",
            opts.get(
                "sstream",
                payload.get(
                    "stream",
                    payload.get("strem", payload.get("sstream", False))
                ) if isinstance(payload, dict) else False,
            ),
        ),
    )

    return coerce_bool(value, default=False)


def sse_data(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"


def _chunk_to_dict(chunk):
    if isinstance(chunk, dict):
        return chunk

    if hasattr(chunk, "model_dump"):
        try:
            return chunk.model_dump()
        except Exception:
            pass

    if hasattr(chunk, "to_dict"):
        try:
            return chunk.to_dict()
        except Exception:
            pass

    return None


def _content_to_text(content):
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text", ""))
        return ""

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue

            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "text":
                    parts.append(str(item.get("content", "")))
                continue

            text_attr = getattr(item, "text", None)
            if text_attr:
                parts.append(str(text_attr))

        return "".join(parts)

    text_attr = getattr(content, "text", None)
    if text_attr:
        return str(text_attr)

    return ""


def _extract_text_from_openai_like_payload(payload):
    parts = _extract_stream_parts_from_openai_like_payload(payload)
    return parts.get("response") or parts.get("thinking") or ""


def _extract_stream_parts_from_openai_like_payload(payload):
    result = {"thinking": "", "response": ""}

    choices = payload.get("choices") or []
    if not choices:
        return result

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        first_choice = _chunk_to_dict(first_choice) or {}

    delta = first_choice.get("delta")
    if isinstance(delta, dict):
        reasoning_text = _content_to_text(delta.get("reasoning_content"))
        if not reasoning_text:
            reasoning_text = _content_to_text(delta.get("reasoning"))
        if reasoning_text:
            result["thinking"] = reasoning_text

        delta_text = _content_to_text(delta.get("content"))
        if delta_text:
            result["response"] = delta_text

        return result

    reasoning_text = _content_to_text(first_choice.get("reasoning_content"))
    if reasoning_text:
        result["thinking"] = reasoning_text

    message = first_choice.get("message")
    if isinstance(message, dict):
        message_text = _content_to_text(message.get("content"))
        if message_text:
            result["response"] = message_text

    return result


def extract_stream_chunk_parts(chunk):
    result = {"thinking": "", "response": ""}

    if isinstance(chunk, (bytes, bytearray)):
        chunk = chunk.decode("utf-8", errors="ignore")

    if isinstance(chunk, str):
        line = chunk.strip()
        if not line:
            return result

        if line.startswith("data:"):
            line = line[5:].strip()

        if line == "[DONE]":
            return result

        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return _extract_stream_parts_from_openai_like_payload(parsed)
            return result
        except Exception:
            result["response"] = line
            return result

    chunk_dict = _chunk_to_dict(chunk)
    if chunk_dict:
        return _extract_stream_parts_from_openai_like_payload(chunk_dict)

    # Fallback for object-style SDK chunks
    choices = getattr(chunk, "choices", None)
    if not choices:
        return result

    first_choice = choices[0]
    delta = getattr(first_choice, "delta", None)
    if delta is not None:
        reasoning_content = _content_to_text(getattr(delta, "reasoning_content", None))
        if not reasoning_content:
            reasoning_content = _content_to_text(getattr(delta, "reasoning", None))
        if reasoning_content:
            result["thinking"] = reasoning_content

        delta_content = _content_to_text(getattr(delta, "content", None))
        if delta_content:
            result["response"] = delta_content

        return result

    message = getattr(first_choice, "message", None)
    if message is not None:
        message_content = _content_to_text(getattr(message, "content", None))
        if message_content:
            result["response"] = message_content

    return result


def extract_stream_chunk_text(chunk):
    parts = extract_stream_chunk_parts(chunk)
    return parts.get("response") or parts.get("thinking") or ""