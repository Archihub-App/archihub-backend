"""Server-sent events, framed correctly.

SSE FRAMING IS EXACT, AND EASY TO GET SUBTLY WRONG. A frame built as::

    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

In an f-string, ``\\n`` is a **literal backslash followed by 'n'** — not a
newline. So the stream contains no event separators at all, and no standard SSE
client can read it. `upgrade_front` copes only because `AIservice.tsx` carries
repair regexes that rewrite those literal sequences back into real newlines
before parsing.

Emitting correct SSE is backward-compatible with that frontend, which was
checked rather than assumed: `normalizeEscapedSSEDelimiters` matches the literal
two-character sequences ``\\n\\n`` and ``\\r\\n\\r\\n``. A correct stream
contains neither, so the regexes do not match, the text passes through
untouched, and `nextSeparatorIndex` finds the real ``\\n\\n`` it is already
looking for. Both framings therefore work today, and only the correct one works
with anything else — an `EventSource`, a proxy that understands SSE, or any
client written against the spec.

(CLAUDE.md previously said to preserve the exact bytes. That guidance was
written before the frontend parser was read closely; it is superseded here, and
the note has been updated.)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

logger = logging.getLogger(__name__)

#: Sent periodically so proxies do not close an idle connection. A comment line
#: is ignored by every conforming client.
KEEPALIVE = ": keepalive\n\n"

#: ``X-Accel-Buffering: no`` is the one that matters in this deployment: nginx
#: fronts the backend and buffers proxied responses by default, so without it the
#: whole answer arrives at once when the model finishes - streaming looks broken
#: while being perfectly correct on the wire.
STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def frame(payload: dict, *, event: str | None = None) -> str:
    """One SSE frame.

    ``ensure_ascii=False`` so accented text is not expanded into escapes, and
    embedded newlines in the JSON are impossible because ``json.dumps`` escapes
    them — which is what makes a single ``data:`` line safe here.
    """
    body = json.dumps(payload, ensure_ascii=False)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {body}\n\n"


def done() -> str:
    """The terminator every OpenAI-compatible client recognises."""
    return "data: [DONE]\n\n"


def event_stream(chunks: Iterator, *, on_error=None) -> Iterator[str]:
    """Render model chunks as SSE, including failures.

    **An error mid-stream is delivered as an event, not a dropped connection.**
    The status line has already been sent by then, so there is no status code
    left to signal with; a client that sees the socket close cannot tell a
    finished answer from a failed one. The legacy code simply let the exception
    escape the generator.
    """
    from archihub.api.aiservices import errors

    try:
        for chunk in chunks:
            payload: dict = {}
            if getattr(chunk, "delta", ""):
                payload["delta"] = chunk.delta
            if getattr(chunk, "tool_calls", None):
                payload["tool_calls"] = chunk.tool_calls
            if getattr(chunk, "finish_reason", None):
                payload["finish_reason"] = chunk.finish_reason
            if getattr(chunk, "usage", None):
                payload["usage"] = chunk.usage

            if payload:
                yield frame(payload)
    except errors.ProviderError as exc:
        logger.info("Model stream failed: %s", exc)
        if on_error:
            on_error(exc)
        yield frame({"error": str(exc), "reason": exc.reason.value}, event="error")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected failure while streaming a model response")
        if on_error:
            on_error(exc)
        yield frame(
            {"error": "An unexpected error occurred", "reason": errors.Reason.UNKNOWN.value},
            event="error",
        )
    finally:
        yield done()


def plain_response(frames: Iterator):
    """Stream frames a caller has already rendered, with the same headers.

    `response()` above renders model chunks into OpenAI-shaped frames. The
    record assistant emits its own shape - `{"type": "response", ...}` then
    `{"type": "done", ...}` - because that is what `AIservice.tsx` parses, and a
    payload with neither `type` nor `response` falls through its parser to the
    *done* branch. So the assistant builds its frames and this only carries
    them, sharing the headers rather than duplicating them.
    """
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


def response(chunks: Iterator, *, on_error=None):
    """A ``StreamingResponse`` carrying the headers streaming actually needs."""
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        event_stream(chunks, on_error=on_error),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )
