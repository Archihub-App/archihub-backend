import json
import re
import uuid


_GENERIC_STEP_TITLE_RE = re.compile(r"^(?:paso|step)\s*\d*$", re.IGNORECASE)


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


class ThinkingStepTracker:
    def __init__(self):
        self._partial_line = ""
        self._current_step = None
        self._steps = []
        self._order = 0

    def consume_thinking(self, thinking_text):
        if not thinking_text:
            return []

        text = str(thinking_text).replace("\r\n", "\n").replace("\r", "\n")
        self._partial_line += text

        events = []
        lines = self._partial_line.split("\n")
        self._partial_line = lines.pop() if lines else ""

        for line in lines:
            title = self._extract_title_from_line(line)
            if title:
                events.extend(self._transition_to_step(title))

        # Some providers stream long single lines with no trailing newline.
        if ":" in self._partial_line and len(self._partial_line) >= 80:
            title = self._extract_title_from_line(self._partial_line)
            if title:
                events.extend(self._transition_to_step(title))
                self._partial_line = ""

        return events

    def finalize(self):
        events = []

        if self._partial_line:
            title = self._extract_title_from_line(self._partial_line)
            if title:
                events.extend(self._transition_to_step(title))
            self._partial_line = ""

        if self._current_step:
            events.append(self._to_event(self._current_step, "done"))
            self._current_step = None

        return events

    def summary(self):
        return list(self._steps)

    def _transition_to_step(self, title):
        normalized_title = self._normalize_title(title)
        if not normalized_title:
            return []

        events = []

        if self._current_step and self._current_step["normalized_title"] == normalized_title:
            return events

        if self._current_step:
            events.append(self._to_event(self._current_step, "done"))

        self._order += 1
        step = {
            "step_id": f"step_{self._order}_{uuid.uuid4().hex[:8]}",
            "order": self._order,
            "title": title,
            "normalized_title": normalized_title,
        }

        self._steps.append(
            {
                "step_id": step["step_id"],
                "order": step["order"],
                "title": step["title"],
            }
        )

        self._current_step = step
        events.append(self._to_event(step, "running"))
        return events

    def _to_event(self, step, status):
        return {
            "type": "thinking_step",
            "step_id": step["step_id"],
            "order": step["order"],
            "title": step["title"],
            "status": status,
        }

    @staticmethod
    def _normalize_title(title):
        return " ".join(str(title).strip().lower().split())

    def _extract_title_from_line(self, line):
        cleaned = " ".join(str(line).strip().split())
        if not cleaned:
            return ""

        if ":" in cleaned:
            title_part, description_part = cleaned.split(":", 1)
            title = self._sanitize_title_part(title_part)
            description = " ".join(description_part.strip().split())

            if _GENERIC_STEP_TITLE_RE.match(title) and description:
                title = self._short_phrase(description)

            return self._truncate_title(title)

        bullet_match = re.match(r"^(?:[-*]\s+|\d+\s*[.)-]\s+)(.+)$", cleaned)
        if bullet_match:
            return self._truncate_title(self._sanitize_title_part(bullet_match.group(1)))

        return ""

    @staticmethod
    def _sanitize_title_part(title):
        sanitized = re.sub(r"^[-*]\s*", "", str(title))
        sanitized = re.sub(r"^\d+\s*[.)-]\s*", "", sanitized)
        sanitized = " ".join(sanitized.strip().split())
        return sanitized.strip(" -:;,.")

    @staticmethod
    def _short_phrase(text):
        phrase = re.split(r"[.;,]", text, maxsplit=1)[0].strip()
        words = phrase.split()
        if len(words) > 8:
            phrase = " ".join(words[:8])
        return phrase

    @staticmethod
    def _truncate_title(title, max_len=72):
        value = str(title).strip()
        if not value:
            return ""
        if len(value) <= max_len:
            return value
        return value[:max_len].rstrip()


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