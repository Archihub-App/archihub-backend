"""Asking a model something, and coping when it says no.

Everything here is written once against the dialect contract and works for every
provider. Copying this logic per vendor is how the copies come to disagree about
things nobody decided — whether a call retries, whether it has a timeout, which
parameter carries the token ceiling — and each difference then looks deliberate
to the next reader.

Three things happen here that are worth stating plainly.

**A request that will not fit is shrunk, not failed.** Every conversation grows
past the model's window eventually; the recovery is to compress the middle of
the history and retry, keeping the system prompt and the most recent turns
intact. It keys on a classified ``CONTEXT_LENGTH`` reason rather than on the
wording of an error message, which varies by provider, by version and by
locale.

**Compression is progressive and bounded.** Each attempt keeps fewer turns
verbatim. It stops when there is nothing left to give up, rather than looping.

**A model is not asked to do what it cannot.** If the catalogue says a model has
no image capability and the conversation contains an image, that is a clear
refusal before the call — not a provider error the user has to interpret.
Crucially this only fires on a *positive* statement of capabilities: a provider
that reports nothing is not second-guessed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from archihub.api.aiservices import catalogue, errors
from archihub.core.i18n import gettext as _

logger = logging.getLogger(__name__)

#: How many times a too-large conversation is compressed and retried before
#: giving up. Each round is a real API call, so this is small.
MAX_SHRINK_ATTEMPTS = 3

#: Turns at the end of the conversation kept verbatim at the first attempt.
#: Halves each round, to a floor of one.
INITIAL_VERBATIM_TAIL = 8

#: How much of a compressed message survives, in characters.
COMPRESSED_LENGTH = 400


class CapabilityError(Exception):
    """The model cannot do what this request needs."""


# ---------------------------------------------------------------------------
# Capability checks
# ---------------------------------------------------------------------------


def required_capabilities(messages: list[dict], options: dict) -> set[str]:
    """What this request needs of a model, from the request itself."""
    needed = {"chat"}

    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    kind = part.get("type")
                    if kind == "image_url":
                        needed.add("image")
                    elif kind in ("input_audio", "audio"):
                        needed.add("audio")

    if options.get("tools"):
        needed.add("tools")
    return needed


def check_capabilities(model, messages: list[dict], options: dict) -> None:
    """Refuse a request the model is known to be unable to serve.

    **Only acts on positive knowledge.** A model whose provider declares no
    capabilities is not blocked — absence of evidence is not evidence of
    absence, and refusing on silence would make every OpenAI model unusable for
    images, since OpenAI's `/models` says nothing at all.
    """
    if model is None or not model.capabilities:
        return

    missing = required_capabilities(messages, options) - set(model.capabilities)
    missing.discard("chat")
    if missing:
        raise CapabilityError(
            _(
                'The model "{model}" does not support: {missing}',
                model=model.id,
                missing=", ".join(sorted(missing)),
            )
        )


# ---------------------------------------------------------------------------
# Fitting a conversation into a window
# ---------------------------------------------------------------------------


def compress(messages: list[dict], keep_tail: int) -> list[dict]:
    """Shorten the middle of a conversation, keeping its ends intact.

    The system prompt carries the instructions and the most recent turns carry
    the thread; what is in between is summarisable. Compressed turns are marked
    so the model is not misled into thinking it said something shorter than it
    did.
    """
    if len(messages) <= keep_tail + 1:
        return messages

    head = [m for m in messages[:1] if m.get("role") == "system"]
    body = messages[len(head) : len(messages) - keep_tail]
    tail = messages[len(messages) - keep_tail :]

    return [*head, *(_shorten(m) for m in body), *tail]


def _shorten(message: dict) -> dict:
    text = _flatten(message.get("content"))
    if len(text) <= COMPRESSED_LENGTH:
        return message
    return {
        "role": message.get("role", "user"),
        "content": text[:COMPRESSED_LENGTH] + _(" … [earlier message shortened to fit]"),
    }


def _flatten(content) -> str:
    """A message's text, with non-text parts named rather than dropped silently."""
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(item.get("text") or "")
            elif item.get("type") == "image_url":
                parts.append(_("[image]"))
    return " ".join(p for p in parts if p)


def _shrink_schedule() -> Iterator[int]:
    keep = INITIAL_VERBATIM_TAIL
    for _attempt in range(MAX_SHRINK_ATTEMPTS):
        yield max(1, keep)
        keep //= 2


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def complete(provider: dict, messages: list[dict], **options) -> dict:
    """One answer, with capability checking and context recovery."""
    adapter = catalogue.build_adapter(provider)
    model = catalogue.find_model(provider, options.get("model") or "")
    check_capabilities(model, messages, options)

    options = _with_defaults(options, model)
    attempt_messages = messages

    for keep_tail in _shrink_schedule():
        try:
            return adapter.chat(attempt_messages, **options).as_dict()
        except errors.ProviderError as exc:
            if exc.reason not in errors.RECOVERABLE_BY_SHRINKING:
                raise
            shrunk = compress(messages, keep_tail)
            if shrunk == attempt_messages:
                # Nothing left to give up; failing is honest.
                raise
            logger.info(
                "Conversation exceeded the model window; retrying with %d recent turns kept",
                keep_tail,
            )
            attempt_messages = shrunk

    raise errors.ProviderError(
        errors.Reason.CONTEXT_LENGTH,
        _("The conversation is too long for this model, even after shortening"),
    )


def stream(provider: dict, messages: list[dict], **options) -> Iterator:
    """The answer as it arrives.

    Context recovery applies only up to the first byte: once tokens have reached
    the caller, silently restarting with a different history would produce a
    reply that contradicts what they have already read.
    """
    adapter = catalogue.build_adapter(provider)
    model = catalogue.find_model(provider, options.get("model") or "")
    check_capabilities(model, messages, options)

    options = _with_defaults(options, model)
    attempt_messages = messages

    for keep_tail in _shrink_schedule():
        started = False
        try:
            for chunk in adapter.stream(attempt_messages, **options):
                started = True
                yield chunk
            return
        except errors.ProviderError as exc:
            if started or exc.reason not in errors.RECOVERABLE_BY_SHRINKING:
                raise
            shrunk = compress(messages, keep_tail)
            if shrunk == attempt_messages:
                raise
            attempt_messages = shrunk

    raise errors.ProviderError(
        errors.Reason.CONTEXT_LENGTH,
        _("The conversation is too long for this model, even after shortening"),
    )


def _with_defaults(options: dict, model) -> dict:
    """Fill in what the catalogue knows and the caller did not say.

    Only from *reported* metadata. If nothing is known about the model's output
    limit, none is sent and the provider applies its own default — which is the
    correct behaviour and what the legacy hardcoded tables were standing in for.
    """
    resolved = dict(options)
    if model is not None and resolved.get("max_tokens") is None and model.max_output_tokens:
        resolved["max_tokens"] = model.max_output_tokens
    return resolved


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


def parse_tool_arguments(tool_call: dict) -> dict:
    """A tool call's arguments as a mapping.

    Providers disagree on whether ``arguments`` is a JSON string or an object,
    and a model can emit malformed JSON in either. A bad payload yields an empty
    mapping rather than raising, so one confused tool call does not end the
    conversation.
    """
    function = tool_call.get("function") if isinstance(tool_call, dict) else None
    if not isinstance(function, dict):
        return {}

    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except ValueError:
            logger.info("A model produced tool arguments that are not valid JSON")
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
