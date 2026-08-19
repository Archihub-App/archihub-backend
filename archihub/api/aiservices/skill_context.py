"""Applying a user's selected skills to the prompt.

Port of the resolution and injection half of ``app/utils/SkillManager.py``
(``prepare_conversation_payload`` / ``resolve_requested_skills`` /
``enrich_messages``). Skill storage, syncing and CRUD are already in
``skills.py``; this is only the part that turns a selection into prompt text.

TWO WAYS TO ASK FOR A SKILL, AND THE FRONTEND USES BOTH AT ONCE. `AImessaging.tsx`
sends the selection in ``applied_skills``, **and** prefixes the message with a
literal ``\\path`` token per skill:

    requestMessage = [...appliedSkills.map(s => `\\${s}`), trimmed].join(' ')

So a backend that only reads the field still leaves the token sitting in the
message text, where the model sees an unexplained ``\\research/summarise`` and
has to guess. Both are read here, the tokens are stripped from the message, and
the union is resolved — which is also what lets a user type a skill inline
without touching the picker.

RESOLUTION IS BY LOOKUP, NEVER BY PATH ARITHMETIC. An identifier is matched
against the stored ``path``/``command``/``name``/``title`` of a skill record. A
skill that does not resolve is **skipped silently**: an unrecognised backslash
token in prose - a Windows path, an escape sequence - must not fail the
request.
"""

from __future__ import annotations

import copy
import logging
import re

logger = logging.getLogger(__name__)

#: ``\name`` at a word boundary. The negative lookbehind keeps it from matching
#: inside a token that is already part of something else - a Windows path or an
#: escaped character in quoted text.
INLINE_SKILL = re.compile(r"(?<!\S)\\([A-Za-z0-9_./-]+)")

#: How the resolved skills are introduced to the model. Wording preserved from
#: the legacy renderer: it is prompt text, and small edits change behaviour.
PREAMBLE = (
    "Use the following skill instructions as additional context for this request. "
    "Follow them only when they are relevant and do not override higher-priority "
    "safety or system rules."
)

#: A ceiling on how much skill text may be prepended to one request. Skills are
#: operator-authored files with no size limit of their own, and several selected
#: at once are concatenated - without this a single request could exceed the
#: model's window before the user's own question is even reached.
MAX_CONTEXT_CHARS = 60_000


def _mongo():
    from archihub.infra.mongo import get_mongo

    return get_mongo()


def strip_inline(message: str) -> tuple[str, list[str]]:
    """``(message without its skill tokens, the tokens found)``."""
    if not isinstance(message, str) or not message:
        return message or "", []

    found = [match.group(1) for match in INLINE_SKILL.finditer(message)]
    if not found:
        return message, []

    cleaned = INLINE_SKILL.sub("", message)
    return re.sub(r"\s{2,}", " ", cleaned).strip(), found


def _identifiers(applied) -> list[str]:
    """Skill identifiers out of whatever shape the client sent.

    The picker sends strings; a stored conversation replays dicts. Both are
    accepted, because `applied_skills` round-trips through the database.
    """
    found: list[str] = []
    if isinstance(applied, str):
        return [applied]
    for item in applied or []:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            for key in ("path", "command", "id", "name", "title"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    found.append(value)
                    break
    return found


def lookup(identifier: str) -> dict | None:
    """One skill record by any of the names it answers to."""
    from archihub.api.aiservices import skills

    normalized = (identifier or "").strip().lstrip("\\").strip()
    if not normalized:
        return None

    candidates = [normalized]
    try:
        stored = skills.normalise(normalized)
        candidates.append(stored)
        candidates.append(skills.command_of(stored))
    except Exception:
        # `normalise` refuses anything that would escape the skills directory.
        # A traversal string simply matches no skill; it is not an error, and
        # reporting one would tell a caller which strings are interesting.
        logger.debug("Skill identifier %r is not a usable path", identifier)

    seen: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.append(candidate)

    filters = {
        "$or": [
            {field: candidate}
            for candidate in seen
            for field in ("path", "command", "name", "title")
        ]
    }
    return _mongo().get_record(skills.COLLECTION, filters)


def resolve(message: str, applied) -> tuple[str, list[dict]]:
    """``(message with tokens removed, the skills to apply)``.

    Order follows the request - the picker's selection first, then anything
    typed inline - and duplicates are dropped by path, so selecting a skill and
    also typing it applies it once.
    """
    cleaned, inline = strip_inline(message)

    resolved: list[dict] = []
    seen: set[str] = set()
    for identifier in [*_identifiers(applied), *inline]:
        skill = lookup(identifier)
        if not skill:
            logger.debug("No skill matches %r", identifier)
            continue
        path = skill.get("path")
        if not path or path in seen:
            continue
        seen.add(path)
        resolved.append(skill)

    return cleaned, resolved


def render(skills_: list[dict]) -> str:
    """The skills as one block of prompt text."""
    from archihub.api.aiservices import skills as skills_module

    sections = []
    for skill in skills_:
        title = skill.get("title") or skill.get("name") or skill.get("path")
        command = skill.get("command") or skills_module.command_of(skill.get("path") or "")
        content = (skill.get("content") or "").strip()
        sections.append(f"Active skill: {title}\\{command}\n{content}")

    body = "\n\n".join(sections)
    if len(body) > MAX_CONTEXT_CHARS:
        logger.warning(
            "Skill context of %d characters truncated to %d", len(body), MAX_CONTEXT_CHARS
        )
        body = body[:MAX_CONTEXT_CHARS]
    return f"{PREAMBLE}\n\n{body}"


def apply_to(messages: list[dict], message: str, applied) -> tuple[list[dict], list[dict]]:
    """``(messages with the skills applied, the skills applied)``.

    The context goes on the **last user turn**, not into the system prompt.
    That is the legacy placement and it is the right one here: the system turn
    already states what the assistant is (a transcript reader, a document
    reader), and a skill is a modifier of *this* request rather than a change of
    role. It also keeps a resumed conversation honest - earlier turns keep the
    skills they were asked with.
    """
    cleaned, resolved = resolve(message, applied)
    if not resolved:
        return messages, []

    prepared = copy.deepcopy(messages)
    context = render(resolved)

    for index in range(len(prepared) - 1, -1, -1):
        if prepared[index].get("role") != "user":
            continue

        content = prepared[index].get("content")
        if isinstance(content, str):
            prepared[index]["content"] = (
                f"{context}\n\nUser request:\n{cleaned}" if cleaned else context
            )
        elif isinstance(content, list):
            # A multi-part turn (an image plus a question): the context joins
            # the text part rather than becoming one of its own, so a provider
            # that expects one text block per turn still gets one.
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    part["text"] = f"{context}\n\n{cleaned or part.get('text') or ''}"
                    break
            else:
                content.append({"type": "text", "text": f"{context}\n\n{cleaned}"})
        else:
            return messages, []

        return prepared, resolved

    return messages, []
