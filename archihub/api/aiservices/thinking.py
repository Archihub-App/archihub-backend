"""Turning a model's reasoning stream into named steps.

Port of ``ThinkingStepTracker`` in ``app/api/aiservices/utils/StreamingUtils.py``.

WHAT THIS IS, AND WHAT IT IS NOT. Several providers stream the model's own
reasoning separately from its answer (``reasoning_content``, ``thinking_delta``,
parts flagged ``thought``). That text is prose, not structure — there are no
tool calls and no agent loop behind it. This reads it line by line and promotes
anything shaped like a heading into a step, so the assistant can show *what the
model worked through* as a collapsible list instead of a wall of monologue.

So the ``thinking_step`` events are a **presentation** of the reasoning stream,
not an agent loop. `AImessaging.tsx` also handles ``agent_thought``/
``tool_execution``/``tool_result``/``memory_note``; nothing here emits those, and
they belong to a different subsystem.

WHY THE REASONING IS NEVER CONCATENATED INTO THE ANSWER. It is fluent prose in
the same voice, so a user cannot tell it apart once it is mixed in. It is kept on
its own `ChatChunk` field the whole way through, and the Google dialect had to be
fixed for exactly this: its parts list holds both, and the answer builder was
taking all of them.
"""

from __future__ import annotations

import re
import uuid

#: A heading whose own words carry no meaning, so the description after the
#: colon is the better title. Matched case-insensitively.
_GENERIC_TITLE = re.compile(
    r"^(step|paso|thought|pensamiento|reasoning|analysis|note|nota)\s*\d*$", re.IGNORECASE
)

_BULLET = re.compile(r"^(?:[-*]\s+|\d+\s*[.)-]\s+)(.+)$")

#: Long single lines with no newline still deserve a step, but only once they
#: are long enough that a colon is likely to be a heading rather than punctuation.
_LONG_LINE = 80

MAX_TITLE = 72


class ThinkingSteps:
    """Accumulates reasoning text and emits step events as they are recognised.

    Stateful by necessity: reasoning arrives in arbitrary fragments, so a line
    may be split across several deltas and a step's end is only known when the
    next one starts.
    """

    def __init__(self) -> None:
        self._partial = ""
        self._current: dict | None = None
        self._steps: list[dict] = []
        self._order = 0

    # -- consuming -------------------------------------------------------
    def consume(self, text: str) -> list[dict]:
        """Feed a reasoning delta; return whatever events it completed."""
        if not text:
            return []

        self._partial += str(text).replace("\r\n", "\n").replace("\r", "\n")

        events: list[dict] = []
        lines = self._partial.split("\n")
        # The last fragment has no newline yet, so it is not a complete line.
        self._partial = lines.pop() if lines else ""

        for line in lines:
            title = self._title_of(line)
            if title:
                events.extend(self._begin(title))

        if ":" in self._partial and len(self._partial) >= _LONG_LINE:
            title = self._title_of(self._partial)
            if title:
                events.extend(self._begin(title))
                self._partial = ""

        return events

    def finalize(self) -> list[dict]:
        """Close the open step. Called once, after the stream ends."""
        events: list[dict] = []

        if self._partial:
            title = self._title_of(self._partial)
            if title:
                events.extend(self._begin(title))
            self._partial = ""

        if self._current:
            events.append(_event(self._current, "done"))
            self._current = None

        return events

    def summary(self) -> list[dict]:
        """Every step recognised, for storing beside the assistant's turn."""
        return list(self._steps)

    # -- internals -------------------------------------------------------
    def _begin(self, title: str) -> list[dict]:
        normalized = " ".join(title.strip().lower().split())
        if not normalized:
            return []
        # The same heading restated across two deltas is one step, not two.
        if self._current and self._current["normalized"] == normalized:
            return []

        events: list[dict] = []
        if self._current:
            events.append(_event(self._current, "done"))

        self._order += 1
        step = {
            "step_id": f"step_{self._order}_{uuid.uuid4().hex[:8]}",
            "order": self._order,
            "title": title,
            "normalized": normalized,
        }
        self._steps.append({k: step[k] for k in ("step_id", "order", "title")})
        self._current = step
        events.append(_event(step, "running"))
        return events

    def _title_of(self, line: str) -> str:
        cleaned = " ".join(str(line).strip().split())
        if not cleaned:
            return ""

        if ":" in cleaned:
            head, tail = cleaned.split(":", 1)
            title = _sanitize(head)
            description = " ".join(tail.strip().split())
            if _GENERIC_TITLE.match(title) and description:
                title = _short_phrase(description)
            return _truncate(title)

        bullet = _BULLET.match(cleaned)
        if bullet:
            return _truncate(_sanitize(bullet.group(1)))

        return ""


def _event(step: dict, status: str) -> dict:
    return {
        "type": "thinking_step",
        "step_id": step["step_id"],
        "order": step["order"],
        "title": step["title"],
        "status": status,
    }


def _sanitize(title: str) -> str:
    value = re.sub(r"^[-*]\s*", "", str(title))
    value = re.sub(r"^\d+\s*[.)-]\s*", "", value)
    return " ".join(value.strip().split()).strip(" -:;,.")


def _short_phrase(text: str) -> str:
    phrase = re.split(r"[.;,]", text, maxsplit=1)[0].strip()
    words = phrase.split()
    return " ".join(words[:8]) if len(words) > 8 else phrase


def _truncate(title: str, max_len: int = MAX_TITLE) -> str:
    value = str(title).strip()
    return value if len(value) <= max_len else value[:max_len].rstrip()
